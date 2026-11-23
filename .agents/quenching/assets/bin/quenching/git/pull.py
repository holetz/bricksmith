"""`cq git pr create|probe|view|record|merge` — the pull-request writes, with a fixed argv.

The `spec-orchestrator` (the only agent that publishes) holds no `gh pr create`/`gh pr merge` grant and no `az repos pr` grant: a
pattern on the command text never fenced `-d`, `-s`, `-r`, an alias, or Azure's
`--bypass-policy`/`--auto-complete`. Here every `gh` and `az` argv is built in this module, and
the route is the `provider` `cq specs config --json` prints. `create` names the base and the head
explicitly — on GitHub the body goes on stdin; on Azure it opens the PR with no completion
option at all, so no policy is bypassed and nothing completes on its own, and a body past
Azure's 4000-character description limit goes in cut at a section boundary and whole as the PR's
first comment thread (`az devops invoke`, argv built here too). `probe` reads the
provider's route and `view` one PR's state, base and head; neither writes. `merge` dispatches
Azure web URLs to `azure_pull` (build validation policies, pinned head, no-fast-forward,
no policy bypass). On GitHub it reads `gh pr checks`
first and merges only with every check green — a `skipping` check the gate requires is not
green — and the PR's `mergeStateStatus` clean, pinning the head it read with
`--match-head-commit`, always with `--merge` — never `--squash`,
`--rebase`, `--delete-branch`, `--admin` or `--auto`.

Exit 0 done · 1 findings (`gh`/`az` refused, or a check red, pending or absent — `reason` names
which) · 2 refusal (a malformed name or URL)."""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

from quenching.common.output import FINDINGS, OK, emit, refuse
from quenching.common.tools import spawn_argv
from quenching.git.audit import _branch_ok
from quenching.specs.commands.fields import cmd_record
from quenching.specs.backends import open_backend
from quenching.specs.commands.epic import AZURE_PR_STATE
from quenching.specs.commands.output import Emitter, read_one
from quenching.git.pr import normalize_azure_pull_request
from quenching.git.push import _specs_note
from quenching.specs.commands.push import sync_store
from quenching.specs.config import find_repo_root, load_config

PR_URL = re.compile(r"https://[A-Za-z0-9.-]+/[\w.-]+/[\w.-]+/pull/(\d+)")
GREEN = {"pass", "skipping"}
MERGEABLE = {"CLEAN", "HAS_HOOKS"}
RED = {"fail", "cancel"}
POLL_S = 20
WAIT_MAX_S = 540
GH_TIMEOUT_S = 120
# Azure DevOps refuses a PR description longer than this, in characters, markdown or not.
AZURE_DESCRIPTION_MAX = 4000
AZURE_BODY_NOTE = "Corpo completo no primeiro comentário deste PR."


def _tool(tool: str, *argv: str, stdin: str | None = None) -> tuple[int, str, str]:
    try:
        done = subprocess.run(spawn_argv([tool, *argv]), capture_output=True, text=True, encoding="utf-8", errors="replace", input=stdin or "",
                              timeout=GH_TIMEOUT_S, env={**os.environ, "LC_ALL": "C", "LANG": "C"})
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "", str(e)
    return done.returncode, done.stdout, done.stderr


def _gh(*argv: str, stdin: str | None = None) -> tuple[int, str, str]:
    return _tool("gh", *argv, stdin=stdin)


def _az(*argv: str) -> tuple[int, str, str]:
    return _tool("az", *argv)


def _provider() -> str | None:
    """`github`, `azure-boards`, or None when the origin names neither host."""
    try:
        return load_config(find_repo_root(os.getcwd())).get("provider")
    except Exception:  # an unreadable config routes nowhere new: create keeps the GitHub route
        return None


def _checks(url: str) -> tuple[str, list[dict], str]:
    """`green`, `red`, `pending`, `none` or `error`, the checks, and gh's message."""
    code, out, err = _gh("pr", "checks", url, "--json", "name,bucket")
    try:
        checks = json.loads(out) if out.strip() else []
    except json.JSONDecodeError:
        checks = None
    if not isinstance(checks, list):
        return "error", [], (err or out).strip()
    if not checks:
        return ("none" if code == 0 or "no checks" in err.lower() else "error"), [], err.strip()
    buckets = {c.get("bucket") for c in checks if isinstance(c, dict)}
    if buckets & RED:
        return "red", checks, err.strip()
    if buckets <= GREEN and all(isinstance(c, dict) for c in checks):
        if "skipping" in buckets:
            skipped = _required_skipped(url, checks)
            if skipped:
                return "skipped", [c for c in checks if c.get("name") in skipped], err.strip()
        return "green", checks, err.strip()
    return "pending", checks, err.strip()


def _required_skipped(url: str, checks: list[dict]) -> set[str]:
    """Names of the `skipping` checks the gate requires; when gh cannot list the required
    checks, every skipping check counts as required."""
    skipping = {c.get("name") for c in checks if c.get("bucket") == "skipping"}
    code, out, err = _gh("pr", "checks", url, "--required", "--json", "name")
    if code == 1 and "no required checks reported" in err.lower():
        return set()
    try:
        required = ({c.get("name") for c in json.loads(out)}
                    if code in (0, 8) and out.strip() else None)
    except (json.JSONDecodeError, AttributeError, TypeError):
        required = None
    return skipping if required is None else skipping & required


def _view(url: str) -> tuple[str, str]:
    """The PR's head sha and `mergeStateStatus`, `("", "")` when gh cannot say."""
    _, out, _ = _gh("pr", "view", url, "--json", "headRefOid,mergeStateStatus")
    try:
        data = json.loads(out)
        return str(data["headRefOid"]), str(data["mergeStateStatus"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return "", ""


def _is_merged(url: str) -> bool:
    """True only when gh reports the PR `MERGED`; anything unreadable is not merged."""
    _, out, _ = _gh("pr", "view", url, "--json", "state")
    try:
        return json.loads(out).get("state") == "MERGED"
    except (json.JSONDecodeError, AttributeError, TypeError):
        return False


def _stamp_merge(spec: str, number: int, url: str, subject: str | None = None) -> dict:
    """Merge the `merge` record of the spec this PR was merged for, through `cmd_record`.

    Write-once: a record already present is reported as `stamped: false`, never rewritten.
    The subject follows GitHub's `Merge pull request #N from owner/head` without the `#`, which frontmatter reads as a comment;
    Azure passes its own `Merge pull request N from <branch> into <target>`."""
    quiet = _Quiet()
    try:
        backend, err = open_backend(find_repo_root(os.getcwd()))
        info, err2 = (None, err) if err else read_one(backend, spec, quiet)
        if err2 or not info:
            return {"ok": False, "message": (err2 or {}).get("message", "no such spec")}
        present = (info.get("frontmatter") or {}).get("merge")
        if present:
            return {"ok": True, "stamped": False, "value": present}
        if subject is None:  # GitHub: the subject names owner/head, read from the PR itself
            head = _real_head(number, url)
            if isinstance(head, str):
                return {"ok": False, "message": f"head unreadable: {head}"}
            owner = url.split("/")[3]
            subject = f"Merge pull request {number} from {owner}/{head[1]}"
        ns = SimpleNamespace(spec=spec, name="merge", json=True, set=[
            "strategy=merge-commit", f"subject={subject}", f"pr={number}"])
        code = cmd_record(ns, find_repo_root(os.getcwd()), quiet)
    except Exception as e:  # the PR is merged already: a stamp that blew up is reported, not raised
        return {"ok": False, "message": str(e)}
    if code != 0:
        return {"ok": False, "message": quiet.seen.get("message", f"record exited {code}")}
    return {"ok": True, "stamped": True, "value": quiet.seen.get("value")}


def _merged(args, payload: dict, human: str) -> int:
    """Emit a merge that happened, stamping the spec's `merge` record when `--spec` names one."""
    spec = getattr(args, "spec", None)
    if spec:
        number = int(PR_URL.fullmatch(args.url).group(1))
        payload["merge"] = stamp = _stamp_merge(spec, number, args.url)
        if not stamp["ok"]:
            payload.update(ok=False, reason="stamp-failed", message=stamp["message"])
            emit(args.json, payload, f"{human}; `merge` record not stamped: {stamp['message']}")
            return FINDINGS
    payload["specs"] = specs = sync_store(find_repo_root(os.getcwd()))
    emit(args.json, payload, human + _specs_note(specs))
    return OK


def _merge(args) -> int:
    from quenching.git.azure_pull import AZURE_PR_URL, merge_azure

    azure = AZURE_PR_URL.fullmatch(args.url or "")
    match = PR_URL.fullmatch(args.url or "")
    if not match and not azure:
        return refuse({"code": "git-pr-url-invalid",
                       "message": f"not a pull-request URL: {args.url}"}, args.json)
    if not 0 <= args.wait <= WAIT_MAX_S:
        return refuse({"code": "git-pr-wait-invalid",
                       "message": f"--wait must be between 0 and {WAIT_MAX_S} seconds"}, args.json)
    if azure:
        return merge_azure(args, azure)
    def already_merged() -> int:
        return _merged(args, {"ok": True, "url": args.url, "number": int(match.group(1)),
                              "alreadyMerged": True}, f"already merged {args.url}")

    if _is_merged(args.url):
        return already_merged()
    deadline = time.monotonic() + args.wait
    head, _ = _view(args.url)
    while True:
        state, checks, message = _checks(args.url)
        if state != "pending" or time.monotonic() + POLL_S > deadline:
            break
        print(f"checks pending; re-reading in {POLL_S}s", file=sys.stderr, flush=True)
        time.sleep(POLL_S)
    names = sorted(str(c.get("name")) for c in checks if isinstance(c, dict)
                   and (c.get("bucket") not in GREEN or state == "skipped"))
    payload = {"ok": False, "url": args.url, "number": int(match.group(1)), "checks": state,
               "notGreen": names}
    if state == "none" and getattr(args, "allow_no_checks", False):
        payload["noChecksAccepted"] = True
    elif state != "green":
        payload["reason"] = {"red": "failed", "pending": "pending", "none": "no-checks",
                                    "skipped": "skipped-required"}.get(
            state, "checks-unreadable")
        if message:
            payload["message"] = message
        emit(args.json, payload, f"not merged: {payload['reason']} {' '.join(names)}".rstrip())
        return FINDINGS
    now, merge_state = _view(args.url)
    if not head or now != head or merge_state not in MERGEABLE:
        if _is_merged(args.url):
            return already_merged()
        payload.update(reason="merge-state", message=f"head {head or '?'} -> {now or '?'}, "
                       f"mergeStateStatus {merge_state or 'unreadable'}")
        emit(args.json, payload, f"not merged: {payload['message']}")
        return FINDINGS
    code, out, err = _gh("pr", "merge", args.url, "--merge", f"--match-head-commit={head}")
    if code != 0:
        if _is_merged(args.url):
            return already_merged()
        payload.update(reason="merge-refused", message=(err or out).strip())
        emit(args.json, payload, f"not merged: {payload['message']}")
        return FINDINGS
    payload["ok"] = True
    return _merged(args, payload, f"merged {args.url} (--merge)")


class _Quiet(Emitter):
    """Collects what `cmd_record` would print, so the verb emits ONE document."""

    def __init__(self) -> None:
        self.seen: dict = {}

    def emit(self, as_json: bool, obj: dict, human: str) -> None:
        self.seen = obj


def _stamp(spec: str, number: int, url: str) -> dict:
    """Merge the `pr` record through the same function `cq specs record` runs."""
    quiet = _Quiet()
    ns = SimpleNamespace(spec=spec, name="pr", json=True, set=[
        f"number={number}", f"url={url}", f"date={datetime.date.today().isoformat()}"])
    try:
        code = cmd_record(ns, find_repo_root(os.getcwd()), quiet)
    except Exception as e:  # the PR exists already: a stamp that blew up is reported, not raised
        return {"ok": False, "message": str(e)}
    if code != 0:
        return {"ok": False, "message": quiet.seen.get("message", f"record exited {code}")}
    return {"ok": True, "value": quiet.seen.get("value")}


def _spec_vs_head(spec: str, head: str, *, by_name: bool = True) -> dict | None:
    """Refuse BEFORE any PR exists: the spec must exist and own `head`.

    The spec owns the branch its `branch.work` record names; before that record exists, the
    branch name must carry the spec's id (`plan/<id>-<slug>`) unless `by_name` is off, as in
    `worktree add`, where the convention need not carry the id but a name carrying ANOTHER spec's
    id is still refused. `None` means the pair is fine."""
    quiet = _Quiet()
    backend, err = open_backend(find_repo_root(os.getcwd()))
    info, err2 = (None, err) if err else read_one(backend, spec, quiet)
    if err2 or not info:
        return {"code": "git-pr-spec-unknown",
                "message": f"--spec {spec} does not resolve: "
                           f"{(err2 or {}).get('message', 'no such spec')}"}
    work = ((info.get("frontmatter") or {}).get("branch") or {}).get("work")
    sid = str(info["id"])
    if work:
        owned = work == head
    elif by_name:
        owned = re.match(rf"(?:.+/)?{re.escape(sid)}-", head) is not None
    else:
        other = re.match(r"(?:.+/)?(\d+)-", head)
        owned = other is None or other.group(1) == sid
    if not owned:
        return {"code": "git-pr-spec-head-mismatch",
                "message": f"spec {sid} does not own branch {head}"
                           + (f" (its branch record is {work})" if work else "")}
    return None


def _real_head(number: int, url: str) -> tuple[int, str] | str:
    """The PR's own `(number, head branch)` as the provider reports it, or gh's/az's message."""
    if _provider() == "azure-boards":
        code, out, err = _az("repos", "pr", "show", f"--id={number}", "--detect=true",
                             "--output=json")
        keys = ("pullRequestId", "sourceRefName")
    else:
        code, out, err = _gh("pr", "view", url, "--json", "headRefName,number")
        keys = ("number", "headRefName")
    try:
        data = json.loads(out)
        return int(data[keys[0]]), str(data[keys[1]]).removeprefix("refs/heads/")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return (err or out).strip() or "the provider returned no pull request"


def _record(args) -> int:
    """`pr record`: stamps the `pr` record of a PR opened outside `pr create`, once the PR the
    provider reports under `--number`/`--url` publishes `--head`."""
    number, url = args.number, args.url
    if number < 1 or not url.startswith("https://") or "/_apis/" in url:
        return refuse({"code": "git-pr-record-invalid",
                       "message": "--number must be positive and --url the PR's https webUrl "
                                  "(never an /_apis/ URL)"}, args.json)
    bad = _spec_vs_head(args.spec, args.head)
    if bad:
        return refuse(bad, args.json)
    real = _real_head(number, url)
    if isinstance(real, str):
        emit(args.json, {"ok": False, "number": number, "url": url, "head": args.head,
                         "reason": "head-unreadable", "message": real},
             f"`pr` record not stamped: head unreadable: {real}")
        return FINDINGS
    if real != (number, args.head):
        return refuse({"code": "git-pr-record-head-mismatch",
                       "message": f"PR #{real[0]} publishes {real[1]}, not #{number} "
                                  f"{args.head}"}, args.json)
    stamp = _stamp(args.spec, number, url)
    payload = {"ok": stamp["ok"], "number": number, "url": url, "head": args.head, "pr": stamp}
    if not stamp["ok"]:
        payload.update(reason="stamp-failed", message=stamp["message"])
    emit(args.json, payload, f"pr record stamped: #{number} {url}" if stamp["ok"]
         else f"`pr` record not stamped: {stamp['message']}")
    return OK if stamp["ok"] else FINDINGS


def _open_github(args) -> tuple[int, str, dict] | str:
    code, out, err = _gh("pr", "create", f"--base={args.base}", f"--head={args.head}",
                         f"--title={args.title}", "--body-file=-", stdin=args.body)
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    url = next((line for line in reversed(lines) if PR_URL.fullmatch(line)), None)
    if code != 0 or url is None:
        return (err or out).strip()
    return int(PR_URL.fullmatch(url).group(1)), url, {}


def _azure_description(body: str) -> str:
    """The body as Azure's description, which the API refuses past `AZURE_DESCRIPTION_MAX`
    characters (markdown does not change the count): a longer body is cut at the last `\\n## `
    section boundary that fits beside the note, or at the raw limit when none does."""
    if len(body) <= AZURE_DESCRIPTION_MAX:
        return body
    note = f"\n\n{AZURE_BODY_NOTE}"
    room = AZURE_DESCRIPTION_MAX - len(note)
    cut = body.rfind("\n## ", 0, room + len("\n## "))
    head = body[:cut] if 0 < cut <= room else body[:room]
    return head.rstrip() + note


def _azure_comment(raw: dict, number: int, body: str) -> dict:
    """Publish the whole body as the PR's first thread (markdown, status closed) through
    `az devops invoke`; the result, never an exception — a failure here never undoes the PR."""
    repository = raw.get("repository") if isinstance(raw.get("repository"), dict) else {}
    project = repository.get("project") if isinstance(repository.get("project"), dict) else {}
    project_ref = project.get("name") or project.get("id")
    repository_id = repository.get("id")
    if not project_ref or not repository_id:
        return {"ok": False, "message": "az returned no repository/project id for the comment"}
    thread = {"comments": [{"parentCommentId": 0, "content": body, "commentType": 1}],
              "status": 4}
    try:
        handle, path = tempfile.mkstemp(prefix="cq-pr-thread-", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(thread, stream, ensure_ascii=False)
    except OSError as e:
        return {"ok": False, "message": f"could not write the comment payload: {e}"}
    try:
        code, out, err = _az("devops", "invoke", "--detect=true", "--area=git",
                             "--resource=pullRequestThreads", "--route-parameters",
                             f"project={project_ref}", f"repositoryId={repository_id}",
                             f"pullRequestId={number}", "--http-method=POST",
                             f"--in-file={path}", "--output=json")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if code != 0:
        return {"ok": False, "message": (err or out).strip() or f"az exited {code}"}
    try:
        thread_id = json.loads(out).get("id")
    except (json.JSONDecodeError, AttributeError):
        thread_id = None
    return {"ok": True, "threadId": thread_id}


def _open_azure(args) -> tuple[int, str, dict] | str:
    """`az repos pr create` with no `--auto-complete`, `--bypass-policy` or merge option. A body
    past Azure's description limit goes in cut (`_azure_description`) and, once the PR exists,
    whole as its first comment thread; a comment that fails is a finding, the PR stands."""
    argv = ["repos", "pr", "create", "--detect=true", f"--source-branch={args.head}",
            f"--target-branch={args.base}", f"--title={args.title}"]
    description = _azure_description(args.body) if args.body else ""
    if description:
        argv.append(f"--description={description}")
    if args.work_item:
        argv.append(f"--work-items={args.work_item}")
    if args.transition_work_items:
        argv.append("--transition-work-items=true")
    if args.delete_source_branch:
        argv.append("--delete-source-branch=true")
    code, out, err = _az(*argv, "--output=json")
    try:
        raw = json.loads(out) if code == 0 else None
        pr = normalize_azure_pull_request(raw) if raw is not None else None
    except (json.JSONDecodeError, AttributeError):
        raw = pr = None
    if not pr or not pr["webUrl"] or not str(pr["id"]).isdigit():
        return (err or out).strip() or "az returned no pull request"
    number = int(pr["id"])
    extra: dict = {"apiUrl": pr["apiUrl"]}
    if description != (args.body or ""):
        extra["descriptionTruncated"] = True
        comment = _azure_comment(raw, number, args.body)
        extra["bodyComment"] = comment
        if not comment["ok"]:
            extra["findings"] = [{"code": "git-pr-azure-comment-failed",
                                  "message": "the PR stands with a cut description; the whole "
                                             f"body was not posted as a comment: "
                                             f"{comment['message']}"}]
    return number, pr["webUrl"], extra


def _create(args) -> int:
    cwd = os.getcwd()
    for label, value in (("base", args.base), ("head", args.head)):
        if value.startswith("-") or not _branch_ok(cwd, value):
            return refuse({"code": "git-pr-ref-invalid",
                           "message": f"{label} is not a branch name: {value}"}, args.json)
    if not args.title.strip():
        return refuse({"code": "git-pr-title-blank", "message": "--title may not be blank"},
                      args.json)
    provider = "azure-boards" if _provider() == "azure-boards" else "github"
    azure_only = args.work_item or args.transition_work_items or args.delete_source_branch
    if provider == "github" and azure_only:
        return refuse({"code": "git-pr-flag-provider",
                       "message": "--work-item, --transition-work-items and "
                                  "--delete-source-branch are Azure-only"}, args.json)
    if args.work_item is not None and args.work_item < 1:
        return refuse({"code": "git-pr-work-item-invalid",
                       "message": "--work-item must be positive"}, args.json)
    if args.spec:
        bad = _spec_vs_head(args.spec, args.head)
        if bad:
            return refuse(bad, args.json)
    if args.body == "-":
        args.body = sys.stdin.read()
    if provider == "azure-boards":
        at = next((flag for flag, value in (("--title", args.title), ("--description", args.body),
                                            ("--head", args.head), ("--base", args.base))
                   if value.startswith("@")), None)
        if at:
            return refuse({"code": "git-pr-azure-at-prefix",
                           "message": f"az reads a {at} value starting with `@` as a local "
                                      "file; rewrite it so it does not begin with `@`"},
                          args.json)
    opened = _open_azure(args) if provider == "azure-boards" else _open_github(args)
    if isinstance(opened, str):
        emit(args.json, {"ok": False, "provider": provider, "reason": "create-refused",
                         "message": opened}, f"PR not created: {opened}")
        return FINDINGS
    number, url, extra = opened
    payload = {"ok": True, "provider": provider, "url": url, "number": number,
               "base": args.base, "head": args.head, **extra}
    if args.spec:
        payload["pr"] = stamp = _stamp(args.spec, number, url)
        if not stamp["ok"]:
            payload.update(ok=False, reason="stamp-failed", message=stamp["message"])
            emit(args.json, payload, f"PR #{number}: {url} opened; `pr` record not stamped: "
                                     f"{stamp['message']}")
            return FINDINGS
    payload["specs"] = specs = sync_store(find_repo_root(os.getcwd()))
    notes = "".join(f"\nfinding: {f['message']}" for f in payload.get("findings", []))
    emit(args.json, payload, f"PR #{number}: {url}" + _specs_note(specs) + notes)
    return OK


def _probe(args) -> int:
    """Read-only: does this checkout have an authenticated PR route on its provider?"""
    provider = _provider()
    if provider == "azure-boards":
        code, out, err = _az("repos", "pr", "list", "--status=all", "--top=1", "--detect=true",
                             "--output=json")
    elif provider == "github":
        code, out, err = _gh("repo", "view", "--json", "nameWithOwner")
    else:
        code, out, err = 1, "", "origin names neither github nor azure-boards"
    payload = {"ok": code == 0, "provider": provider}
    if code != 0:
        message = (err or out).strip()
        lowered = message.lower()
        payload.update(reason="unauthenticated" if provider and ("login" in lowered
                       or "auth" in lowered) else "no-route", message=message)
        emit(args.json, payload, f"no PR route: {payload['reason']} {message}".rstrip())
        return FINDINGS
    emit(args.json, payload, f"PR route: {provider}")
    return OK


def _show(args) -> int:
    """Read-only: the PR's `state` (`MERGED`/`OPEN`/`CLOSED`), `base` and `head` on its provider."""
    if args.number < 1:
        return refuse({"code": "git-pr-number-invalid",
                       "message": "--number must be positive"}, args.json)
    provider = _provider()
    if provider == "azure-boards":
        code, out, err = _az("repos", "pr", "show", f"--id={args.number}", "--detect=true",
                             "--output=json")
    else:
        provider = "github"
        code, out, err = _gh("pr", "view", str(args.number), "--json",
                             "number,state,baseRefName,headRefName,url")
    try:
        data = json.loads(out) if code == 0 else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        message = (err or out).strip() or "the provider returned no pull request"
        emit(args.json, {"ok": False, "provider": provider, "number": args.number,
                         "reason": "unreadable", "message": message},
             f"PR #{args.number} unreadable: {message}")
        return FINDINGS
    if provider == "azure-boards":
        state = AZURE_PR_STATE.get(str(data.get("status") or "").lower(), "")
        base, head = data.get("targetRefName"), data.get("sourceRefName")
        url = normalize_azure_pull_request(data)["webUrl"]
    else:
        state, base, head = str(data.get("state") or ""), data.get("baseRefName"), \
            data.get("headRefName")
        url = data.get("url")
    base, head = (str(ref or "").removeprefix("refs/heads/") for ref in (base, head))
    payload = {"ok": True, "provider": provider, "number": args.number, "url": url,
               "state": state, "base": base, "head": head}
    emit(args.json, payload, f"PR #{args.number} {state}: {head} -> {base} {url or ''}".rstrip())
    return OK


def cmd_pr(args) -> int:
    if args.action == "view":
        return _show(args)
    if args.action == "record":
        return _record(args)
    if args.action == "probe":
        return _probe(args)
    return _merge(args) if args.action == "merge" else _create(args)
