"""The `github` backend — specs as GitHub issues, over the `gh` CLI.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

from quenching.common.git import COMMAND_TIMEOUT_S, _git
from quenching.common.remote import github_slug, normalize_remote
from quenching.common.config import CONFIG_FILE, find_repo_root
from quenching.common.io import read_text, write_text
from quenching.common.leak import guard_store_write
from quenching.specs.backends.azure import azure_predecessor_ids
from quenching.specs.backends.base import (BackendRefusal, SpecBackend, parse_retry_after,
                                           read_only_refusal, transport_read_only)
from quenching.specs.backends.hybrid import (GH_BODY_MAX, GH_PART_MAX, hybrid_join,
                                             hybrid_project, hybrid_split, hybrid_title_join,
                                             hybrid_unwrap, hybrid_unwrap_part, hybrid_wrap,
                                             hybrid_wrap_part)
from quenching.specs import store
from quenching.specs.config import load_config
from quenching.specs.parse import (PHASES, carry_forward_fields, declared_tags,
                                   derive_info, derive_labels, reconcile_label_set,
                                   strip_frontmatter_keys)


# `gh`'s own exit code for "no host is authenticated", distinct from the 1 it uses for an
# API that answered with an error. Telling the two apart is the whole point of shelling out
# to `gh` instead of speaking HTTP: one is fixed by `gh auth login`, the other is not.
GH_NOT_AUTHENTICATED = 4
# OURS, never one of gh's: the binary is not on PATH, so no process ever started.
GH_MISSING = 127
GH_TIMEOUT = 124
GH_MAX_ATTEMPTS = 3
GH_LEAN_LIMIT = 1000
GH_RETRY_BACKOFF = (0.25, 0.5)
GH_CACHE_VERSION = 1
GH_CACHE_TTL_S = 300


normalize_github_remote = normalize_remote


def github_cache_path(remote: str) -> str:
    """Return the per-user cache path for one normalized GitHub remote."""
    normalized = normalize_github_remote(remote)
    key = hashlib.sha256(normalized.encode()).hexdigest()[:32]
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "quenching", "github", f"{key}.json")


def github_cache_read(remote: str) -> dict:
    """Read a fresh resolution cache entry, treating every malformed entry as a miss."""
    normalized = normalize_github_remote(remote)
    if not normalized:
        return {}
    try:
        value = json.loads(read_text(github_cache_path(normalized)) or "null")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict) or value.get("version") != GH_CACHE_VERSION \
            or value.get("remote") != normalized:
        return {}
    created = value.get("createdAt")
    if not isinstance(created, (int, float)) or time.time() - created > GH_CACHE_TTL_S:
        return {}
    if not isinstance(value.get("repo"), str) or not value["repo"].strip():
        return {}
    return value


def github_cache_write(remote: str, repo: str, open_issues: int | None) -> None:
    """Remember a resolution without making the cache authoritative or part of the repository."""
    normalized = normalize_github_remote(remote)
    if not normalized:
        return
    payload = {"version": GH_CACHE_VERSION, "remote": normalized, "repo": repo,
               "openIssues": open_issues, "createdAt": time.time()}
    path = github_cache_path(normalized)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text(path, json.dumps(payload))
    except OSError:
        pass


def github_cache_forget(remote: str) -> None:
    """Drop one remote's resolution entry so the next run re-reads the open-issue count."""
    normalized = normalize_github_remote(remote)
    if not normalized:
        return
    try:
        os.remove(github_cache_path(normalized))
    except OSError:
        pass


def _gh_transient(code: int, stdout: str, stderr: str) -> bool:
    """Whether a failed `gh` call may succeed if the same request is tried again."""
    if code == GH_TIMEOUT:
        return True
    text = f"{stdout}\n{stderr}".lower()
    return "rate limit" in text or bool(re.search(r"http\s+(?:429|500|502|503|504)\b", text))


_GH_WRITE_FLAGS = ("-f", "-F", "--field", "--raw-field", "--input")


def _gh_is_read(argv: tuple[str, ...]) -> bool:
    """Whether a `gh` call only reads, and is therefore safe to repeat.

    `gh api` is a GET unless `-X`/`--method` says otherwise OR a body/field flag is present
    (gh then defaults to POST). Of the porcelain verbs only `list` and `view` read; anything
    unrecognised counts as a write, because the guard that uses this must fail closed."""
    if not argv:
        return False
    if argv[0] == "api":
        method = "GET"
        for i, token in enumerate(argv):
            if token in ("-X", "--method") and i + 1 < len(argv):
                method = argv[i + 1].upper()
            elif token.startswith("--method="):
                method = token.split("=", 1)[1].upper()
            elif token in _GH_WRITE_FLAGS and method == "GET":
                method = "POST"
        return method == "GET"
    return len(argv) > 1 and argv[1] in ("list", "view")


def _gh_is_idempotent(argv: tuple[str, ...]) -> bool:
    """Whether repeating a `gh` call leaves GitHub in the same state as running it once.

    Reads, plus the whole-value writes this backend makes: `gh api -X PATCH|PUT|DELETE` and
    `gh issue edit`. A POST is never here: a timeout can fire after GitHub accepted it, and a
    second attempt would create a second issue or comment."""
    if _gh_is_read(argv):
        return True
    if argv[:1] == ("api",):
        for i, token in enumerate(argv):
            if token in ("-X", "--method") and i + 1 < len(argv):
                return argv[i + 1].upper() in ("PATCH", "PUT", "DELETE")
            if token.startswith("--method="):
                return token.split("=", 1)[1].upper() in ("PATCH", "PUT", "DELETE")
        return False
    return argv[:2] == ("issue", "edit")


def _gh_result(result) -> tuple[int, str, str, int]:
    """Normalize the historical three-field test doubles and the four-field transport result."""
    if len(result) == 4:
        code, stdout, stderr, attempts = result
        return code, stdout, stderr, attempts
    code, stdout, stderr = result
    return code, stdout, stderr, 1


def _gh_run(cwd: str, *argv: str, stdin: str | None = None) -> tuple[int, str, str, int]:
    """Exit code, stdout AND stderr of one `gh` command.

    A SIBLING of `_git_run`, deliberately not a reuse of it. The two binaries fail
    differently and the difference IS the contract here: `gh` answers "nobody is logged in"
    with its own exit 4 and a body-less stderr, and "GitHub said no" with exit 1 plus an
    `HTTP <code>` line, while the JSON explaining why goes to STDOUT. A runner that
    flattened those to "nonzero" could not tell a human to run `gh auth login` rather than
    to check the repository name.

    A missing binary comes back as `GH_MISSING` rather than as an exception, so the one
    failure a user is most likely to hit is an ordinary return value on the path every
    caller already handles — never a traceback out of a `subprocess` call.

    A cwd that does not exist is an error and NOT a fallback to `"."`, for the same reason
    `_git_run` refuses it: `gh repo view` reads the git remote of wherever it runs, so the
    fallback would resolve some other repository and then write specs into it.

    60s and not git's 30: this is a round trip to api.github.com, not a local object
    lookup, and a paginated listing of a busy repository legitimately takes seconds."""
    import subprocess
    reads = _gh_is_read(argv)
    if not reads and transport_read_only():
        raise read_only_refusal("gh", argv)
    if not os.path.isdir(cwd):
        return 1, "", f"not a directory: {cwd}"
    # ONLY IDEMPOTENT CALLS ARE REPEATED: reads and whole-value PATCH/PUT/DELETE. A timeout can
    # fire after GitHub accepted a POST, and a second attempt would then create a second issue
    # or comment; a failed POST is reported once and the caller decides what is safe to do next.
    max_attempts = GH_MAX_ATTEMPTS if _gh_is_idempotent(argv) else 1
    env = store.subprocess_env("github")
    for attempt in range(1, max_attempts + 1):
        try:
            out = subprocess.run(["gh", *argv], capture_output=True, text=True,
                                 timeout=COMMAND_TIMEOUT_S,
                                 cwd=cwd, input=stdin, env=env)
            code, stdout, stderr = out.returncode, out.stdout, out.stderr
        except FileNotFoundError as e:
            return GH_MISSING, "", str(e), attempt
        except subprocess.TimeoutExpired as e:
            detail = e.stderr or str(e) or "gh command timed out"
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            code, stdout, stderr = GH_TIMEOUT, "", str(detail)
        except (OSError, ValueError, subprocess.SubprocessError) as e:   # noqa: BLE001
            return 1, "", str(e), attempt
        if code == 0 or attempt == max_attempts or not _gh_transient(code, stdout, stderr):
            return code, stdout, stderr, attempt
        delay = parse_retry_after(stderr, stdout)
        time.sleep(GH_RETRY_BACKOFF[attempt - 1] if delay is None else delay)


def _gh_said(stdout: str, stderr: str) -> str:
    """The one line worth quoting back from a failed `gh` call.

    `gh api` SPLITS A FAILURE ACROSS BOTH STREAMS: stderr carries its own one-liner
    (`gh: Not Found (HTTP 404)`) and stdout carries GitHub's JSON body, whose `message` is
    the half that says why — "API rate limit exceeded for user ID 1" against a bare
    "HTTP 403". Quoting only stderr loses the reason; quoting only stdout loses the status
    code, and loses everything for the failures that never reach the API at all."""
    head = next((ln.strip() for ln in (stderr or "").splitlines() if ln.strip()), "")
    # gh prefixes its own one-liners with `gh: `, and every refusal built from this already
    # says "gh said" — kept, the message reads "gh said: gh: Not Found".
    head = head[4:].strip() if head.startswith("gh: ") else head
    detail = ""
    try:
        obj = json.loads(stdout or "")
        if isinstance(obj, dict) and isinstance(obj.get("message"), str):
            detail = obj["message"].strip()
    except (json.JSONDecodeError, TypeError, ValueError):
        detail = ""
    if detail and detail.lower() not in head.lower():
        return f"{head} — {detail}" if head else detail
    return head or "gh failed without saying why"


def gh_refusal(action: str, code: int, stdout: str, stderr: str, attempts: int = 1) -> dict:
    """Every way a `gh` call can fail, as an exit-2 refusal a human can act on.

    THREE OUTCOMES, THREE DIFFERENT REMEDIES, and separating them is the reason this
    backend is a subprocess and not an HTTP client: the binary is missing (install it),
    the binary is there but nobody is logged in (`gh auth login`), or GitHub itself said
    no (read what it said). Collapsing them into "github failed" would leave the first two
    looking like an outage and send a human hunting a network problem that is not there.

    Authentication is recognised by TWO signals, not one. `gh api` exits 4 when no host is
    configured at all, but a configured host holding a revoked or wrong token gets as far
    as the API and comes back as an ordinary exit 1 with `HTTP 401` — the same shape as a
    404, and the wrong remedy for it.

    Always exit 2, always a refusal and never a finding: nothing was read and nothing was
    written, which is a different statement from "the specs have a problem"."""
    if code == GH_MISSING:
        return {
            "code": "sp-gh-missing", "exit": 2, "action": action,
            "attempts": attempts,
            "message": "backend 'github' needs the `gh` CLI and it is not on PATH — install "
                       "GitHub CLI (https://cli.github.com), then run `gh auth login`; no "
                       "spec was read or written",
        }
    said = _gh_said(stdout, stderr)
    if code == GH_TIMEOUT:
        return {
            "code": "sp-gh-timeout", "exit": 2, "action": action, "attempts": attempts,
            "gh": said,
            "message": f"`gh` timed out while {action} after {attempts} attempt(s) — "
                       f"the GitHub request did not complete; gh said: {said}",
        }
    if code == GH_NOT_AUTHENTICATED or "HTTP 401" in (stderr or "") \
            or "gh auth login" in (stderr or ""):
        return {
            "code": "sp-gh-unauthenticated", "exit": 2, "action": action,
            "attempts": attempts, "gh": said,
            "message": f"`gh` is installed but not authenticated for this repository — run "
                       f"`gh auth login`; gh said: {said}",
        }
    return {
        "code": "sp-gh-api-error", "exit": 2, "action": action, "attempts": attempts,
        "gh": said, "ghExit": code,
        "message": f"github refused {action} — gh said: {said}",
    }


# The version the shapes below were measured against. It travels INSIDE the refusal message
# because the discriminant is a property of `gh`'s own `--paginate --slurp`, not of the API:
# a human reading the refusal on a future `gh` needs to know which version was asserted.
GH_MEASURED_VERSION = "gh 2.97.0 (2026-07-31)"

GH_EMPTY_LISTING_REMEDY = (
    "re-run the command — this is a transport fault, not a state; if it repeats, run "
    "`gh api --paginate --slurp \"repos/<owner>/<name>/issues?state=all&per_page=100\"` by "
    "hand and compare what it prints against the shapes above"
)


def empty_listing_refusal(action: str, pages, open_issues: int | None = None) -> dict | None:
    """The exit-2 refusal for a paginated listing that did not come back, or `None` when the
    payload has the shape a listing legitimately has or the available issue count cannot prove
    that the empty result is truncated.

    A BLANK RESULT IS A REFUSAL ONLY WHEN THE COUNT PROVES IT IS SUSPICIOUS. Measured on this
    repository:

    - `None` — `gh` exited 0 and printed nothing, which `_api`'s `json.loads(out or "null")`
      turns into a valid `null`. It is a response that never arrived, but without a positive
      issue count it cannot be distinguished from a repository with no specs yet.
    - `[]` — zero pages. A front that genuinely holds nothing answers `[[]]`, ONE page that
      is empty; zero pages is suspicious only when the repository has open issues.
    - `[[]]` — one empty page: a genuinely empty front, which passes. Suspicion about THAT
      state is a refusal only when the count is positive; a missing or zero count leaves the
      result as an honest empty front.

    THE CALLER ASKS, AND NEVER `_api`. `_api` is shared with the writes, and one of those is
    a DELETE whose legitimate GitHub answer is 204 No Content — `gh` prints nothing there and
    `null` is the RIGHT answer. Only the caller knows which shape it asked for. It is the
    same line `az_refusal` already draws for its own transport, and this is the sibling the
    `gh` side was missing. The caller supplies the count because only the resolved repository
    knows whether an empty result is contradicted by open issues."""
    if isinstance(pages, list) and any(page for page in pages):
        return None
    if open_issues is None or open_issues <= 0:
        return None
    observed = "no output at all (`gh` exited 0 and printed nothing)" if pages is None \
        else f"`{json.dumps(pages)}`"
    return {
        "code": "sp-gh-empty-listing", "exit": 2, "action": action, "observed": observed,
        "openIssues": open_issues,
        "message": f"`gh api` exited 0 while {action} but returned {observed} — a front that "
                   f"legitimately holds nothing answers with ONE empty page (`[[]]`), never "
                   f"with zero pages, so this response was cut short and is not an empty "
                   f"front (measured on {GH_MEASURED_VERSION}); nothing was read",
        "remedy": GH_EMPTY_LISTING_REMEDY,
    }


def short_listing_refusal(action: str, pages, open_issues: int | None) -> dict | None:
    """The exit-2 refusal for a full listing that holds FEWER open issues than the repository
    reports open — proof of truncation, where zero specs is only suspicion.

    The REST listing returns every issue, PRs included; `open_issues` (`issues.totalCount`)
    counts open issues only and excludes PRs. Open non-PR issues seen below that count means
    pages went missing. The count is cached up to `GH_CACHE_TTL_S`, so a concurrent close can
    make the listing legitimately shorter; the caller drops the cached count on refusal and
    the remedy is one re-run."""
    if open_issues is None or open_issues <= 0 or not isinstance(pages, list):
        return None
    seen = sum(1 for page in pages for issue in (page or [])
               if isinstance(issue, dict) and "pull_request" not in issue
               and issue.get("state") == "open")
    if seen >= open_issues:
        return None
    return {
        "code": "sp-gh-short-listing", "exit": 2, "action": action, "observed": seen,
        "openIssues": open_issues,
        "message": f"`gh api` exited 0 while {action} but listed {seen} open issue(s) where "
                   f"the repository reports {open_issues}; the listing was cut short, so "
                   f"nothing was derived from it",
        "remedy": GH_EMPTY_LISTING_REMEDY,
    }


def lean_limit_refusal(action: str, observed: int, attempts: int = 1) -> dict:
    """Refuse a lean result that reached `gh issue list`'s hard return ceiling."""
    return {
        "code": "sp-gh-lean-truncated", "exit": 2, "action": action,
        "limit": GH_LEAN_LIMIT, "observed": observed, "attempts": attempts,
        "message": f"`gh issue list` returned the limit of {GH_LEAN_LIMIT} rows while "
                   f"{action}; the lean index may be truncated, so no complete listing "
                   "can be derived — paginate below the limit or use the full listing",
    }


def stale_write_refusal(number: int, expected: str, actual: str | None) -> dict:
    """Refuse a write whose issue changed after the caller read it."""
    return {
        "code": "sp-gh-stale-write", "exit": 2, "issue": number,
        "expectedUpdatedAt": expected, "actualUpdatedAt": actual,
        "message": f"GitHub issue #{number} changed after it was read — expected updated_at "
                   f"{expected!r}, received {actual!r}; re-read the spec and reapply the "
                   "change instead of merging two documents automatically",
        "remedy": "re-read the spec and reapply the change",
    }


GH_LISTING_SUSPECT_REMEDY = (
    "re-run the command — a listing that comes back the same way twice is the front's real "
    "state, and one that does not was a transport fault"
)


def listing_is_suspect(rows: int, open_issues: int | None) -> bool:
    """Zero specs read out of a repository that does have issues open — SUSPICION, never
    proof, which is why nothing built on this predicate ever refuses.

    A repository that adopted this backend over an existing issue tracker and has not
    created its first spec yet IS this state, exactly, and is legitimate. Narrowing the
    trigger cannot separate the two, because they are the same observation; what separates
    them is the wording saying the number out loud and letting a human recognise which one
    they are in. `None` — the count could not be learned, because the name came from the git
    remote — corroborates nothing and is never read as zero."""
    return rows == 0 and open_issues is not None and open_issues > 0


def listing_suspect_message(repo: str, open_issues: int) -> str:
    """The ONE wording of that suspicion, written here beside the predicate: the `stderr`
    line at the moment of the read cites it, and so does `doctor`'s own finding. A second
    call site that composed its own sentence would drift from this one the day either was
    edited alone."""
    return (f"{repo} has {open_issues} open issue(s) and not one of them carries a spec "
            f"marker, so the specs front reads as empty — if no spec has been created here "
            f"yet, this is exactly that state and it is expected; if specs do exist, this "
            f"listing did not come back whole and nothing derived from it can be trusted")


# Keyed by repository and not by a bare flag: one process can legitimately open this backend
# more than once (`doctor` opens its own), and the second one may be a different repository
# with its own answer to give.
_LISTING_SUSPECT_ANNOUNCED: set[str] = set()


def announce_listing_suspect(repo: str, open_issues: int | None) -> None:
    """One line on stderr, once per process, at the read that could have lost something.

    THE READ AND NOT THE WRITE, which is where `announce_unproved`'s own gloss puts the
    line — and the exception is stated rather than assumed. That gloss reasons that "a read
    of an unproven backend loses nothing — it returns wrong data or a refusal, and both are
    visible immediately". Here the wrong data is PRECISELY what is not visible: an empty
    front reads as a fact. The rule underneath is unchanged — the line lands where the loss
    would happen — and here that is the read.

    stderr and never stdout: every caller branches on the `--json` payload, and a warning
    printed into it would break the parse it exists to inform."""
    if repo in _LISTING_SUSPECT_ANNOUNCED:
        return
    _LISTING_SUSPECT_ANNOUNCED.add(repo)
    if open_issues is None:
        # The lean index is a SEARCH, which is eventually consistent, and the open-issue count
        # could not be learned (the name came from the git remote): nothing corroborates the
        # emptiness either way, so the line says exactly that and never claims a fault.
        message = (f"{repo}: the lean listing came back with no specs, and it reads GitHub's "
                   f"search index, which is eventually consistent; the open-issue count is "
                   f"unknown, so this empty front is unproven")
    else:
        message = listing_suspect_message(repo, open_issues)
    print(f"warning: {message}; {GH_LISTING_SUSPECT_REMEDY}", file=sys.stderr)


def resolve_github_repo(cwd: str) -> tuple[str, int | None, dict]:
    """`owner/name` for the repository this checkout points at, how many issues it has open,
    and a refusal — the count being `None` wherever it could not be learned for free.

    ASK `gh` FIRST, because it answers the question the API will actually be called with:
    it resolves the remote gh itself would use, honours the `gh repo set-default` a human
    set for a fork, and surfaces the auth failure at RESOLUTION time rather than three
    calls later in the middle of a write.

    The git remote is the fallback and not the primary for exactly that reason — it is a
    string, not an answer: on a fork, `origin` names the fork rather than the repository
    the issues live in. It is kept because a checkout whose gh-to-git integration fails
    (no git on PATH, a checkout gh cannot attribute) still has a legible answer, and
    refusing there would be refusing over a detail of how gh finds the remote.

    THE OPEN-ISSUE COUNT RIDES ALONG ON THIS CALL and costs nothing: `issues` is another
    field of the `--json` this call already makes, so asking for it opens no round trip.
    Measured on holetz/claude-quenching with gh 2.97.0: `issues.totalCount` is 38, the same
    number `gh issue list --state open` reports, and it counts issues only — GitHub's
    `issues` connection excludes pull requests. It is the corroboration that lets a listing
    of zero specs be told apart from a repository that has no issues at all; the `--jq` this
    call used to carry is gone because two fields need the object, not one scalar.

    Never guesses a repository it cannot name. A wrong answer here does not fail — it
    silently reads and writes somebody else's issues."""
    declared, refusal = store.declared_store(cwd)
    if refusal:
        return "", None, refusal
    if "github" in declared:
        return declared["github"]["repository"], None, {}
    remote = _git(cwd, "remote", "get-url", "origin").strip()
    normalized_remote = normalize_github_remote(remote)
    cached = github_cache_read(normalized_remote)
    if cached:
        return cached["repo"], cached.get("openIssues"), {}

    code, out, err, attempts = _gh_result(
        _gh_run(cwd, "repo", "view", "--json", "nameWithOwner,issues"))
    if code == 0 and out.strip():
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            parsed = None
        # gh exited 0 with something other than the object it was asked for. Falling through
        # to the remote is the same degradation this function already applies to every other
        # way gh fails to answer — the repository name is what has to survive.
        view = parsed if isinstance(parsed, dict) else {}
        name = str(view.get("nameWithOwner") or "").strip()
        if name:
            issues = view.get("issues")
            open_issues = issues.get("totalCount") if isinstance(issues, dict) else None
            github_cache_write(normalized_remote, name, open_issues)
            return name, open_issues, {}
    # The two failures the remote cannot repair — no binary, nobody logged in — refuse here
    # with their own remedy instead of degrading into "could not resolve the repository".
    if code in (GH_MISSING, GH_NOT_AUTHENTICATED) or "gh auth login" in (err or ""):
        return "", None, gh_refusal("resolving the repository", code, out, err, attempts)
    url = remote
    name = github_slug(url)
    if name:
        # The remote answers the name and nothing else: no count, and never a guess at one.
        github_cache_write(normalized_remote, name, None)
        return name, None, {}
    return "", None, {
        "code": "sp-gh-repo-unresolved", "exit": 2, "remote": url or None,
        "gh": _gh_said(out, err),
        "message": f"backend 'github' could not tell which repository holds the specs — "
                   f"`gh repo view` failed ({_gh_said(out, err)}) and origin "
                   f"({url or 'absent'}) is not a github.com remote; add one, or pick the "
                   f"repository with `gh repo set-default`",
    }


# Color and one-line description for each `spec:` label — GitHub-only, never in
# schema.json. `label:` there is the name every backend with a native tag concept can use;
# color is a GitHub label's own visual property with no Azure Boards tag equivalent, so it
# stays where the one backend that renders it lives. `_store`'s issue PATCH already creates
# a label missing from the repo (Design item 7 of this spec), with an arbitrary color —
# `_ensure_label_colors` is the one-time follow-up that fixes it.
SPEC_LABEL_META = {
    "spec:ranked":       ("c2e0c6", "priority is recorded — a human ranked this spec"),
    "spec:interrogated": ("bfd4f2", "refined is recorded — a real interrogation happened"),
    "spec:approved":     ("0e8a16", "approved is recorded — the record's `by:` says whose word it was"),
    "spec:built":        ("5319e7", "the derived stage — task or Handoff work is under way"),
    "spec:reviewed":     ("fbca04", "a human read the whole branch diff"),
    "spec:merged":       ("1d76db", "the branch merged into the integration branch"),
}


# -- native issue dependencies ------------------------------------------------- #
# `dependsOn` is carried as GitHub's "blocked by" relation (GA August 2025), over the REST
# endpoints `repos/{repo}/issues/{n}/dependencies/blocked_by` (GET lists, POST adds with the
# blocking issue's REST `id`, DELETE `.../{id}` removes). The ids that count are the ones the
# Azure backend already keeps: numeric, never the spec's own, declaration order, deduplicated.
GH_DEPENDENCY_LIMIT = 50


def github_blocked_by(listing, repo: str | None = None) -> dict[int, int]:
    """`{issue number: REST id}` of the issues a `blocked_by` listing names. With `repo`, an
    issue whose `repository_url` names another repository is left out: its number is not a
    spec of this store, and it is a relation this backend never touches."""
    pages = listing if listing and isinstance(listing[0], list) else [listing or []]
    return {int(issue["number"]): int(issue["id"])
            for page in pages for issue in (page or [])
            if isinstance(issue, dict) and issue.get("number") and issue.get("id")
            and not (repo and issue.get("repository_url")
                     and not str(issue["repository_url"]).endswith(f"/repos/{repo}"))}


def github_dependency_diff(current, wanted: list[int]) -> tuple[list[int], list[int]]:
    """`(numbers to add, numbers to remove)` that make the blocked-by set equal `wanted`,
    diffed by issue number: an unchanged set is two empty lists."""
    return ([n for n in wanted if n not in current],
            sorted(n for n in current if n not in wanted))


def github_dependency_limit_refusal(number: int | None, wanted: list[int]) -> dict | None:
    """The refusal for a spec waiting on more issues than one relation type holds."""
    if len(wanted) <= GH_DEPENDENCY_LIMIT:
        return None
    target = f"issue #{number}" if number else "a new issue"
    return {"code": "sp-gh-dependency-limit", "exit": 2, "count": len(wanted),
            "max": GH_DEPENDENCY_LIMIT,
            "message": f"`dependsOn` names {len(wanted)} issues and GitHub holds at most "
                       f"{GH_DEPENDENCY_LIMIT} blocked-by relations on {target}; nothing was "
                       "written"}


class GitHubBackend(SpecBackend):
    """Specs as GitHub issues, reached through `gh api` in a subprocess.

    ONE ISSUE IS ONE SPEC, and the phase is the issue's own state: open is `plans`, closed
    is `archive`. That mapping is not a shortcut — it is the same fact told once. A label
    would be a second declaration of a phase the tracker already knows, and the two would
    diverge the first time somebody closed an issue from the web UI.

    ONE ISSUE BODY IS THE WHOLE DOCUMENT. There are no sub-issues: the retirement and what
    measured it are recorded at HYBRID SERIALISATION above. A document past GitHub's body
    ceiling spills into continuation comments on its own issue, which is the only reason this
    class ever makes a second call for one spec.

    It derives NOTHING. `resolve_one` picks the spec, `derive_info` produces the stages,
    gates, records and tasks, exactly as they are produced for a file on disk; `parse_tasks`
    is the ONLY thing that ever decides a task is checked or blocked. This class turns issues
    into the canonical document and back and does no more than that.

    THE SEVEN FRONTMATTER RECORDS STAY IN THE BODY, none of them a label — every FIELD a
    record carries, that is. Multi-field records (`priority`, `branch`, `merge`, `refined`)
    have no honest single-string label form — encoding `{level, criticality, complexity,
    date}` into a label name would invent a second format only a new parser could read
    back, which is the backend deriving its own encoding exactly where the interface
    forbids it. Keeping them in the body costs the records being invisible in the issue
    list without opening the issue — that gap is what the `spec:` labels below close.

    A `spec:` LABEL PER PRESENT RECORD, PLUS `spec:built` FOR THE DERIVED `executing`
    STAGE, rides inside the same PATCH `_store` already makes — never a second call, never
    read back. This is rendering derived state onto the tracker's own UI, not a second
    encoding of a record's fields: `derive_labels` computes the desired set from `info`
    alone, and `reconcile_label_set` folds it against whatever the issue already carries so
    a human's own label (never under the `spec:` prefix) is untouched. See
    /docs/standards/architecture/spec-backend.md for the category this is, and why it is not
    the sub-issue projection that was retired.

    The listing is fetched once per process and cached, which is a local cache and NOT a
    store: it is not authoritative, nothing outside this object reads it, and every write
    drops it. It exists because the CLI asks for the listing more than once per command,
    and each ask is a network round trip. Since the listing already carries every body,
    `read_spec` costs NOTHING beyond it for a one-part spec — which is every spec but the
    largest two this repository holds."""

    name = "github"

    def __init__(self, repo: str, cwd: str, types: dict[str, str] | None = None,
                 open_issues: int | None = None) -> None:
        self.repo = repo
        self.cwd = cwd
        # How many issues the repository has open, learned for free by the same
        # `gh repo view --json` that resolved the name — `None` when the name came from the
        # git remote instead, which answers no such thing. It is never a second call: a
        # corroboration that cost a round trip would be paid on every command to say
        # something only the empty listing ever needs.
        self.open_issues = open_issues
        # `workItemTypes.<key>.github`, pre-filtered to the entries this backend can name —
        # never the whole catalogue entry, since `create_spec` needs only the native name.
        self.types = types or {}
        # descriptor, issue number, first chunk, how many parts the marker declares,
        # issue title, and the issue's own labels as of this listing — what the label
        # reconciliation in `_store` diffs against, so it costs no call of its own
        self._rows: list[tuple[dict, int, str, int, str, dict, list[str], str | None]] | None = None
        # `spec:` labels already confirmed correctly colored THIS process — so a repo
        # this tool has been reconciling against for a while pays no repeat GET+PATCH for
        # a label it fixed on an earlier write in the same command.
        self._colors_confirmed: set[str] = set()

    # -- transport ---------------------------------------------------------- #
    def _api(self, action: str, *argv: str, stdin: str | None = None):
        """One `gh api` call, parsed. Raises `BackendRefusal` for every way it can fail."""
        code, out, err, attempts = _gh_result(
            _gh_run(self.cwd, "api", *argv, stdin=stdin))
        if code != 0:
            raise BackendRefusal(gh_refusal(action, code, out, err, attempts))
        try:
            return json.loads(out or "null")
        except json.JSONDecodeError as e:
            # Not an API error — gh exited 0 and handed back something unparseable. Named
            # separately so it can never be read as "GitHub said no".
            raise BackendRefusal({
                "code": "sp-gh-bad-response", "exit": 2, "action": action,
                "attempts": attempts,
                "message": f"`gh api` exited 0 while {action} but its output is not JSON: {e}",
            }) from e

    def _write_api(self, action: str, method: str, path: str, payload: dict):
        """A mutating call, with the payload on STDIN rather than in argv.

        `--input -` and not `-f body=…`: a spec document is kilobytes of markdown with
        newlines, quotes and backticks in it, and every one of those is a way for argv
        quoting to corrupt what lands in the issue.

        THE BODY CEILING IS CHECKED HERE, at the one point every write passes through,
        rather than at each caller — a check per caller is the shape that leaves one out.
        Refusing beats letting GitHub answer 422: in the middle of a migration of dozens of
        specs a 422 is a validation error with no spec's name on it, while this names the
        measured size and the ceiling and emits no call at all.

        IT IS A BACKSTOP AND NO LONGER THE POLICY. Every document write goes through
        `hybrid_split`, which cuts to `GH_PART_MAX` before a payload is ever built, so this
        refusal cannot fire for a spec's own text. It stays because it guards the one point
        every write passes through, and a future caller that builds a body some other way
        must still be unable to hand GitHub something it will answer 422 to."""
        body = payload.get("body")
        if isinstance(body, str) and len(body) > GH_BODY_MAX:
            raise BackendRefusal({
                "code": "sp-gh-body-too-large", "exit": 2, "action": action,
                "size": len(body), "max": GH_BODY_MAX,
                "message": f"the document is {len(body)} characters and a GitHub issue body "
                           f"holds {GH_BODY_MAX} — shorten a section while {action}; nothing "
                           f"was written",
            })
        return self._api(action, "-X", method, path, "--input", "-",
                         stdin=json.dumps(payload))

    # -- the listing, fetched once ------------------------------------------- #
    def _load(self) -> list[tuple[dict, int, str, int, str, dict, list[str], str | None]]:
        if self._rows is not None:
            return self._rows
        # `--slurp` because `--paginate` alone concatenates one JSON array per page, which
        # is not a JSON document. state=all: `archive` is the closed half of the tracker,
        # so a default (open-only) listing would report every archived spec as missing.
        action = "listing the repository's issues"
        pages = self._api(action, "--paginate", "--slurp",
                          f"repos/{self.repo}/issues?state=all&per_page=100")
        # THE SHAPE IS ASSERTED HERE and nowhere else: this is the one caller that knows it
        # asked for a list of pages, and the whole front is derived from what it returns —
        # a listing that silently comes back empty reports every spec in the repository as
        # missing, with `ok: true` and exit 0.
        refusal = empty_listing_refusal(action, pages, self.open_issues)
        if refusal:
            raise BackendRefusal(refusal)
        short = short_listing_refusal(action, pages, self.open_issues)
        if short:
            # The count may be the stale half: forget it so the re-run reads a fresh one.
            github_cache_forget(_git(self.cwd, "remote", "get-url", "origin").strip())
            raise BackendRefusal(short)
        rows: list[tuple[dict, int, str, int, str, dict, list[str], str | None]] = []
        for page in pages:
            for issue in (page or []):
                if not isinstance(issue, dict) or "pull_request" in issue:
                    # GitHub models a pull request AS an issue, so `/issues` answers with
                    # both. A PR can never be a spec, and one that happened to carry the
                    # marker would otherwise be listed and then written over.
                    continue
                head, parts = hybrid_unwrap(issue.get("body") or "")
                if not parts:
                    # The marker, not its optional legacy payload, distinguishes specs from
                    # ordinary issues.
                    continue
                phase = "archive" if issue.get("state") == "closed" else "plans"
                number = int(issue.get("number") or 0)
                rows.append(({
                    "id": number, "phase": phase, "folder": phase, "legacy": False,
                    "path": issue.get("html_url")
                            or f"https://github.com/{self.repo}/issues/{number}",
                }, number, head, parts, str(issue.get("title") or ""), self._native_fields(issue),
                    self._issue_labels(issue), issue.get("updated_at")))
        if listing_is_suspect(len(rows), self.open_issues):
            announce_listing_suspect(self.repo, self.open_issues)
        self._rows = rows
        return rows

    def _invalidate(self) -> None:
        self._rows = None

    def _issue_labels(self, issue: dict) -> list[str]:
        """Every label name on `issue`, RAW — the `spec:` rendering included. Only the write
        path wants them this way; readers want `declared_tags` over the result."""
        return [str(lbl.get("name")) for lbl in (issue.get("labels") or [])
                if isinstance(lbl, dict) and lbl.get("name")]

    def _lean_rows(self) -> list[dict]:
        """The provider's cheap spec index: title, state and the visible `spec:` labels.

        `gh issue list` searches the body marker server-side, so the document body never
        crosses the wire. It is intentionally separate from `_load`: a caller asking for the
        complete front still needs the canonical document and its native fields."""
        action = "listing specs through GitHub's lean index"
        code, out, err, attempts = _gh_result(_gh_run(
            self.cwd, "issue", "list", "--repo", self.repo, "--state", "all", "--search",
            '"quenching-spec" in:body', "--json", "number,title,state,labels", "--limit",
            str(GH_LEAN_LIMIT)))
        if code != 0:
            raise BackendRefusal(gh_refusal(action, code, out, err, attempts))
        try:
            issues = json.loads(out or "null")
        except json.JSONDecodeError as e:
            raise BackendRefusal({
                "code": "sp-gh-bad-response", "exit": 2, "action": action,
                "attempts": attempts,
                "message": f"`gh` exited 0 while {action} but its output is not JSON: {e}",
            }) from e
        if not isinstance(issues, list):
            raise BackendRefusal({
                "code": "sp-gh-bad-response", "exit": 2, "action": action,
                "attempts": attempts,
                "message": f"`gh` returned {type(issues).__name__}, not a list, for {action}",
            })
        refusal = empty_listing_refusal(action, issues, self.open_issues)
        if refusal:
            raise BackendRefusal(refusal)
        if len(issues) >= GH_LEAN_LIMIT:
            raise BackendRefusal(lean_limit_refusal(action, len(issues), attempts))
        rows: list[dict] = []
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            number = int(issue.get("number") or 0)
            if not number:
                continue
            state = str(issue.get("state") or "open").strip().lower()
            phase = "archive" if state == "closed" else "plans"
            rows.append({
                "id": number, "title": str(issue.get("title") or ""), "state": state,
                "records": [label for label in self._issue_labels(issue)
                            if label.startswith("spec:")],
                "phase": phase, "folder": phase, "legacy": False,
                "path": f"https://github.com/{self.repo}/issues/{number}",
                # The rows come from a search index, not the issues REST resource: a write
                # made moments ago may not be visible yet.
                "consistency": "eventual",
            })
        if not rows and self.open_issues != 0:
            # NEVER an empty front as a quiet success. With a positive count it is the known
            # suspect state; with an unknown count it is merely unproven; both say so.
            announce_listing_suspect(self.repo, self.open_issues)
        return rows

    def _native_fields(self, issue: dict) -> dict:
        """`tags`/`assignee`, reassembled from `labels`/`assignees` — the READ half of
        `## Design` §Stored is not projected. GitHub allows several assignees; the
        canonical field is singular, so only the first is reflected — the same restriction
        the schema already puts on every backend. `start`/`target` have no honest native
        counterpart here (no scheduling fields on an issue) and stay in the document,
        exactly as `date:` already does.

        EVERY `spec:` LABEL IS EXCLUDED. That surface carries two mechanisms — this storage
        and the rendering of derived state `derive_labels` computes — and the reserved prefix
        is what keeps them from reading each other's writes as the spec's own declared
        content. Reassembling one would make the very next write reaffirm a rendered label as
        a tag the document claims."""
        out: dict = {}
        labels = declared_tags(self._issue_labels(issue))
        if labels:
            out["tags"] = labels
        assignees = [a.get("login") for a in (issue.get("assignees") or [])
                    if isinstance(a, dict) and a.get("login")]
        if assignees:
            out["assignee"] = assignees[0]
        return out

    # -- the five primitives -------------------------------------------------- #
    def list_specs(self, phase: str | None = None, lean: bool = False) -> list[dict]:
        if lean:
            rows = [row for row in self._lean_rows()
                    if phase is None or row["phase"] == phase]
            return sorted(rows, key=lambda r: (PHASES.index(r["phase"]), r["id"]))
        rows = [dict(d) for d, _, _, _, _, _, _, _ in self._load()
                if phase is None or d["phase"] == phase]
        return sorted(rows, key=lambda r: (PHASES.index(r["phase"]), r["id"]))

    def read_spec(self, spec_id: str | int) -> tuple[dict | None, dict]:
        """One spec, by the number GitHub allocated.

        TWO PATHS TO THE SAME `info`, and which one runs is decided by what the process
        already paid for, never by the caller:

        - **The cache is cold** — the normal case, and what the native ID is FOR. One
          `GET /issues/<n>` fetches the single item: measured 0.4 s / 7.7 KB against a
          repository of 157 specs, where finding the same document by a slug buried in every
          body cost 3.8 s and 5.25 MB.
        - **The cache is warm** — `list_specs` already paginated the tracker, so every
          document is in hand. Going back to the wire for one of them is a request that
          buys nothing: `cq specs list --json` reads all 157 and, measured before this
          branch, that turned 4.8 s into 78 s of single-item GETs.

        Neither path derives anything of its own — both hand the same canonical document to
        `derive_info`, which is what `spec-backend.md` §The interface is the document
        requires. A row from a listing this process just made and a row fetched now are the
        same fact; nothing here re-reads the wire to confirm it."""
        try:
            number = int(spec_id)
        except (TypeError, ValueError):
            return None, {"code": "sp-unknown-id", "exit": 1, "id": spec_id,
                          "message": f"no spec with id '{spec_id}'"}
        if self._rows is not None:
            row = next((r for r in self._rows if r[1] == number), None)
            if row is None:
                return None, {"code": "sp-unknown-id", "exit": 1, "id": spec_id,
                              "message": f"no spec with id '{spec_id}'"}
            descriptor, _, head, parts, title, native_fields, labels, updated_at = row
            return self._assemble(dict(descriptor), number, head, parts, title,
                                  native_fields, labels, updated_at), {}
        issue = self._api(f"reading issue #{number}", f"repos/{self.repo}/issues/{number}")
        head, parts = hybrid_unwrap((issue or {}).get("body") or "")
        if not parts:
            return None, {"code": "sp-unknown-id", "exit": 1, "id": spec_id,
                          "message": f"no spec with id '{spec_id}'"}
        phase = "archive" if issue.get("state") == "closed" else "plans"
        spec = {
            "id": number, "phase": phase, "folder": phase, "legacy": False,
            "path": issue.get("html_url")
                    or f"https://github.com/{self.repo}/issues/{number}",
        }
        return self._assemble(spec, number, head, parts, str(issue.get("title") or ""),
                              self._native_fields(issue), self._issue_labels(issue),
                              issue.get("updated_at")), {}

    def _assemble(self, spec: dict, number: int, head: str, parts: int, native_title: str,
                  native_fields: dict, labels: list[str], updated_at: str | None = None) -> dict:
        """The one derivation both read paths go through, so neither can drift from the
        other about what a spec's `info` holds."""
        full_text = head if parts <= 1 else self._joined(number, head, parts)
        info = derive_info(spec, hybrid_title_join(full_text, native_title))
        info["frontmatter"].update(native_fields)
        info["_github_parts"] = parts
        info["_github_labels"] = labels
        if updated_at:
            info["_github_updated_at"] = updated_at
        return info

    # `start`/`target` have no native counterpart on an issue (no scheduling fields) and
    # stay in the document, exactly as `date:` already does — only these two are stored.
    GH_STORED_KEYS = ("tags", "assignee")

    def write_spec(self, info: dict, text: str) -> None:
        text = guard_store_write(text, self.cwd, info.get("text"), BackendRefusal)
        number = int(info["id"])
        # The dependency cap refuses BEFORE any request, the revision check's GET included.
        wanted = azure_predecessor_ids(derive_info(info, text)["frontmatter"], number)
        deps_changed = wanted != azure_predecessor_ids(info["frontmatter"], number)
        if deps_changed:
            refusal = github_dependency_limit_refusal(number, wanted)
            if refusal:
                raise BackendRefusal(refusal)
        expected_updated_at = info.get("_github_updated_at")
        if expected_updated_at:
            current = self._api(f"checking issue #{number} before writing",
                                f"repos/{self.repo}/issues/{number}")
            actual_updated_at = current.get("updated_at") if isinstance(current, dict) else None
            if actual_updated_at != expected_updated_at:
                raise BackendRefusal(stale_write_refusal(
                    number, expected_updated_at, actual_updated_at))
        had_parts = int(info.get("_github_parts", 1))
        current_labels = info.get("_github_labels", [])
        # `derive_info` — the SAME shared derivation every read goes through — off the text
        # about to be written, never a second, backend-own way of deciding the stage: this
        # class derives nothing of its own, per its own docstring above. It is also FRESH,
        # which the stored fields need for a second reason: every caller hands `write_spec`
        # the OLD `info` beside the NEW `text`.
        new_info = derive_info(info, text)
        # CARRIED FORWARD where this write's own text is silent — `_store` strips `tags`/
        # `assignee` from the document, so an ordinary write (a section edit, a ticked task)
        # hands text that never mentions them at all; reading that absence as "clear them"
        # would wipe both on the next unrelated write.
        native_fields = carry_forward_fields(info["frontmatter"], new_info["frontmatter"],
                                             self.GH_STORED_KEYS)
        # ONE SURFACE, TWO MECHANISMS, joined here and nowhere else: the spec's own declared
        # tags are STORAGE, the `spec:` set is a RENDERING recomputed on every write. The
        # reserved prefix keeps them disjoint — `reconcile_label_set` replaces the rendered
        # half wholesale and never touches a name outside it, which is exactly what the
        # stored tags are.
        desired = derive_labels(new_info)
        labels = reconcile_label_set(declared_tags(native_fields.get("tags") or []), desired)
        # DEPENDENCIES BEFORE THE BODY, and only when `dependsOn` changed: GitHub carries the
        # two in separate requests, and this order makes a retry converge — a failed link
        # leaves the old `dependsOn` in the body, so the retry still sees the change and
        # re-diffs against the live relations.
        if deps_changed:
            self._sync_dependencies(number, wanted)
        self._store(number, text, had_parts, labels, native_fields)
        # AFTER `_store`: a label `_store`'s own PATCH just created does not exist yet
        # before that call, and fixing a nonexistent label's color 404s. Gated on an
        # actual SET change — `set(...)`, not `!=` on the lists — so the no-milestone-
        # change write this backend must cost no round trip for (task 5.2) never reaches
        # this branch at all. GitHub returns a listing's labels alphabetically, never in
        # the schema order `reconcile_label_set` builds `labels` in, so comparing the
        # lists as ORDERED sequences read a same-set reorder as a change every time —
        # measured live against issue #877 on 2026-08-05, before this line read `set(...)`.
        if set(labels) != set(current_labels):
            self._ensure_label_colors(desired)
        self._invalidate()

    def _store(self, number: int, text: str, had_parts: int,
               labels: list[str] | None = None,
               native_fields: dict | None = None) -> None:
        """The whole write, given an issue number already in hand.

        `labels`/`assignees`, when given, ride in this SAME PATCH — one call updates title,
        body, the spec's own stored tags and the tracker's `spec:` rendering together, never
        a second round trip.

        A label GitHub does not already have fails the WHOLE request atomically (measured
        against this repository: `not found`, nothing written), which is the write-refusal
        `## Open Decisions` settles for §3.4: this tool never creates a label to make a
        write of the SPEC'S OWN tags succeed. `_ensure_label_colors` is not an exception to
        that — it grooms the `spec:` set this tool renders and owns, never a name the
        document declared."""
        stripped = strip_frontmatter_keys(text, self.GH_STORED_KEYS)
        stored, title = hybrid_project(stripped)
        chunks = hybrid_split(stored, GH_PART_MAX)
        payload = {"title": title,
                   "body": hybrid_wrap(chunks[0][0], len(chunks))}
        if labels is not None:
            payload["labels"] = labels
        if native_fields is not None and "assignee" in native_fields:
            payload["assignees"] = [native_fields["assignee"]] \
                if native_fields["assignee"] else []
        self._write_api(f"updating issue #{number}", "PATCH",
                        f"repos/{self.repo}/issues/{number}", payload)
        self._sync_parts(number, chunks, had_parts)

    def _ensure_label_colors(self, names: list[str]) -> None:
        """Fix color and description for whichever of `names` are not already confirmed
        correct this process — called only from `write_spec`, and only when a write is
        about to change an issue's labels, so a no-milestone-change write never reaches
        here at all.

        One GET of the repo's own label registry, then a PATCH per label that still
        disagrees — never more than once per label per process, per `_colors_confirmed`."""
        todo = [n for n in names if n in SPEC_LABEL_META and n not in self._colors_confirmed]
        if not todo:
            return
        existing = {l.get("name"): (l.get("color"), l.get("description"))
                   for page in (self._api("listing labels", "--paginate", "--slurp",
                                          f"repos/{self.repo}/labels?per_page=100") or [])
                   for l in (page or [])}
        for name in todo:
            color, desc = SPEC_LABEL_META[name]
            if existing.get(name) != (color, desc):
                self._write_api(f"fixing color for label {name!r}", "PATCH",
                                f"repos/{self.repo}/labels/{name}",
                                {"color": color, "description": desc})
            self._colors_confirmed.add(name)

    def create_spec(self, phase: str, text: str) -> str:
        text = guard_store_write(text, self.cwd, None, BackendRefusal)
        fresh = derive_info({"phase": phase}, text)
        refusal = github_dependency_limit_refusal(
            None, azure_predecessor_ids(fresh["frontmatter"]))
        if refusal:
            raise BackendRefusal(refusal)
        stripped = strip_frontmatter_keys(text, self.GH_STORED_KEYS)
        stored, title = hybrid_project(stripped)
        chunks = hybrid_split(stored, GH_PART_MAX)
        payload = {"title": title,
                  "body": hybrid_wrap(chunks[0][0], len(chunks))}
        # The same union `write_spec` makes, from an empty current set: the spec's declared
        # tags plus whatever the fresh document already renders. A capture form renders
        # nothing (no records, `captured` stage), so this is usually just the tags — but
        # deriving it rather than assuming that keeps the two paths one rule.
        labels = reconcile_label_set(declared_tags(fresh["frontmatter"].get("tags") or []),
                                     derive_labels(fresh))
        if labels:
            payload["labels"] = labels
        if fresh["frontmatter"].get("assignee"):
            payload["assignees"] = [fresh["frontmatter"]["assignee"]]
        issue = self._write_api("creating an issue", "POST", f"repos/{self.repo}/issues",
                                payload)
        number = int((issue or {}).get("number") or 0)
        url = (issue or {}).get("html_url") or f"https://github.com/{self.repo}/issues/{number}"
        try:
            return self._finish_create(phase, number, url, fresh, chunks)
        except BackendRefusal as refusal:
            # The issue EXISTS. A refusal that hid its number invites a blind retry of `new`,
            # which duplicates it; name it and say what is left to do.
            refusal.err.update({
                "issue": number, "url": url,
                "message": f"{refusal.err.get('message', 'a step failed')} — issue #{number} "
                           f"({url}) was ALREADY created; do not run `new` again, finish "
                           f"that issue instead",
            })
            raise

    def _finish_create(self, phase: str, number: int, url: str, fresh: dict,
                       chunks: list[tuple[str, bool]]) -> str:
        # The abstract `workItemType:` key, projected to GitHub's own Issue Type — through
        # `_set_type` (`gh issue edit --type`), NEVER a `type` field on the create payload
        # above. MEASURED live (2026-08-07, holetz/claude-quenching#898): the REST create
        # silently drops an invalid name — `ok: true`, `issueType: null`, no error at all —
        # while `gh issue edit --type` validates against the repository's own types and
        # refuses loudly. A second call rather than free, but only once, and only when a
        # type actually resolves.
        type_key = fresh["frontmatter"].get("workItemType")
        type_name = self.types.get(type_key) if type_key else None
        if type_name:
            self._set_type(number, type_name)
        elif type_key:
            # The entry exists — `resolve_type_key` already gated `--type` at `new` — but
            # names no `github` translation. Never a refusal: an entry may exist for one
            # backend only without breaking the other (§Design), so the create proceeds
            # untyped and only says why.
            print(f"note: workItemType '{type_key}' has no `github` name declared in "
                  f"{CONFIG_FILE} — no type applied to this issue", file=sys.stderr)
        self._sync_parts(number, chunks)
        # A new issue is blocked by nothing yet: every wanted dependency is an add.
        wanted = azure_predecessor_ids(fresh["frontmatter"], number)
        if wanted:
            self._sync_dependencies(number, wanted, current={})
        if phase == "archive":
            # Created open and then closed, because "closed" is not a state an issue can be
            # born in. Two calls for a case `new` never takes — only a migration does.
            self._set_state(number, "closed")
        self._invalidate()
        return url

    def _blocked_by(self, number: int) -> dict[int, int]:
        """`{number: REST id}` of the issues this issue is blocked by."""
        return github_blocked_by(self._api(
            f"reading what issue #{number} is blocked by", "--paginate", "--slurp",
            f"repos/{self.repo}/issues/{number}/dependencies/blocked_by?per_page=100"),
            self.repo)

    def _sync_dependencies(self, number: int, wanted: list[int],
                           current: dict[int, int] | None = None) -> None:
        """Make the issue's blocked-by set equal `wanted`. A removal uses the REST id the
        listing returned; an add reads the blocking issue once for its id."""
        if current is None:
            current = self._blocked_by(number)
        adds, removes = github_dependency_diff(current, wanted)
        path = f"repos/{self.repo}/issues/{number}/dependencies/blocked_by"
        for dep in removes:
            self._api(f"removing #{dep} from what issue #{number} is blocked by",
                      "-X", "DELETE", f"{path}/{current[dep]}")
        for dep in adds:
            blocking = self._api(f"reading issue #{dep}, which issue #{number} waits for",
                                 f"repos/{self.repo}/issues/{dep}")
            self._write_api(f"marking issue #{number} as blocked by #{dep}", "POST", path,
                            {"issue_id": int(blocking["id"])})

    def predecessor_links(self, spec_id: str | int) -> list[int]:
        """The sorted issue numbers this spec's issue is blocked by."""
        return sorted(self._blocked_by(int(spec_id)))

    def move_spec(self, info: dict, dest_phase: str) -> str:
        number = int(info["id"])
        issue = self._set_state(number, "closed" if dest_phase == "archive" else "open")
        self._invalidate()
        return (issue or {}).get("html_url") \
            or f"https://github.com/{self.repo}/issues/{number}"

    def _set_state(self, number: int, state: str):
        return self._write_api(f"setting issue #{number} to {state}", "PATCH",
                               f"repos/{self.repo}/issues/{number}", {"state": state})

    def _set_type(self, number: int, type_name: str) -> None:
        """`gh issue edit --type`, the one PORCELAIN call this backend makes — never `gh api`.

        The REST `POST .../issues` this backend otherwise uses for everything silently drops
        an invalid `type` field: MEASURED live against `holetz/claude-quenching#898`, a create
        with a nonexistent type name came back `ok: true` with `issueType: null`, no error at
        all. `gh issue edit --type` is the one mechanism proven to validate against the
        repository's own issue types and refuse loudly for the same name — the same shape
        `gh_refusal` already reports for `gh api`, reused here because `gh`'s three failure
        modes (missing binary, unauthenticated, said no) are the same across both."""
        action = f"setting issue #{number}'s type to '{type_name}'"
        code, out, err, attempts = _gh_result(
            _gh_run(self.cwd, "issue", "edit", str(number),
                    "--repo", self.repo, "--type", type_name))
        if code != 0:
            raise BackendRefusal(gh_refusal(action, code, out, err, attempts))

    # -- the continuation comments a spilled document uses --------------------- #
    def _comments(self, number: int) -> list[dict]:
        return self._api(f"listing comments on #{number}",
                         "--paginate", "--slurp",
                         f"repos/{self.repo}/issues/{number}/comments?per_page=100") or []

    def _part_comments(self, number: int) -> list[tuple[int, dict]]:
        """`(index, comment)` for this issue's continuation comments, in part order.

        Ordinary discussion on a spec issue stays possible and is skipped here, the same way
        `_load` skips an issue without the spec marker: a store that assumed every comment was
        its own would eat a human's note the first time somebody left one."""
        found: list[tuple[int, dict]] = []
        for page in self._comments(number):
            for comment in (page if isinstance(page, list) else [page]):
                index, _, _ = hybrid_unwrap_part((comment or {}).get("body") or "")
                if index:
                    found.append((index, comment))
        found.sort(key=lambda row: row[0])
        return found

    def _joined(self, number: int, head: str, parts: int) -> str:
        """The whole document for a spilled spec — one extra call, made only when the marker
        on the body says there is more.

        A part the marker promised and the comments do not hold is a REFUSAL, not a shorter
        document: silently returning the head would hand every downstream command a spec whose
        `## Tasks` simply stops, and the next write would then persist that truncation as the
        new truth."""
        chunks = [(head, False)]      # nothing precedes the head, so its flag is unused
        for _, comment in self._part_comments(number):
            _, chunk, eol = hybrid_unwrap_part(comment.get("body") or "")
            chunks.append((chunk, eol))
        if len(chunks) != parts:
            raise BackendRefusal({
                "code": "sp-gh-parts-missing", "exit": 2, "issue": number,
                "found": len(chunks), "declared": parts,
                "message": f"issue #{number} declares {parts} document parts and "
                           f"{len(chunks)} are present — a continuation comment was deleted, or a "
                           f"multi-part write was interrupted before it finished; "
                           f"nothing was read and nothing was written",
            })
        return hybrid_join(chunks)

    def _sync_parts(self, number: int, chunks: list[tuple[str, bool]],
                    had_parts: int = 1) -> None:
        """Make the issue's continuation comments match `chunks[1:]` exactly.

        COSTS NOTHING FOR A ONE-PART SPEC THAT WAS ALREADY ONE PART, which is every write this
        repository will make but two: `chunks` is in hand and `had_parts` comes from the listing
        already fetched, so the common case returns before any call is made.

        `had_parts` is why the early return is safe. A document that USED to spill and no longer
        does still has stale comments to delete, and a check that only looked at the new part
        count would leave them there to be joined back on the next read — a truncation that
        would then be persisted as the truth by the write after it.

        A comment the document no longer needs is DELETED, not retired: unlike an issue, a
        comment really can be deleted with an ordinary token, so there is no leftover to skip
        on the next read and no second meaning for a marker to carry."""
        want = chunks[1:]
        if not want and had_parts <= 1:
            return
        existing = self._part_comments(number)
        for position, (chunk, eol) in enumerate(want, start=2):
            body = hybrid_wrap_part(position, len(chunks), chunk, eol)
            if position - 2 < len(existing):
                cid = existing[position - 2][1]["id"]
                self._write_api(f"updating continuation comment {cid} on #{number}", "PATCH",
                                f"repos/{self.repo}/issues/comments/{cid}", {"body": body})
            else:
                self._write_api(f"adding a continuation comment to #{number}", "POST",
                                f"repos/{self.repo}/issues/{number}/comments", {"body": body})
        for _, comment in existing[len(want):]:
            self._api(f"deleting a stale continuation comment on #{number}",
                      "-X", "DELETE", f"repos/{self.repo}/issues/comments/{comment['id']}")


def open_github_backend(root: str) -> tuple[SpecBackend | None, dict]:
    """The `github` backend for this workspace, or the refusal that says why not.

    Resolution happens HERE and not in `GitHubBackend.__init__`, so the cost — one `gh`
    round trip — is paid by the first command that actually needs a spec, on the same
    "on demand" rule `open_backend` applies to the files worktree. It is also what makes
    the missing-binary and not-logged-in refusals arrive at the START of a command instead
    of halfway through a write."""
    cwd = find_repo_root(root)
    repo, open_issues, err = resolve_github_repo(cwd)
    if err:
        return None, err
    types = {k: v["github"] for k, v in load_config(root)["workItemTypes"].items()
             if v.get("github")}
    return GitHubBackend(repo, cwd, types=types, open_issues=open_issues), {}
