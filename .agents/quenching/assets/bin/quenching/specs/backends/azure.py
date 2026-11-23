"""The `azure-boards` backend — specs as Azure Boards work items, over the `az` CLI.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time

from quenching.common.git import COMMAND_TIMEOUT_S, _git
from quenching.common.config import CONFIG_FILE, find_repo_root
from quenching.common.io import write_text
from quenching.common.leak import guard_store_write
from quenching.common.tools import spawn_argv
from quenching.specs.backends.base import (BackendRefusal, SpecBackend, parse_retry_after,
                                           read_only_refusal, transport_read_only)
from quenching.specs.backends.hybrid import (hybrid_project, hybrid_split, hybrid_title_join,
                                             hybrid_unwrap, hybrid_wrap)
from quenching.specs import store
from quenching.specs.config import (AZ_DEFAULT_DISCOVERY_TAG, announce_unproved, load_config,
                                    resolve_work_item_type)
from quenching.specs.parse.deps import deps_of
from quenching.specs.parse import (FIELD_KEYS, PHASES, board_state_of,
                                   carry_forward_fields, declared_tags, derive_info,
                                   derive_labels, strip_frontmatter_keys, tags_outside_catalog)


# OURS, never one of az's: the binary is not on PATH, so no process ever started. Same
# number and same meaning as `GH_MISSING`, kept separate so neither constant becomes the
# other's by accident.
AZ_MISSING = 127
# OURS, like `AZ_MISSING`: the call hit the timeout, so the outcome of a write is UNKNOWN.
AZ_TIMEOUT = 124
AZ_MAX_ATTEMPTS = 3
AZ_RETRY_BACKOFF = (0.25, 0.5)
# WIQL answers at most this many ids; at the ceiling the listing may be truncated.
AZ_WIQL_LIMIT = 20_000
# Azure DevOps' own application id. `az rest` issues a token for whatever `--resource` names,
# and the default (ARM) is rejected by dev.azure.com — this is the audience the API accepts,
# and it is a constant of the service rather than of any organisation.
AZ_DEVOPS_RESOURCE_ID = "499b84ac-1321-427f-aa17-267ca6975798"

# `az` does NOT have gh's exit 4 — it answers almost everything with exit 1 and says why in
# stderr, so the split into remedies is made on what it SAID rather than on the code. Each
# tuple is (fragment lowercased, refusal code, remedy), tried in order; the first match
# wins, so the more specific fragments come first.
AZ_STDERR_SIGNALS = (
    ("az extension add", "sp-az-extension-missing",
     "run `az extension add --name azure-devops`"),
    ("is not in the 'az' command group", "sp-az-extension-missing",
     "run `az extension add --name azure-devops`"),
    ("az devops login", "sp-az-unauthenticated", "run `az devops login`"),
    ("az login", "sp-az-unauthenticated", "run `az login`"),
    ("before you can run azure devops commands", "sp-az-unauthenticated",
     "run `az devops login`"),
    ("tf400813", "sp-az-unauthenticated",
     "the identity is authenticated but not authorised for this project"),
    # Captured from az 2.88 by running `az boards query` with no defaults set. It reaches
    # this table only when the defaults vanish BETWEEN `resolve_azure_project` and the call
    # — otherwise resolution refuses first, with `sp-az-no-project`, which is why both
    # carry the same code and the same remedy.
    ("must be specified", "sp-az-no-project",
     "run `az devops configure --defaults organization=https://dev.azure.com/<org> "
     "project=<project>`"),
    # The PATCH endpoint's own vocabulary. A consolidated write is all-or-nothing, so these
    # two take the document edit down with them — which is exactly why each carries the
    # remedy for ITS cause instead of arriving as one anonymous `sp-az-api-error`.
    ("the type changed without a value", "sp-az-format-uncoupled",
     "the multilineFieldsFormat op travelled without its own System.Description value — "
     "`azure_patch_body` couples them, so reaching this means the coupling broke"),
    ("rule error", "sp-az-rule-error",
     "the process rejected a field value — check `azureColumns`/`azureStates` against what "
     "this project's board actually allows; nothing was written"),
)


def azure_cache_path(org: str, project: str) -> str:
    """Where this org+project's resolutions are remembered, BETWEEN processes.

    OUTSIDE THE REPOSITORY, always. A cache in the tree is committed, travels to another
    machine and to another checkout, and a stale entry there points at a work item that was
    recreated — writing somebody else's card while looking exactly like a hit. The user cache
    directory is per-machine by construction, which is the property that matters.

    IT IS NOT A STORE. `spec-backend.md` §Granular reading already admits a cache inside
    `cq specs` on three conditions — it is not authoritative, nothing outside the CLI reads
    it, and the backend stays the source of truth. Crossing processes changes none of those;
    what it changes is that a wrong entry now survives the process that wrote it, which is
    why every reader here re-validates against what came back rather than trusting the hit.

    Keyed by org and project so two projects in one org never see each other's answers."""
    import hashlib
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache")
    key = hashlib.sha256(f"{org}\n{project}".encode()).hexdigest()[:16]
    return os.path.join(base, "quenching", "azure", f"{key}.json")


def azure_cache_read(org: str, project: str) -> dict:
    """What was remembered, or `{}` — an unreadable or corrupt cache is a miss, never a
    failure. Nothing here is authoritative, so there is nothing to refuse over."""
    try:
        with open(azure_cache_path(org, project), encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {}
        data.pop("slugs", None)
        return data
    except (OSError, json.JSONDecodeError):
        return {}


def azure_cache_write(org: str, project: str, **entries) -> None:
    """Merge `entries` into the cache. A write that cannot happen is silently nothing: the
    cache only ever saves a call, so failing to save one is not worth a refusal."""
    path = azure_cache_path(org, project)
    data = azure_cache_read(org, project)
    data.pop("slugs", None)
    data.update(entries)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text(path, json.dumps(data))
    except OSError:
        pass


def _az_transient(code: int, stdout: str, stderr: str) -> bool:
    """Whether a failed `az` call may succeed if the same request is tried again."""
    if code == AZ_TIMEOUT:
        return True
    text = f"{stdout}\n{stderr}".lower()
    return ("rate limit" in text or "too many requests" in text
            or bool(re.search(r"\b(?:429|500|502|503|504)\b", text)))


def _az_option(argv: tuple[str, ...], *names: str, default: str = "") -> str:
    for i, token in enumerate(argv):
        if token in names and i + 1 < len(argv):
            return argv[i + 1]
        for name in names:
            if token.startswith(name + "="):
                return token.split("=", 1)[1]
    return default


def _az_is_read(argv: tuple[str, ...]) -> bool:
    """Whether an `az` call only reads, and is therefore safe to repeat.

    `az rest` is a GET unless `--method` says otherwise. `devops invoke` against the
    `workitemsbatch` resource is a POST by transport but a pure read by meaning, so it is
    admitted. `boards query` and `work-item show` read; everything unrecognised counts as a
    write, because the guard that uses this must fail closed."""
    if not argv:
        return False
    if argv[0] == "rest":
        return _az_option(argv, "--method", "-m", default="get").lower() == "get"
    if argv[0] == "devops":
        if len(argv) > 1 and argv[1] == "invoke":
            return (_az_option(argv, "--http-method", default="GET").upper() == "GET"
                    or _az_option(argv, "--resource") == "workitemsbatch")
        return len(argv) > 1 and argv[1] == "configure" and "--list" in argv \
            or argv[:3] == ("devops", "project", "show")
    if argv[0] == "repos":
        return len(argv) > 1 and argv[1] == "show"
    if argv[0] == "boards":
        return argv[1:2] == ("query",) or argv[1:3] == ("work-item", "show")
    return False


def _az_run(cwd: str, *argv: str, stdin: str | None = None) -> tuple[int, str, str]:
    """Exit code, stdout AND stderr of one `az` command.

    A SIBLING of `_gh_run` for the same reason that one is a sibling of `_git_run`: the two
    CLIs fail differently, and here the difference is that `az` has no dedicated
    "unauthenticated" exit code — it says so in stderr and exits 1, the same as an API
    error. Flattening them would tell a human to check the project name when the real fix is
    `az devops login`.

    `--only-show-errors` suppresses az's upgrade notices and preview warnings, which
    otherwise land in stderr and would be quoted back as the reason a call failed.

    A missing binary comes back as `AZ_MISSING` rather than as an exception, so the failure
    a user is most likely to hit stays an ordinary return value.

    60s and not git's 30, matching `_gh_run`: this is a round trip to dev.azure.com."""
    import subprocess
    # `az` expands a value opening with `@` into the CONTENT of that file, so a title like
    # `@/etc/hostname` would publish a local file as the work item's title.
    title = next((argv[i + 1] for i, a in enumerate(argv[:-1]) if a == "--title"), "")
    title = next((a[len("--title="):] for a in argv if a.startswith("--title=")), title)
    if title.startswith("@"):
        raise BackendRefusal({
            "code": "sp-az-bad-title", "exit": 2,
            "message": "a title opening with `@` is read by `az` as a file path; "
                       f"start it with another character — got {title[:40]!r}"})
    reads = _az_is_read(argv)
    if not reads and transport_read_only():
        raise read_only_refusal("az", argv)
    if not os.path.isdir(cwd):
        return 1, "", f"not a directory: {cwd}"
    # ONLY READS ARE REPEATED, exactly as in `_gh_run`: a timeout can fire after Azure
    # accepted a create, and repeating it would make a second work item.
    max_attempts = AZ_MAX_ATTEMPTS if reads else 1
    env = store.subprocess_env("azure")
    for attempt in range(1, max_attempts + 1):
        try:
            out = subprocess.run(spawn_argv(["az", *argv, "--only-show-errors"]), capture_output=True,
                                 text=True, timeout=COMMAND_TIMEOUT_S, cwd=cwd, input=stdin,
                                 env=env)
            code, stdout, stderr = out.returncode, out.stdout, out.stderr
        except FileNotFoundError as e:
            return AZ_MISSING, "", str(e)
        except subprocess.TimeoutExpired as e:
            detail = e.stderr or str(e) or "az command timed out"
            if isinstance(detail, bytes):
                detail = detail.decode(errors="replace")
            code, stdout, stderr = AZ_TIMEOUT, "", str(detail)
        except (OSError, ValueError, subprocess.SubprocessError) as e:   # noqa: BLE001
            return 1, "", str(e)
        if code == 0 or attempt == max_attempts or not _az_transient(code, stdout, stderr):
            return code, stdout, stderr
        delay = parse_retry_after(stderr, stdout)
        time.sleep(AZ_RETRY_BACKOFF[attempt - 1] if delay is None else delay)
    return code, stdout, stderr


def _az_said(stdout: str, stderr: str) -> str:
    """The one line worth quoting back from a failed `az` call.

    Unlike `gh`, `az` puts the whole story in stderr and prefixes it with `ERROR: `. Stdout
    is read as a fallback only, for the calls that fail with a JSON body and an empty
    stderr."""
    for line in (stderr or "").splitlines():
        line = line.strip()
        if not line:
            continue
        return line[6:].strip() if line.upper().startswith("ERROR:") else line
    head = next((ln.strip() for ln in (stdout or "").splitlines() if ln.strip()), "")
    return head or "az failed without saying why"


def az_refusal(action: str, code: int, stdout: str, stderr: str) -> dict:
    """Every way an `az` call can fail, as an exit-2 refusal a human can act on.

    FIVE OUTCOMES, FIVE REMEDIES — one more than the `gh` transport has, because `az boards`
    lives in an extension that is not installed by default. A human whose `az` is installed
    and logged in still gets "not recognised" until they add it, and telling them to log in
    again would be the wrong remedy delivered confidently.

    The `code == 0` branch is reached only by `_az`'s single-item calls (show/create/update),
    which never legitimately print nothing on success. It is never reached for the WIQL
    query: measured on this org (az 2.89.0 + azure-devops 1.0.6), a query that matches zero
    work items and a query whose macro failed to resolve are byte-identical — exit 0, empty
    stdout, empty stderr — so refusing there on emptiness alone would refuse the ordinary
    "no specs yet" case exactly as often as the fault it exists to catch. `_az`'s caller
    decides which shape it asked for; this function only renders the refusal once asked.

    Always exit 2, always a refusal and never a finding: nothing was read and nothing was
    written."""
    if code == AZ_MISSING:
        return {
            "code": "sp-az-missing", "exit": 2, "action": action,
            "message": "backend 'azure-boards' needs the Azure CLI and it is not on PATH — "
                       "install `az` (https://aka.ms/azure-cli), then run "
                       "`az extension add --name azure-devops` and `az devops login`; no "
                       "spec was read or written",
        }
    if code == AZ_TIMEOUT:
        return {
            "code": "sp-az-timeout", "exit": 2, "action": action,
            "message": f"`az` timed out while {action}; if that was a write its outcome is "
                       f"UNKNOWN and it was not retried — re-read the work item before "
                       f"writing again",
        }
    if code == 0:
        return {
            "code": "sp-az-empty-response", "exit": 2, "action": action,
            "message": f"`az boards` exited 0 while {action} but printed nothing — a "
                       f"single-item call never legitimately returns empty, so the response "
                       f"was cut short; nothing was written",
        }
    said = _az_said(stdout, stderr)
    haystack = f"{stderr or ''}\n{stdout or ''}".lower()
    for fragment, refusal_code, remedy in AZ_STDERR_SIGNALS:
        if fragment in haystack:
            return {
                "code": refusal_code, "exit": 2, "action": action, "az": said,
                "message": f"azure-boards refused {action} — {remedy}; az said: {said}",
            }
    return {
        "code": "sp-az-api-error", "exit": 2, "action": action, "az": said, "azExit": code,
        "message": f"azure devops refused {action} — az said: {said}",
    }


def resolve_azure_project(cwd: str) -> tuple[tuple[str, str], dict]:
    """`(organization, project)` for this checkout, or a refusal.

    ASK `az` FOR ITS OWN DEFAULTS, the same reasoning `resolve_github_repo` applies to `gh`:
    the answer that matters is the one the CLI will actually use, and a human who ran
    `az devops configure --defaults organization=… project=…` has already stated it in the
    place `az` reads. Deriving it from a git remote instead would be a guess dressed as an
    answer — an Azure DevOps remote URL carries an organization and a REPOSITORY, and the
    repository is not the project.

    Never guesses. Missing defaults are a refusal naming the exact command that sets them,
    because a wrong answer here does not fail — it reads and writes somebody else's board."""
    declared, refusal = store.declared_store(cwd)
    if refusal:
        return ("", ""), refusal
    if "azure" in declared:
        return (declared["azure"]["organization"], declared["azure"]["project"]), {}
    code, out, err = _az_run(cwd, "devops", "configure", "--list")
    if code != 0:
        return ("", ""), az_refusal("resolving the organization and project", code, out, err)
    defaults = {}
    for line in (out or "").splitlines():
        key, sep, value = line.partition("=")
        if sep:
            defaults[key.strip().lower()] = value.strip()
    org, project = defaults.get("organization", ""), defaults.get("project", "")
    if not org or not project:
        missing = " and ".join(n for n, v in (("organization", org), ("project", project))
                               if not v)
        return ("", ""), {
            "code": "sp-az-no-project", "exit": 2, "missing": missing,
            "message": f"backend 'azure-boards' has no default {missing} — run "
                       f"`az devops configure --defaults organization=https://dev.azure.com/"
                       f"<org> project=<project>`; no spec was read or written",
        }
    # RESOLVED TO ITS NAME, never left as whatever `az devops configure` happens to hold.
    # Measured (task 6.3): a human's default is legitimately a project GUID — `--project`
    # routing accepts either — but `azure_query_wiql`'s `[System.TeamProject] = '<value>'`
    # compares against the field's own text value, which is always the NAME; a GUID there
    # answers `sp-az-api-error` ("not found in hierarchy") on every listing.
    #
    # CACHED, because this answer does not change. A project is renamed about as often as it
    # is created, and the call to ask cost 0,5s of `az` startup on EVERY process — measured
    # 2026-08-06 on a real organization, whose configured default is itself a GUID, which is exactly
    # the case a local shape test cannot shortcut. The cache is keyed on the RAW default, so
    # a human who runs `az devops configure --defaults project=…` is followed rather than
    # ignored: a changed default is a different key and resolves again.
    cached = azure_cache_read(org, project).get("projectName")
    if cached:
        return (org, cached), {}
    code, out, err = _az_run(cwd, "devops", "project", "show", "--project", project,
                             "--org", org, "--output", "json")
    if code != 0:
        return ("", ""), az_refusal("resolving the project's name", code, out, err)
    try:
        name = json.loads(out or "null").get("name") or project
    except json.JSONDecodeError:
        name = project
    azure_cache_write(org, project, projectName=name)
    return (org, name), {}


def azure_query_wiql(project: str, area_path: str | None, discovery_tag: str | None) -> str:
    """The WIQL this backend's listing runs.

    THE PROJECT IS NAMED LITERALLY, NEVER `@project`. Measured on this org (az 2.89.0 +
    azure-devops 1.0.6): the macro resolves to nothing and the query exits 0 with byte-empty
    stdout AND stderr — identical to a query that legitimately matches zero work items. There
    is no signal at the transport layer to tell the two apart, so the literal name is the
    only fix; a refusal keyed on empty output would refuse the ordinary "no specs yet" case
    just as often as the macro bug it was meant to catch.

    `area_path` and `discovery_tag` are optional here — they come from `azurePlacement` in
    `.agents/quenching.json`, which `open_azure_backend` reads and hands the backend. Unset,
    the query is scoped by project alone, which is the same breadth it always had."""
    clauses = [f"[System.TeamProject] = '{project}'"]
    if area_path:
        clauses.append(f"[System.AreaPath] = '{area_path}'")
    if discovery_tag:
        clauses.append(f"[System.Tags] CONTAINS '{discovery_tag}'")
    return f"SELECT [System.Id] FROM WorkItems WHERE {' AND '.join(clauses)}"


def azure_native_fields(fm: dict, discovery_tag: str | None,
                        rendered: list[str] | None = None) -> tuple[dict, str | None]:
    """`(the stored fields keyed by reference name, the assignee)` — the pure half of the
    consolidated write, so the one rule that matters most is checked without a live `az`: the
    discovery tag is ALWAYS in `System.Tags`, even where `fm` declares no tags at all, because
    `_native_fields` excludes it on read and a write that forgot it would silently drop the
    one thing that makes a spec findable again.

    `System.Tags` is ONE field carrying three classes, joined here and separated on read by
    `declared_tags`: what the spec declared (`fm['tags']`, storage), this backend's index
    (the discovery tag), and `rendered` — the `spec:` set `derive_labels` recomputes on every
    write. `fm['tags']` is filtered through `declared_tags` on the way in as well, so a
    reserved name that somehow reached the document cannot be written back as if the spec had
    declared it.

    THE DICT IS THE ONLY FORM. A sibling rendering these as `--fields KEY=VALUE` pairs
    survived this file for one task after `_apply_native_fields` was deleted, exercised by
    nothing but its own selftest — the spelling of a transport with no callers left. The
    values themselves are what the PATCH diffs against the item as read, so the dict is what
    both the builder and the check see."""
    fields: dict = {}
    all_tags = declared_tags(list(fm.get("tags") or []), discovery_tag)
    if discovery_tag and discovery_tag not in all_tags:
        all_tags.append(discovery_tag)
    for name in rendered or []:
        if name not in all_tags:
            all_tags.append(name)
    if all_tags:
        # SORTED, because the comparison is a set. Azure returns `System.Tags` in its own
        # order, so joining in insertion order made every write see a difference that was
        # only a permutation — measured live 2026-08-06.
        fields["System.Tags"] = "; ".join(sorted(all_tags))
    for key, ref in (("start", "Microsoft.VSTS.Scheduling.StartDate"),
                     ("target", "Microsoft.VSTS.Scheduling.TargetDate")):
        value = fm.get(key)
        if value:
            fields[ref] = value
    assignee = fm.get("assignee")
    return fields, (str(assignee) if assignee else None)


def azure_comparable_fields(fields: dict) -> dict:
    """The item as READ, rewritten into the shapes this backend WRITES — the only form a
    diff may compare against.

    TWO FIELDS COME BACK IN A SHAPE THAT CAN NEVER BE WRITTEN, both already measured and
    documented in `_native_fields`: `System.AssignedTo` arrives as an identity object and
    goes out as a UPN, and the two scheduling dates arrive as a full datetime
    (`2026-01-01T03:00:00Z`) and go out as a date. Diffing the raw read against what a write
    produces would find those three always different, so the patch would carry them on every
    single write — the exact no-op op the diff exists to remove, reintroduced as a silent
    default."""
    out = dict(fields)
    tags = out.get("System.Tags")
    if isinstance(tags, str):
        out["System.Tags"] = "; ".join(sorted(t.strip() for t in tags.split(";")
                                              if t.strip()))
    assigned = out.get("System.AssignedTo")
    if isinstance(assigned, dict):
        out["System.AssignedTo"] = assigned.get("uniqueName") or assigned.get("displayName")
    for ref in ("Microsoft.VSTS.Scheduling.StartDate",
                "Microsoft.VSTS.Scheduling.TargetDate"):
        value = out.get(ref)
        if isinstance(value, str) and len(value) > 10:
            out[ref] = value[:10]
    return out


# `System.Description`'s real ceiling — measured (task 6.2), not documented anywhere `az`
# prints: writing past it answers `TF401262: … exceeds the maximum allowed length of
# 1048576`. Checked in `_az_patch`, before the call is made.
AZ_DESCRIPTION_MAX = 1_048_576

# `workitemsbatch`'s own ceiling — measured against Microsoft's documented limit for the
# resource, not guessed. `_show_many` pays 1 + ⌈N/200⌉ calls for a listing instead of 1 + N.
AZ_BATCH_SIZE = 200
# The fields `_show_many` actually reads back, via `_field`: identity, title, state, the
# hybrid-wrapped document, tags (pulled forward at §2.6 for the untagged-marker sweep), and
# — §3.3 — the assignee and the two scheduling dates that round out the four stored fields.
AZ_BATCH_FIELDS = ("System.Id", "System.Title", "System.State", "System.Description",
                   "System.Tags", "System.AssignedTo",
                   "Microsoft.VSTS.Scheduling.StartDate",
                   "Microsoft.VSTS.Scheduling.TargetDate",
                   # Task 1.1, measured: asking for it costs +198 bytes on a 6-item batch
                   # (+0,8%) and removes `_apply_parent`'s whole `work-item show`. `$expand`
                   # would have brought it for free along with the board's column field, but
                   # it was rejected — the items come back carrying TWO
                   # `WEF_<guid>_Kanban.Column`, one per board, and picking between them by
                   # value is the guess §Placement forbids.
                   "System.Parent",
                   # Reaffirmed on every write, so read on every write too — measured live
                   # 2026-08-06: absent from this list, `current` answers None for both and
                   # the diff emits them on EVERY write, which is the no-op op it exists to
                   # remove.
                   "System.AreaPath", "System.IterationPath")

AZ_LEAN_BATCH_FIELDS = ("System.Id", "System.Title", "System.State", "System.Tags")


def azure_patch_body(current: dict, desired: dict, *, markdown: bool = False,
                     parent: tuple[int, str] | None = None) -> list[dict]:
    """The JSON patch ops one write sends, diffed against the item as it was READ.

    THE DIFF DOES NOT WEAKEN THE REAFFIRMATION `spec-backend.md` §Placement declares — it
    removes the ops that change nothing. A human who moved the card leaves `current` and
    `desired` disagreeing, the op is emitted, and the card comes back. What disappears is the
    revision an unchanged field used to bump on every `record` write.

    THE FORMAT OP IS COUPLED TO THE VALUE, never diffed on its own. Measured against the org:
    a patch carrying only `/multilineFieldsFormat/System.Description` answers `400 The type
    changed without a value`, and one whose value equals the stored text changes neither the
    revision nor the format. So `markdown` emits its op ONLY where the description itself is
    an op — which is exactly the case the diff can rule out.

    The parent travels as a relation rather than a field, and `parent` carries BOTH halves
    because they are not interchangeable: the id is what `System.Parent` is diffed against,
    the url is what the op must carry — a relation whose `url` is a bare id is rejected. That
    `System.Parent` is read back with the document is what spares this a `work-item show` of
    its own."""
    ops = [{"op": "add", "path": f"/fields/{key}", "value": value}
           for key, value in desired.items() if current.get(key) != value]
    if markdown and any(o["path"] == "/fields/System.Description" for o in ops):
        ops.append({"op": "add", "path": "/multilineFieldsFormat/System.Description",
                    "value": "Markdown"})
    if parent and int(current.get("System.Parent") or 0) != parent[0]:
        ops.append({"op": "add", "path": "/relations/-",
                    "value": {"rel": "System.LinkTypes.Hierarchy-Reverse",
                              "url": parent[1]}})
    return ops



AZ_PREDECESSOR_REL = "System.LinkTypes.Dependency-Reverse"
_AZ_URL_ID = re.compile(r"/(\d+)/?$")


def azure_predecessor_ids(fm: dict, own_id: int | None = None,
                          extra: list[int] | None = None) -> list[int]:
    """The work item ids a spec waits for: its `dependsOn`, plus the local members of its
    composition (`extra`), keeping only the all-digit ids (a work item of this store) and
    never its own. Declaration order, deduplicated."""
    ids: list[int] = []
    for dep in [str(d) for d in deps_of(fm)] + [str(x) for x in (extra or [])]:
        if dep.isdigit() and int(dep) != own_id and int(dep) not in ids:
            ids.append(int(dep))
    return ids


def composition_ids(info: dict) -> list[int]:
    """The local spec ids a document lists as members — the ones a work item of this store can
    link to as predecessors. A cross-repository member has no work item here."""
    from quenching.specs.parse.epics import is_remote, parse_items
    return [int(i["spec"]) for i in parse_items(info)
            if i["spec"] and str(i["spec"]).isdigit() and not is_remote(str(i["spec"]))]


def azure_predecessor_links(relations: list[dict]) -> dict[int, int]:
    """`{predecessor id: index in the item's relations}` for its predecessor links only.

    The id is read off the tail of the relation's url; any other relation type (parent,
    `ArtifactLink`) and any url that does not end in an id is not a predecessor and is left out."""
    links: dict[int, int] = {}
    for index, rel in enumerate(relations or []):
        if rel.get("rel") != AZ_PREDECESSOR_REL:
            continue
        m = _AZ_URL_ID.search(str(rel.get("url") or ""))
        if m:
            links.setdefault(int(m.group(1)), index)
    return links


def azure_predecessor_ops(relations: list[dict], wanted: list[int],
                          url_of) -> list[dict]:
    """The JSON patch ops that make the item's predecessor links equal `wanted`.

    DIFFED BY ID, never by position: a link already present emits nothing, so an unchanged
    set is an empty list. Removes come first and highest index first, so every index still
    names the relation it was read as; adds append with `/relations/-`."""
    linked = azure_predecessor_links(relations)
    ops = [{"op": "remove", "path": f"/relations/{index}"}
           for dep, index in sorted(linked.items(), key=lambda kv: -kv[1])
           if dep not in wanted]
    ops += [{"op": "add", "path": "/relations/-",
             "value": {"rel": AZ_PREDECESSOR_REL, "url": url_of(dep)}}
            for dep in wanted if dep not in linked]
    return ops


AZ_PATCH_TYPE_ERROR = "The type changed without a value"


def azure_patch_culprit(ops: list[dict], said: str) -> str | None:
    """Which op azure devops rejected, read off what it said — or None where it named none.

    ONLY VALUE-BEARING PATHS ARE CANDIDATES. `/relations/-` ends in `-`, which occurs in
    almost every sentence an API writes, so admitting every op here would attribute a field's
    rejection to the parent link at random.

    The type error is special-cased because it names no field at all, and it has exactly one
    cause: the format op travelled without its own value — the coupling `azure_patch_body`
    exists to hold. Seeing it means that coupling broke, and saying so beats a generic
    refusal that sends a human looking at the field."""
    if AZ_PATCH_TYPE_ERROR.lower() in said.lower():
        return next((o["path"] for o in ops
                     if o["path"].startswith("/multilineFieldsFormat/")), None)
    for op in ops:
        if not op["path"].startswith(("/fields/", "/multilineFieldsFormat/")):
            continue
        if op["path"].rsplit("/", 1)[-1] in said:
            return op["path"]
    return None


def azure_artifact_url(kind: str, project_id: str, repo_id: str, value: str) -> str:
    """The `vstfs:///Git/...` URI an `ArtifactLink` relation's `url` carries — Microsoft's own
    fixed encoding, `<projectId>%2F<repositoryId>%2F<value>`, one scheme per artifact kind.
    `kind='pr'` is deliberately not admitted here: the `PullRequestId` scheme names a Pull
    Request that is itself in Azure Repos, and this plugin's `pr`/`merge.pr` records always
    name a `github` one — not the same artifact, so not a mapping (## Design)."""
    scheme = {"branch": "Ref", "commit": "Commit"}[kind]
    tail = f"GB{value}" if kind == "branch" else value
    return f"vstfs:///Git/{scheme}/{project_id}%2F{repo_id}%2F{tail}"


# `attributes.name` per relation — MEASURED live (task 5.4) against a throwaway work item
# (a real organization and project): `rel: "ArtifactLink"` alone answers 400 "Artifact links
# must have a valid name specified", regardless of what the `url` scheme already says. The
# url disambiguates WHICH artifact; this name is Azure's own display label for the relation
# and is a second, independent requirement — read back and confirmed via `--expand relations`
# on the same probe.
AZURE_ARTIFACT_LINK_NAME = {"branch": "Branch", "commit": "Fixed in Commit"}


class AzureBoardsBackend(SpecBackend):
    """Specs as Azure Boards work items, reached through `az boards` in a subprocess.

    ONE WORK ITEM IS ONE SPEC, the whole document in `System.Description` — the same hybrid
    shape the `github` backend uses, through the same `hybrid_*` helpers, which is the point
    of those helpers having stopped being `gh_*`. Everything the two backends agree on is
    literally shared code rather than two implementations that must be kept in step.

    IT DERIVES NOTHING, exactly as `GitHubBackend` derives nothing: `derive_info` produces
    the stages, gates and records, and `parse_tasks` is the only thing that ever decides a
    task is checked or blocked.

    THE PHASE IS A DECLARED STATE, and this is the one place the two external backends
    genuinely differ. GitHub's open/closed is universal, so the mapping could be written in
    code. An Azure Boards state belongs to the project's process — Basic, Agile, Scrum and
    CMMI each name their states differently, and a customised process names them however it
    likes — so the mapping is read from `azureStates` in `.agents/quenching.json` and is
    NEVER guessed. Absent, the backend refuses (exit 2) naming the key: a guess would not
    fail loudly, it would silently report every archived spec as active.

    A state this tool did not write is read as `plans` — a work item moved to `Active` or
    `Resolved` by a human on the board is still in flight, and only the declared archive
    state means closed. That is the same one-way reading `github` gets from `state=closed`.

    `System.Tags` CARRIES THREE CLASSES AND KEEPS THEM DISJOINT. The spec's own declared
    tags are STORAGE, reassembled on read. The discovery tag is this backend's INDEX,
    re-added on every write. A `spec:` tag per present record, plus `spec:built` for
    `executing`, is a RENDERING of derived state — the same one `github` does with labels,
    through the same `derive_labels` and `reconcile_label_set`, over `;`-joined
    `System.Tags` rather than a `labels` array (no color or description here: Azure Boards
    tags carry neither). `declared_tags` is the one filter that separates the first from the
    other two on every read.

    THE SEVEN FRONTMATTER RECORDS STAY IN THE BODY, and the rendering is not a counterexample
    — it names that a record EXISTS, never the record's own fields. Multi-field records
    (`priority`, `branch`, `merge`, `refined`) have no honest single-tag form, and encoding
    `{level, criticality, complexity, date}` into a tag name would invent a second format
    only a new parser could read back — the backend deriving its own encoding exactly where
    the interface forbids it. A tag stores a tag, never a record; the rule survives native
    storage, it does not retire with it.

    The listing is fetched once per process and cached — a local cache and NOT a store:
    not authoritative, read by nothing outside this object, dropped on every write."""

    name = "azure-boards"

    def __init__(self, org: str, project: str, states: dict, cwd: str,
                area_path: str | None = None, discovery_tag: str | None = None,
                work_item_type: str | None = None, iteration_path: str | None = None,
                parent_id: int | None = None, team: str | None = None,
                board_column: str | None = None,
                repository: str | None = None,
                column_map: dict[str, str] | None = None,
                tag_catalog: dict[str, str] | None = None,
                types: dict[str, dict] | None = None) -> None:
        self.org = org
        self.project = project
        self.states = states
        self.cwd = cwd
        # `workItemTypes`, raw — `resolve_work_item_type` reads the whole entry shape
        # (`{description, azure, github, default}`), never a pre-filtered name-only map the
        # way `GitHubBackend.types` is, because the chain itself decides which entry wins.
        self.types = types or {}
        # `open_azure_backend` is the sole caller — it reads `azurePlacement`, refuses for the
        # one sub-key with no default (`areaPath`), and applies `AZ_DEFAULT_DISCOVERY_TAG` /
        # `AZ_SPEC_TYPE` for the two that have one. Optional here only so the fake and the
        # selftest fixtures can construct this backend without a config to read.
        self.area_path = area_path
        self.discovery_tag = discovery_tag
        self.work_item_type = work_item_type or AZ_SPEC_TYPE
        self.iteration_path = iteration_path
        # `subjects.<key>.parent` is what resolves this in practice: `open_azure_backend`
        # applies `defaultSubject`'s, and `cmd_new` overrides it for an explicit `--subject`.
        # `None` where neither declared one — a repository with no `subjects` links nothing.
        self.parent_id = parent_id
        self.team = team
        self.board_column = board_column
        # `azurePlacement.repository` — the Azure Repos repository name backing this target's
        # CODE, declared because §Design's own `resolve_azure_project` already established
        # that a board project and a code repository are independent facts here. `None` on
        # every target whose code is not in Azure Repos, which is the ordinary case — never a
        # refusal, only the absence of the branch/commit `ArtifactLink` below.
        self.repository = repository
        self._repo_ids: tuple[str, str] | None = None
        self.column_map = column_map or {}
        self.tag_catalog = tag_catalog or {}
        # WEF_<guid>_Kanban.Column, per work-item-type — a process resolving specs of more
        # than one type (§4.1) must not let one type's field answer for another's.
        self._board_field: dict[str, str] = {}
        # descriptor, id, shell doc, native title, the four stored fields, and the RAW
        # System.Tags the tag reconciliation in `write_spec` diffs against — the last costs
        # no call of its own, and is the one place tags are seen before `declared_tags`.
        self._rows: list[tuple[dict, int, str, str, dict, list[str]]] | None = None
        # item id -> its fields, already in the shapes THIS backend writes. Filled by
        # `_load`, read by `write_spec`'s diff, cleared by `_invalidate` with the rows
        # it belongs to — a stale entry would diff a write against the item as it was
        # two writes ago and elide an op that was still needed.
        self._raw: dict[int, dict] = {}
        # The `rev` each work item had when it was read: `write_spec` sends it back as a
        # JSON-Patch `test` op, so a write over a newer revision is refused by the server.
        self._revs: dict[int, int] = {}

    # -- transport ---------------------------------------------------------- #
    def _az_raw(self, action: str, *argv: str, expect: str = "object"):
        """One `az` call outside the `boards` command group, parsed. Raises `BackendRefusal`
        for every way it can fail — the shared half `_az` wraps with the `boards` prefix
        every other call in this backend uses. `workitemsbatch` (`_show_many`) is the one
        caller that needs `az devops invoke` instead.

        `--org` on every call rather than relying on the configured default, for the same
        reason `_az` does: resolution already read it once, and a human changing their `az`
        defaults mid-session must not silently redirect a write to another project.

        `expect='object'` (the default) refuses on exit 0 with empty stdout; `expect='array'`
        never does — see `az_refusal`."""
        code, out, err = _az_run(self.cwd, *argv, "--org", self.org, "--output", "json")
        if code != 0:
            raise BackendRefusal(az_refusal(action, code, out, err))
        if expect == "object" and not (out or "").strip():
            raise BackendRefusal(az_refusal(action, 0, out, err))
        try:
            return json.loads(out or "null")
        except json.JSONDecodeError as e:
            raise BackendRefusal({
                "code": "sp-az-bad-response", "exit": 2, "action": action,
                "message": f"`az` exited 0 while {action} but its output is not JSON: {e}",
            }) from e

    def _az(self, action: str, *argv: str, expect: str = "object"):
        """One `az boards` call — see `_az_raw`, which this delegates to with the `boards`
        prefix every call but the batch read shares."""
        return self._az_raw(action, "boards", *argv, expect=expect)

    def _field(self, item: dict, name: str) -> str:
        return str((item.get("fields") or {}).get(name, "") or "")

    def _phase_of(self, item: dict) -> str:
        return "archive" if self._field(item, "System.State") == self.states["archive"] \
            else "plans"

    def _raw_tags(self, item: dict) -> list[str]:
        """`System.Tags` split, RAW — the discovery tag and the `spec:` rendering included.
        Only the write path's reconciliation wants them this way; every reader goes through
        `declared_tags` over the result."""
        return [t.strip() for t in self._field(item, "System.Tags").split(";") if t.strip()]

    def _native_fields(self, item: dict) -> dict:
        """`tags`/`assignee`/`start`/`target`, reassembled from their native counterparts —
        the READ half of `## Design` §Stored is not projected. `System.AssignedTo` comes
        back as an identity object (`displayName`/`uniqueName`), never a plain string;
        `System.Tags` is `; `-joined. The two scheduling fields come back as a full datetime
        (`2026-01-01T03:00:00Z`); only the date is stored.

        `declared_tags` IS THE FILTER, and it removes two things rather than one: the
        discovery tag (this backend's index) and every `spec:` name (the rendering of derived
        state). Neither is part of what a spec declared, and reassembling either would make
        the next write reaffirm it as the document's own content.

        `uniqueName` (the UPN/e-mail), NEVER `displayName` — measured on this org (task 6.1):
        `--assigned-to` refuses a display name outright (`is an unknown identity`) and only
        resolves a UPN. Reading `displayName` back would read a value this same backend could
        never write again, failing the same-fact-read-back test `spec-backend.md` sets."""
        out: dict = {}
        tags = declared_tags(self._raw_tags(item), self.discovery_tag)
        if tags:
            out["tags"] = tags
        assigned = (item.get("fields") or {}).get("System.AssignedTo")
        if isinstance(assigned, dict):
            name = assigned.get("uniqueName") or assigned.get("displayName")
            if name:
                out["assignee"] = name
        elif isinstance(assigned, str) and assigned.strip():
            out["assignee"] = assigned.strip()
        for key, ref in (("start", "Microsoft.VSTS.Scheduling.StartDate"),
                         ("target", "Microsoft.VSTS.Scheduling.TargetDate")):
            value = self._field(item, ref)
            if value:
                out[key] = value[:10]
        return out

    def _guard_wiql(self, action: str, found: list) -> list:
        """Refuse a WIQL result that reached the API's id ceiling: it may be truncated, and a
        listing derived from a cut-short result would read the missing specs as absent."""
        if isinstance(found, list) and len(found) >= AZ_WIQL_LIMIT:
            raise BackendRefusal({
                "code": "sp-az-wiql-truncated", "exit": 2, "action": action,
                "limit": AZ_WIQL_LIMIT, "observed": len(found),
                "message": f"the WIQL query returned {len(found)} work items while {action}, "
                           f"the API's ceiling of {AZ_WIQL_LIMIT}; the listing may be "
                           f"truncated, so nothing was derived from it — narrow the area "
                           f"path or archive old work items",
            })
        return found

    # -- the listing, fetched once ------------------------------------------- #
    def _load(self) -> list[tuple[dict, int, str, str, dict, list[str]]]:
        if self._rows is not None:
            return self._rows
        # WIQL rather than a saved query: the filter is this tool's, not the project's, and
        # a saved query is one more thing a human has to create before the backend works.
        found = self._az("querying the project's work items", "query", "--project",
                         self.project, "--wiql",
                         azure_query_wiql(self.project, self.area_path, self.discovery_tag),
                         expect="array") or []
        self._guard_wiql("querying the project's work items", found)
        ids = [int(r.get("id") or (r.get("fields") or {}).get("System.Id") or 0)
               for r in found]
        wanted = [i for i in ids if i]
        items = self._show_many(wanted)
        missing = sorted(set(wanted) - {int(item.get("id") or 0) for item in items})
        if missing:
            raise BackendRefusal({
                "code": "sp-az-batch-short", "exit": 2, "missing": missing,
                "action": "reading the queried work items in batch",
                "message": f"the WIQL query named {len(wanted)} work item(s) but the batch "
                           f"read returned {len(items)}; missing ids {missing} — the listing "
                           f"was cut short, so nothing was derived from it",
                "remedy": "re-run the command — this is a transport fault, not a state",
            })
        rows = [row for row in (self._row_of(item) for item in items) if row]
        self._rows = rows
        return rows

    def _row_of(self, item: dict) -> tuple[dict, int, str, str, dict, list[str]] | None:
        """One work item as a listing row, or None where the marker says it is not a spec.

        The marker is what tells a spec apart from the project's real backlog, which this
        backend must never list and must never write over — so an ordinary work item a human
        created answers None here rather than being carried any further."""
        doc, parts = hybrid_unwrap(self._field(item, "System.Description"))
        if not parts:
            return None
        item_id = int(item.get("id") or 0)
        # KEYED BY ID, never a seventh slot on the row. The tuple is already six wide and its
        # last widening shipped a `list_specs` that unpacked five — every operation under this
        # backend died on it. One writer here, readers ask by id, and the unpack sites stay
        # exactly as they are.
        self._raw[item_id] = azure_comparable_fields(item.get("fields") or {})
        if item.get("rev") is not None:
            self._revs[item_id] = item["rev"]
        phase = self._phase_of(item)
        return ({
            "id": item_id, "phase": phase, "folder": phase, "legacy": False,
            "path": f"{self.org.rstrip('/')}/{self.project}/_workitems/edit/{item_id}",
        }, item_id, azure_restore_trailing_newline(doc),
            self._field(item, "System.Title"), self._native_fields(item),
            self._raw_tags(item))

    def _show_many(self, ids: list[int], lean: bool = False) -> list[dict]:
        """Every work item's requested fields, `AZ_BATCH_SIZE` ids per call via
        `az devops invoke --resource workitemsbatch` — 1 + ⌈N/200⌉ calls for a listing rather
        than 1 + N. `az boards work-item show` takes a single id and has no batch form; the
        REST resource does. The complete path asks for `System.Description`; `lean=True` asks
        only for the native index fields, so the WIQL query and the batch both avoid document
        bodies. The cost is still declared, not hidden: it is why the full listing stays cached
        for the whole process."""
        items: list[dict] = []
        for start in range(0, len(ids), AZ_BATCH_SIZE):
            chunk = ids[start:start + AZ_BATCH_SIZE]
            fd, path = tempfile.mkstemp(suffix=".json", prefix="quenching-az-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fields = list(AZ_LEAN_BATCH_FIELDS if lean else AZ_BATCH_FIELDS)
                    if not lean:
                        known = self._board_field_for_read()
                        if known:
                            fields.append(known)
                    json.dump({"ids": chunk, "fields": fields}, fh)
                result = self._az_raw(
                    f"reading {len(chunk)} work item(s) in batch", "devops", "invoke",
                    "--area", "wit", "--resource", "workitemsbatch",
                    "--route-parameters", f"project={self.project}",
                    "--http-method", "POST", "--in-file", path, "--api-version", "7.1")
            finally:
                os.unlink(path)
            items.extend((result or {}).get("value") or [])
        return items

    def _board_field_for_read(self) -> str | None:
        """The board's column field to ASK FOR in the batch — resolving it if that is what it
        takes, and answering None rather than refusing when it cannot be had.

        THE FIELD MUST BE IN THE READ OR THE COLUMN CANNOT BE DIFFED, and a column that
        cannot be diffed is written blind. Measured 2026-08-07 against the real org: on a
        cold cache the item came back without the column, `current` answered None for it, and
        the write went out carrying one op that set the column to the value it already
        held — a seventh `az` call, and a REQUEST rather than a read. Resolving here instead
        makes it six, and the pointless write disappears.

        THE TWO CALLS ARE NOT NEW SPEND FOR A WRITE — `write_spec` resolves the same field a
        moment later either way; this only moves them ahead of the batch, which is the one
        ordering where their answer is still useful. A read that never writes does pay them,
        once ever: the answer is cached across processes, so the whole cost is one cold run
        per organisation, project, team and work item type.

        ONLY WHERE A COLUMN IS DECLARED. With neither `azureColumns` nor `boardColumn` no
        column op is ever emitted, so there is nothing to diff and nothing worth resolving.

        AND IT NEVER REFUSES. `sp-az-no-team` and `sp-az-no-board` belong to the write, which
        calls `_resolve_board_field` for real and raises there with the same message. An
        optimisation that turned a listing into a refusal would be a behaviour change wearing
        a performance fix's clothes.

        RESOLVED AGAINST `self.work_item_type` — the repo's own default, never one spec's
        own resolved type: a batch covers every spec the listing returns, of whatever type
        each was born under, and this is a read-ahead guess for the FIELD NAME to request,
        not an authoritative per-item answer. A guess that misses for an item born under a
        different type costs one write-time resolution later; it never writes anything
        wrong, because `write_spec`/`create_spec`/`move_spec` resolve their own field for
        their own spec regardless of what this returned."""
        if self.work_item_type in self._board_field:
            return self._board_field[self.work_item_type]
        if not (self.column_map or self.board_column):
            return None
        try:
            return self._resolve_board_field(self.work_item_type)
        except BackendRefusal:
            return None

    def _invalidate(self) -> None:
        self._rows = None
        self._raw = {}
        self._revs = {}

    def _item_id(self, spec_id: str | int) -> int:
        return int(spec_id)

    def marker_without_discovery_tag(self) -> list[dict]:
        """Items carrying the spec marker in their description but NOT the discovery tag —
        invisible to `_load()`'s tag-scoped listing, and the exact price `## Design` §A
        descoberta accepts for making the tag an index rather than an authority: a human who
        untags a card makes it vanish from `list`/`status`/every other command, silently.
        `doctor`'s `sp-az-marker-untagged` is the one place that still finds it — a sweep
        scoped by area alone, without the tag."""
        found = self._az("querying the project's work items (untagged sweep)", "query",
                         "--project", self.project, "--wiql",
                         azure_query_wiql(self.project, self.area_path, None),
                         expect="array") or []
        ids = [int(r.get("id") or 0) for r in found if r.get("id")]
        out: list[dict] = []
        for item in self._show_many(ids):
            _, parts = hybrid_unwrap(self._field(item, "System.Description"))
            if not parts:
                continue
            tags = self._raw_tags(item)
            if self.discovery_tag and self.discovery_tag not in tags:
                out.append({"id": int(item.get("id") or 0)})
        return out

    def board_findings(self) -> list[dict]:
        """Three per-spec doctor findings, over the tag-scoped listing only (not the whole
        area — `marker_without_discovery_tag` already covers what that misses): a board
        column that disagrees with the de-para, a tag outside the declared catalog, and a
        spec past the captured stage with no `start`/`target`.

        One `work-item show` per spec, not per finding — doctor is not a build-loop hot
        path, and `work-item show` returns every field, including the dynamic board-column
        one `workitemsbatch` would need asked for by name."""
        out: list[dict] = []
        for row in self.list_specs():
            info, rerr = self.read_spec(row["id"])
            if rerr or info is None:
                continue
            item_id = self._item_id(row["id"])
            item = self._az(f"reading work item {item_id} for doctor", "work-item", "show",
                            "--id", str(item_id))
            state = board_state_of(info)
            expected = self.column_map.get(state, self.board_column)
            if expected:
                type_name = resolve_work_item_type({"workItemTypes": self.types},
                                                   info["frontmatter"].get("workItemType"))
                actual = self._field(item, self._resolve_board_field(type_name))
                if actual and actual != expected:
                    out.append({"kind": "column", "id": item_id,
                               "actual": actual, "expected": expected})
            # `declared_tags`, never the raw set: the discovery tag and the `spec:`
            # rendering are the TOOL's, so a catalogue that never lists them is complete,
            # not lacking — flagging them would make this finding fire on every spec.
            tags = declared_tags(self._raw_tags(item), self.discovery_tag)
            for tag in tags_outside_catalog(tags, self.tag_catalog):
                out.append({"kind": "tag", "id": item_id, "tag": tag})
            # Past `captured` (Backlog) is where the team's own rule ("a partir do
            # Entendimento Técnico, as datas são obrigatórias") starts applying —
            # `## Out of Scope` keeps this a finding, never a refusal.
            if state != "captured" and not (info["frontmatter"].get("start")
                                            and info["frontmatter"].get("target")):
                out.append({"kind": "dates", "id": item_id})
        return out

    # -- the five primitives -------------------------------------------------- #
    def list_specs(self, phase: str | None = None, lean: bool = False) -> list[dict]:
        if lean:
            found = self._az("querying the project's work items (lean index)", "query",
                             "--project", self.project, "--wiql",
                             azure_query_wiql(self.project, self.area_path, self.discovery_tag),
                             expect="array") or []
            self._guard_wiql("querying the project's work items (lean index)", found)
            ids = [int(r.get("id") or (r.get("fields") or {}).get("System.Id") or 0)
                   for r in found]
            rows = []
            for item in self._show_many([i for i in ids if i], lean=True):
                item_id = int(item.get("id") or 0)
                if not item_id:
                    continue
                state = self._field(item, "System.State")
                item_phase = self._phase_of(item)
                rows.append({
                    "id": item_id, "title": self._field(item, "System.Title"),
                    "state": state, "records": [tag for tag in self._raw_tags(item)
                                                   if tag.startswith("spec:")],
                    "phase": item_phase, "folder": item_phase, "legacy": False,
                    "path": f"{self.org.rstrip('/')}/{self.project}/_workitems/edit/{item_id}",
                })
            return sorted([row for row in rows
                           if phase is None or row["phase"] == phase],
                          key=lambda r: (PHASES.index(r["phase"]), r["id"]))
        rows = [dict(d) for d, _, _, _, _, _ in self._load()
                if phase is None or d["phase"] == phase]
        return sorted(rows, key=lambda r: (PHASES.index(r["phase"]), r["id"]))

    def revisions(self) -> dict[int, int]:
        """`id -> rev` of every work item the listing names, with no document body fetched.

        The `rev` is a top-level field of a work item, so the lean batch already carries it.
        The sync cache diffs this against what it holds; an item that is not a spec is
        included, because the lean batch cannot tell, and the cache drops it on its refetch."""
        found = self._az("querying the project's work items (revisions)", "query",
                         "--project", self.project, "--wiql",
                         azure_query_wiql(self.project, self.area_path, self.discovery_tag),
                         expect="array") or []
        self._guard_wiql("querying the project's work items (revisions)", found)
        ids = [int(r.get("id") or (r.get("fields") or {}).get("System.Id") or 0)
               for r in found]
        return {int(item["id"]): int(item.get("rev") or 0)
                for item in self._show_many([i for i in ids if i], lean=True)
                if item.get("id")}

    def read_spec(self, spec_id: str | int) -> tuple[dict | None, dict]:
        """One spec, by the work-item ID the board allocated.

        The same two paths `github`'s own `read_spec` documents, for the same reason: a
        direct `workitemsbatch` for one item when nothing has been loaded — which is what
        the native ID buys — and the row `_load` already holds when a listing in this same
        process paid for it. `doctor`'s `board_findings` reads every spec straight after
        `list_specs`, and would otherwise pay a request per spec for documents it had."""
        try:
            item_id = int(spec_id)
        except (TypeError, ValueError):
            return None, {"code": "sp-unknown-id", "exit": 1, "id": spec_id,
                          "message": f"no spec with id '{spec_id}'"}
        row = None
        if self._rows is not None:
            row = next((r for r in self._rows if r[1] == item_id), None)
        if row is None:
            items = self._show_many([item_id])
            row = self._row_of(items[0]) if items else None
        if not row:
            return None, {"code": "sp-unknown-id", "exit": 1, "id": spec_id,
                          "message": f"no spec with id '{spec_id}'"}
        spec, full_text, native_title, native_fields = (dict(row[0]), row[2], row[3], row[4])
        info = derive_info(spec, hybrid_title_join(full_text, native_title))
        # REASSEMBLED, not re-parsed: the stored document never carries these four keys
        # (`write_spec` strips them — §Stored is not projected), so the native fields ARE
        # the only copy, and they win outright over whatever the raw text happened to say.
        info["frontmatter"].update(native_fields)
        if item_id in self._revs:
            info["_azure_rev"] = self._revs[item_id]
        return info, {}

    def write_spec(self, info: dict, text: str) -> None:
        announce_unproved(self.name)
        text = guard_store_write(text, self.cwd, info.get("text"), BackendRefusal)
        item_id = self._item_id(info["id"])
        # FRESH, off the text THIS write is putting in place — every caller (`cmd_record`,
        # `cmd_field`, `cmd_promote`...) hands `write_spec` the OLD `info` beside the NEW
        # `text`, so trusting `info["frontmatter"]` here would read the state a write is
        # REPLACING rather than the one it is creating. The board column (§2.11) had exactly
        # this bug from §2.5 until this task — the derivation was cheap and nothing forced
        # it to be re-run. It is also what `derive_labels` renders from, the SAME shared
        # calculation `github` reconciles its labels with.
        fresh = derive_info(info, text)
        # `System.Description` has no published ceiling, so `hybrid_split` is handed None
        # and answers with the one chunk that is the whole document. The call is made anyway,
        # rather than skipped, so this backend goes through the SAME serialisation as the
        # proved one instead of a shorter path of its own that nothing checks.
        stripped = strip_frontmatter_keys(text, FIELD_KEYS)
        stored, title = hybrid_project(stripped)
        chunks = hybrid_split(stored, None)
        # Placement is REAFFIRMED here, not just declared at creation — a human moving the
        # work item to another area between writes sees the next one bring it back, the same
        # way `move_spec` already owns the state. Only declared fields reach `desired`, so
        # an unset `iteration_path` is simply not one of them.
        desired = {"System.Title": title,
                   "System.Description": hybrid_wrap(chunks[0][0], fmt="div")}
        if self.area_path:
            desired["System.AreaPath"] = self.area_path
        if self.iteration_path:
            desired["System.IterationPath"] = self.iteration_path
        # CARRIED FORWARD, not read off `fresh` alone: `strip_frontmatter_keys` means an
        # ORDINARY write — a section edit, a ticked task — hands this method text that never
        # mentions `tags`/`assignee`/`start`/`target` at all, because they were never in the
        # document to begin with. Reading that silence as "clear them" would wipe every
        # stored field on the next unrelated write; `carry_forward_fields` keeps `info`'s
        # (the pre-write read's) values unless THIS write's own text set one explicitly.
        #
        # The RENDERED half rides the same call: `derive_labels` off the fresh document, into
        # the one `System.Tags` value `azure_native_fields` already builds. It is not carried
        # forward and never diffed against what the item holds — a rendering is recomputed
        # from the source on every write by definition.
        native, assignee = azure_native_fields(
            carry_forward_fields(info["frontmatter"], fresh["frontmatter"], FIELD_KEYS),
            self.discovery_tag, derive_labels(fresh))
        desired.update(native)
        if assignee:
            desired["System.AssignedTo"] = assignee
        # The column rides the SAME body, which is what kept `azureStates` and `azureColumns`
        # from undoing each other before: the board resolves `System.State` from the column,
        # and one patch cannot race itself.
        column = self.column_map.get(board_state_of(fresh), self.board_column)
        if column:
            type_name = resolve_work_item_type({"workItemTypes": self.types},
                                               fresh["frontmatter"].get("workItemType"))
            desired[self._resolve_board_field(type_name)] = column
        # `markdown=True` on every write, not once at creation: the format op only travels
        # where the description is itself an op, so an item already in `Markdown` pays
        # nothing and one still in `html` is converted by the first write that touches it.
        # The parent rides the same body: `System.Parent` comes back with the document, so
        # the link is REAFFIRMED by diff — already linked emits nothing, unlinked emits one
        # relation op — where it used to cost a `work-item show` of its own on every write.
        parent = (self.parent_id, self._work_item_url(self.parent_id)) \
            if self.parent_id else None
        ops = azure_patch_body(self._raw.get(item_id, {}), desired, markdown=True,
                               parent=parent)
        # PREDECESSOR LINKS ride the document's own patch, so the `rev` test below guards both
        # and the write stays all-or-nothing. Read only when `dependsOn` really changed (an
        # internal diff against the pre-write `info`), so every other write pays no extra call.
        wanted = azure_predecessor_ids(fresh["frontmatter"], item_id,
                                       composition_ids(fresh))
        if wanted != azure_predecessor_ids(info["frontmatter"], item_id,
                                           composition_ids(info)):
            ops += azure_predecessor_ops(self._relations_of(item_id), wanted,
                                         self._work_item_url)
        if ops:
            rev = info.get("_azure_rev", self._revs.get(item_id))
            self._az_patch(f"updating work item {item_id}", item_id, ops, rev=rev)
        if self.repository:
            self._link_new_artifacts(item_id, info, fresh)
        self._invalidate()

    def _relations_of(self, item_id: int) -> list[dict]:
        """The item's relations, one `work-item show --expand relations`."""
        item = self._az(f"reading the relations of work item {item_id}", "work-item", "show",
                        "--id", str(item_id), "--expand", "relations")
        return list((item or {}).get("relations") or [])

    def predecessor_links(self, spec_id: str | int) -> list[int]:
        """The sorted ids this spec's work item is linked to as a predecessor."""
        return sorted(azure_predecessor_links(self._relations_of(self._item_id(spec_id))))

    def _link_new_artifacts(self, item_id: int, old_info: dict, fresh: dict) -> None:
        """`branch`/task-commit `ArtifactLink`s, attempted only for what THIS write actually
        changed — an internal diff against `old_info` (the pre-write read every caller already
        hands `write_spec`), never a second Azure read. §Design: a relation has no field this
        backend already reads back, so the alternative would be `$expand=relations` on every
        load; comparing the document to itself is what makes one attempt per real change cost
        nothing on every OTHER write."""
        old_branch = (old_info["frontmatter"].get("branch") or {}).get("work")
        new_branch = (fresh["frontmatter"].get("branch") or {}).get("work")
        if new_branch and new_branch != old_branch:
            self._link_artifact(item_id, "branch", new_branch)
        old_subjects = {t.get("id"): t.get("subject") for t in old_info["tasks"]}
        for task in fresh["tasks"]:
            subject = task.get("subject")
            if not subject or subject == old_subjects.get(task.get("id")):
                continue
            sha = _git(os.getcwd(), "log", "--fixed-strings", "--grep", subject,
                      "--format=%H", "-1").strip()
            if sha:
                self._link_artifact(item_id, "commit", sha)

    def _resolve_repo_ids(self) -> tuple[str, str]:
        """`(projectId, repositoryId)` for `self.repository` — cached the same way the
        project's own name already is (§Granular reading), keyed under it so more than one
        repository in the same project resolves independently."""
        cached = (azure_cache_read(self.org, self.project).get("repos") or {}).get(
            self.repository)
        if cached:
            return cached["projectId"], cached["repositoryId"]
        repo = self._az_raw("resolving `azurePlacement.repository`", "repos", "show",
                            "--repository", self.repository, "--project", self.project)
        project_id = ((repo or {}).get("project") or {}).get("id")
        repo_id = (repo or {}).get("id")
        if not (project_id and repo_id):
            raise BackendRefusal({
                "code": "sp-az-repo-unresolved", "exit": 2, "repository": self.repository,
                "message": f"`azurePlacement.repository` ('{self.repository}') does not "
                           f"resolve against project '{self.project}' — no native link was "
                           f"attempted; the record itself is already saved",
            })
        repos = azure_cache_read(self.org, self.project).get("repos") or {}
        repos[self.repository] = {"projectId": project_id, "repositoryId": repo_id}
        azure_cache_write(self.org, self.project, repos=repos)
        return project_id, repo_id

    def _link_artifact(self, item_id: int, kind: str, value: str) -> None:
        """Add ONE `ArtifactLink` relation, in its OWN `az rest` call — never folded into the
        document PATCH above. Raises on refusal, the same shape `GitHubBackend._set_type`
        already uses for its own secondary, best-effort write: the document already saved
        successfully by the time this runs, so a refusal here is reported loudly rather than
        swallowed, and never undoes what already landed."""
        project_id, repo_id = self._resolve_repo_ids()
        url = azure_artifact_url(kind, project_id, repo_id, value)
        self._az_patch(f"linking {kind} '{value}' to work item {item_id}", item_id,
                       [{"op": "add", "path": "/relations/-",
                         "value": {"rel": "ArtifactLink", "url": url,
                                   "attributes": {"name": AZURE_ARTIFACT_LINK_NAME[kind]}}}])

    def create_spec(self, phase: str, text: str) -> str:
        announce_unproved(self.name)
        text = guard_store_write(text, self.cwd, None, BackendRefusal)
        # `derive_info` off a minimal descriptor — `create_spec` never receives one, and
        # `board_state_of` reads only `phase`/`frontmatter`/`stage`, all of which come back
        # from the text just handed to `az`.
        fresh = derive_info({"phase": phase}, text)
        stripped = strip_frontmatter_keys(text, FIELD_KEYS)
        stored, title = hybrid_project(stripped)
        # NO `--state`: measured (task 6.3), `az boards work-item create` has no such flag —
        # only `update` does. A new item is born in whatever state its TYPE defaults to
        # ("New", typically) — left alone, because the column op below is what actually
        # decides it (§task 6.3: column and state are not independent on this process; the
        # board resolves state FROM the column, and a direct `--state` write the column
        # write follows would just be undone).
        # The chain a spec's own `workItemType:` resolves through — never `self.work_item_type`,
        # the single per-repo default only `_board_field_for_read`'s read-ahead optimisation
        # still uses. `workItemTypes` describes the PROJECT, not one backend, so `self.types`
        # carries the whole catalogue and this asks the same pure function `create_spec`'s
        # own `github` sibling type-checks against.
        type_name = resolve_work_item_type({"workItemTypes": self.types},
                                           fresh["frontmatter"].get("workItemType"))
        argv = ["work-item", "create", "--project", self.project,
               "--type", type_name, f"--title={title}"]
        if self.area_path:
            argv += ["--area", self.area_path]
        if self.iteration_path:
            argv += ["--iteration", self.iteration_path]
        # THE DOCUMENT DOES NOT TRAVEL ON THE CREATE CALL, and that is what makes a spec
        # born in `Markdown` rather than converted by whichever later write first happens to
        # touch its text. The format op only rides alongside a `System.Description` op —
        # measured: a patch carrying the format alone answers `400 The type changed without
        # a value` — so the description has to be IN the patch for the item to ever leave
        # `html`. It costs no extra call: the placement patch below was being sent anyway.
        item = self._az("creating a work item", *argv)
        item_id = int((item or {}).get("id") or 0)
        # ONE PATCH for everything the creation could not carry. The item was born this
        # instant, so `current` is `{}` by construction and every op below is genuinely new —
        # and the parent needs no read to know it is unlinked, which is the one place
        # `_apply_parent`'s round trip is provably unnecessary rather than merely cached.
        #
        # THE STATE FALLBACK IS DECIDED BEFORE SENDING, never as a second write. No column
        # applicable (`azureColumns` AND `boardColumn` both absent) is the one case where the
        # state has to be forced directly; with a column, forcing it too would be undone by
        # the board resolving state FROM the column (§task 6.3).
        #
        # The discovery tag rides here, and it is not optional: skip it and the spec is
        # invisible to `_load()`'s tag-scoped listing the instant it exists — never found
        # again by slug, by `list`, by anything.
        desired, assignee = azure_native_fields(fresh["frontmatter"], self.discovery_tag,
                                                derive_labels(fresh))
        desired["System.Description"] = hybrid_wrap(
            hybrid_split(stored, None)[0][0], fmt="div")
        if assignee:
            desired["System.AssignedTo"] = assignee
        column = self.column_map.get(board_state_of(fresh), self.board_column)
        if column:
            # The same `type_name` already resolved above for `--type` — one spec, one type,
            # asked once.
            desired[self._resolve_board_field(type_name)] = column
        else:
            desired["System.State"] = self.states[phase]
        parent = (self.parent_id, self._work_item_url(self.parent_id)) \
            if self.parent_id else None
        ops = azure_patch_body({}, desired, markdown=True, parent=parent)
        # A new item has no relations to read: every wanted predecessor is an add.
        ops += azure_predecessor_ops([], azure_predecessor_ids(fresh["frontmatter"], item_id,
                                                              composition_ids(fresh)),
                                     self._work_item_url)
        if ops:
            self._az_patch(f"writing the new work item {item_id}", item_id, ops)
        self._invalidate()
        return f"{self.org.rstrip('/')}/{self.project}/_workitems/edit/{item_id}"

    def _work_item_url(self, item_id: int) -> str:
        """The REST identity of one work item — what a `/relations/-` op must carry.

        NOT THE LOCATOR. `_apis/wit/workItems/<id>` is the API's spelling and is rejected by
        a browser; `_workitems/edit/<id>` is what `_load` puts in `path` and what every
        report prints. A relation handed the browser form is refused, so the two are built
        separately rather than one being derived from the other."""
        return f"{self.org.rstrip('/')}/{self.project}/_apis/wit/workItems/{item_id}"

    def _resolve_board_field(self, type_name: str) -> str:
        """The team's Kanban column field — `WEF_<guid>_Kanban.Column` — resolved once per
        process and cached on `self`. `spec-backend.md` §Granular reading already allows a
        process-local cache that is not a store: it is not authoritative and nothing outside
        this object reads it.

        `type_name` IS THE SPEC'S OWN RESOLVED TYPE, never `self.work_item_type` — a board's
        allowed mappings are per work-item-type, so a spec born under a different type from
        the repo's own default must resolve against its OWN, not the one every caller used
        to share (§4.1's `create_spec` is why one can differ at all).

        FOUND, NEVER GUESSED: the guid is per-TEAM, so a second team's board carries a
        different one. This asks the team for every board it has and keeps the one whose
        `allowedMappings` names `type_name` — measured on a real org, a team
        has six boards (Stories, OKR, Releases, Funcionalidades, Iniciativas, Épicos)
        and 'User Story' resolves to 'Stories'."""
        if type_name in self._board_field:
            return self._board_field[type_name]
        # BETWEEN PROCESSES, not just within one. The guid is per-team and per-process
        # caching meant paying 1 + N calls on every pre-refactor specs script invocation — measured 2,8s on
        # a team with six boards. Task 1.1 ruled out reading it off the item itself, so the
        # resolution stays authoritative and only its ANSWER is remembered, keyed by the team
        # and work item type it was resolved for.
        cache_key = f"boardField:{self.team}:{type_name}"
        cached = azure_cache_read(self.org, self.project).get(cache_key)
        if cached:
            self._board_field[type_name] = cached
            return cached
        if not self.team:
            raise BackendRefusal({
                "code": "sp-az-no-team", "exit": 2,
                "message": "backend 'azure-boards' needs `team` declared in "
                           f"{CONFIG_FILE}'s `azurePlacement` to resolve the board's column "
                           "field — a board belongs to a team, and this tool never guesses "
                           "which one. No spec was read or written",
            })
        listing = self._az_raw("listing the team's boards", "devops", "invoke",
                               "--area", "work", "--resource", "boards",
                               "--route-parameters", f"project={self.project}",
                               f"team={self.team}")
        for row in (listing or {}).get("value") or []:
            board_id = row.get("id")
            if not board_id:
                continue
            detail = self._az_raw(f"reading board '{row.get('name')}'", "devops", "invoke",
                                  "--area", "work", "--resource", "boards",
                                  "--route-parameters", f"project={self.project}",
                                  f"team={self.team}", f"id={board_id}")
            mappings = (detail or {}).get("allowedMappings") or {}
            if any(type_name in m for m in mappings.values()):
                field = ((detail.get("fields") or {}).get("columnField") or {}).get(
                    "referenceName")
                if field:
                    self._board_field[type_name] = field
                    azure_cache_write(self.org, self.project, **{cache_key: field})
                    return field
        raise BackendRefusal({
            "code": "sp-az-no-board", "exit": 2,
            "message": f"no board for team '{self.team}' accepts work item type "
                       f"'{type_name}' — check azurePlacement.team and "
                       f"workItemType; no spec was read or written",
        })

    def move_spec(self, info: dict, dest_phase: str) -> str:
        announce_unproved(self.name)
        item_id = self._item_id(info["id"])
        # The column is what actually moves a card between phases in the human's own vocabulary
        # (Kanban.Column), and applying it is what the board itself resolves `System.State`
        # from — never the reverse. `states[dest_phase]` is the fallback for a repo whose
        # `azureColumns`/`boardColumn` cannot answer for this phase at all.
        dest_info = dict(info)
        dest_info["phase"] = dest_phase
        column = self.column_map.get(board_state_of(dest_info), self.board_column)
        if column:
            type_name = resolve_work_item_type({"workItemTypes": self.types},
                                               info["frontmatter"].get("workItemType"))
            desired = {self._resolve_board_field(type_name): column}
        else:
            desired = {"System.State": self.states[dest_phase]}
        ops = azure_patch_body(self._raw.get(item_id, {}), desired)
        if ops:
            self._az_patch(f"moving work item {item_id} to {dest_phase}", item_id, ops)
        self._invalidate()
        return f"{self.org.rstrip('/')}/{self.project}/_workitems/edit/{item_id}"

    def _az_patch(self, action: str, item_id: int, ops: list[dict], rev: int | None = None):
        """One work item, one JSON patch — the whole write in a single `az` process.

        `az rest`, and NOT `az devops invoke` — measured, not preferred. The extension routes
        by resource NAME, and `workitems` resolves to the CREATE route (`/workitems/${type}`);
        asked to PATCH an id it dies inside msrest with `KeyError: 'type'`, a traceback rather
        than a refusal. `az rest` addresses the endpoint directly, at the price of naming
        Azure DevOps' resource id so the token is issued for the right audience.

        `Content-Type: application/json-patch+json` is not optional — the work item PATCH
        endpoint rejects the default `application/json` outright.

        THE CEILING IS CHECKED HERE, and here is now the only door a document goes through.
        The `--fields System.Description=@<path>` mechanism that used to be that door was
        deleted with this method's arrival, along with the two ceilings it had to explain —
        Linux's own `MAX_ARG_STRLEN` no longer applies to a body that travels as a file. The
        measured `System.Description` limit still does (`TF401262` above it), and naming the
        size before the call beats surfacing that error anonymously mid-write.

        ONE PATCH IS ALL-OR-NOTHING, so the refusal names the op. `## Risks` accepts the
        atomicity — a rejected op takes the document edit down with it — on the condition
        that a human is told WHICH one; a patch of eight ops that fails without saying is
        worse to diagnose than the four separate writes it replaced."""
        for op in ops:
            value = op.get("value")
            if isinstance(value, str) and len(value) > AZ_DESCRIPTION_MAX:
                raise BackendRefusal({
                    "code": "sp-az-description-too-large", "exit": 2, "action": action,
                    "size": len(value), "max": AZ_DESCRIPTION_MAX, "op": op["path"],
                    "message": f"the value for {op['path']} is {len(value)} characters and "
                               f"the field holds {AZ_DESCRIPTION_MAX} (measured: `az` "
                               f"answers TF401262 above it) — shorten a section while "
                               f"{action}; nothing was written",
                })
        # COMPARE-AND-SWAP: a leading `test /rev` op makes the server refuse the whole patch
        # when the item moved on after it was read. Never sent for creations or links, which
        # have no revision they could be stale against.
        sent = [{"op": "test", "path": "/rev", "value": rev}, *ops] if rev is not None else ops
        fd, path = tempfile.mkstemp(suffix=".json", prefix="quenching-az-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(sent, fh)
            from urllib.parse import quote
            uri = (f"{self.org.rstrip('/')}/{quote(self.project)}/_apis/wit/workitems/"
                   f"{item_id}?api-version=7.1")
            code, out, err = _az_run(
                self.cwd, "rest", "--method", "PATCH", "--uri", uri,
                "--resource", AZ_DEVOPS_RESOURCE_ID,
                "--headers", "Content-Type=application/json-patch+json",
                "--body", f"@{path}")
            if code != 0:
                if rev is not None and azure_is_stale_error(out, err):
                    raise BackendRefusal(azure_stale_write_refusal(item_id, rev, action))
                raise BackendRefusal(az_refusal(action, code, out, err))
            try:
                return json.loads(out or "null")
            except json.JSONDecodeError as e:
                raise BackendRefusal({
                    "code": "sp-az-bad-response", "exit": 2, "action": action,
                    "message": f"`az rest` exited 0 while {action} but its output is not "
                               f"JSON: {e}",
                }) from e
        except BackendRefusal as e:
            e.err["ops"] = [op["path"] for op in ops]
            culprit = azure_patch_culprit(ops, str(e.err.get("az") or ""))
            if culprit:
                e.err["op"] = culprit
                e.err["message"] = (f"{e.err.get('message', '')} — the op azure devops "
                                    f"rejected is {culprit}; the whole patch was refused, so "
                                    f"nothing was written")
            raise
        finally:
            os.unlink(path)


AZ_STALE_FRAGMENTS = ("tf26071", "test operation", "has been changed by someone else",
                      "jsonpatchtest", "/rev")


def azure_is_stale_error(stdout: str, stderr: str) -> bool:
    """Whether `az` said the patch's `test /rev` op failed, i.e. the item changed after it
    was read."""
    text = f"{stderr or ''}\n{stdout or ''}".lower()
    return any(fragment in text for fragment in AZ_STALE_FRAGMENTS)


def azure_stale_write_refusal(item_id: int, expected_rev: int, action: str) -> dict:
    """Refuse a write whose work item moved to a newer revision after the caller read it."""
    return {
        "code": "sp-az-stale-write", "exit": 2, "item": item_id, "expectedRev": expected_rev,
        "action": action,
        "message": f"work item {item_id} changed after it was read — expected revision "
                   f"{expected_rev} while {action}; re-read the spec and reapply the change "
                   f"instead of merging two documents automatically; nothing was written",
        "remedy": "re-read the spec and reapply the change",
    }


def azure_restore_trailing_newline(doc: str) -> str:
    """Undo the one transport artefact `az`'s `@file` field mechanism leaves on every read:
    measured (task 6.2), it strips EVERY trailing newline unconditionally — `"x\\n"`,
    `"x\\n\\n\\n"` and `"x"` all read back as `"x"`, so whether the document ended in a
    newline cannot survive the round trip in either direction. Every document this tool ever
    writes ends in exactly one (`TEMPLATE_SPEC`, `capture_form`, every section writer) —
    restoring it here is not a guess, it is undoing a transport artefact, on the one backend
    whose transport has it.

    THAT ARTEFACT IS HISTORY, AND THIS IS NOW COMPATIBILITY RATHER THAN A LIVE CORRECTION.
    `@file` was the WRITE mechanism, deleted with `_az_patch`'s arrival; a document now
    leaves as JSON in an `az rest` body and comes back as JSON from `workitemsbatch`, and
    neither touches the trailing newline. Measured 2026-08-07 against the real org: the same
    section written back verbatim twice sent a patch the FIRST time — `System.Description`
    still stored in the stripped form the old transport left — and, the second time, no
    patch at all. So the stored value converges after one write, and this function's job is
    documents written before that change. It stays because it is a no-op on an
    already-terminated document, and because nothing migrates the ones already stored."""
    if doc and not doc.endswith("\n"):
        return doc + "\n"
    return doc


# `workItemType`'s default. Measured against this org's own process guide: `User Story` is
# the standard card for Story work, and `Issue` — the type this constant named before — is
# documented there as OPTIONAL, for bugs of lesser severity. A default is what a repo that
# declared nothing receives, and receiving "minor bug" on a Power BI panel that reads the
# type is a silent error, not a neutral one. `azurePlacement.workItemType` overrides this for
# a Scrum or CMMI process, whose equivalent is named differently — the same process-dependence
# `azureStates` already carries.
#
# There is no second type any more: a spec was one work item plus one CHILD PER TASK until the
# `## Tasks`-as-children mapping was retired for the reasons at HYBRID SERIALISATION, and one
# work item now carries the whole document.
AZ_SPEC_TYPE = "User Story"

# `System.Tags` is one string, not an array like a GitHub issue's `labels` — Azure Boards'
# own convention joins tags with a semicolon, published in its field reference.
AZ_TAG_SEP = "; "


def open_azure_backend(root: str) -> tuple[SpecBackend | None, dict]:
    """The `azure-boards` backend for this workspace, or the refusal that says why not.

    Resolution happens HERE and not in the constructor, on the same "on demand" rule
    `open_backend` applies to the files worktree and the github backend: the round trip is
    paid by the first command that needs a spec, and the missing-binary, not-logged-in and
    nothing-declared refusals arrive at the START of a command rather than halfway through
    a write."""
    cwd = find_repo_root(root)
    cfg = load_config(root)
    states = cfg["azureStates"]
    if not states:
        return None, {
            "code": "sp-az-no-states", "exit": 2, "config": cfg["path"],
            "message": "backend 'azure-boards' needs the phase-to-state mapping declared in "
                       f"{CONFIG_FILE} — add "
                       '`"azureStates": {"plans": "<your active state>", "archive": '
                       '"<your closed state>"}`; an Azure Boards state is defined by the '
                       "project's process, so this tool never guesses it. No spec was read "
                       "or written",
        }
    area_path = cfg["azurePlacement"].get("areaPath")
    if not area_path:
        return None, {
            "code": "sp-az-no-area", "exit": 2, "config": cfg["path"],
            "message": "backend 'azure-boards' needs `areaPath` declared in "
                       f"{CONFIG_FILE}'s `azurePlacement` — add "
                       '`"azurePlacement": {"areaPath": "<Project>\\\\<Area>\\\\<Sub-area>"}`; '
                       "an Azure Boards area is defined by the project, and a guessed default "
                       "does not fail loudly — it writes to the wrong part of somebody else's "
                       "board. No spec was read or written",
        }
    (org, project), err = resolve_azure_project(cwd)
    if err:
        return None, err
    placement = cfg["azurePlacement"]
    discovery_tag = placement.get("discoveryTag") or AZ_DEFAULT_DISCOVERY_TAG
    work_item_type = placement.get("workItemType") or AZ_SPEC_TYPE
    return AzureBoardsBackend(org, project, states, cwd,
                              area_path=area_path, discovery_tag=discovery_tag,
                              work_item_type=work_item_type,
                              iteration_path=placement.get("iterationPath"),
                              team=placement.get("team"),
                              board_column=placement.get("boardColumn"),
                              repository=placement.get("repository"),
                              column_map=cfg["azureColumns"],
                              tag_catalog=cfg["tagCatalog"],
                              types=cfg["workItemTypes"]), {}
