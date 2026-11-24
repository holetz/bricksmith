"""`cq git worktree link` — materialise the target's declared shared paths; `cq git worktree add`
— cut a branch from the fetched remote base into a new worktree, then link it. The reusable slots
(`acquire`, `release`, `slots`) live in `slots.py`.

The store is derived from git's common directory rather than from the checkout path.  Asking
for an absolute common directory is essential: a primary checkout otherwise answers with a
relative ``.git`` and would create a different sibling store depending on the caller's cwd.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

from quenching.common.git import _git, _git_run
from quenching.common.config import load_config, namespace
from quenching.common.output import FINDINGS, emit, refuse
from quenching.common.paths import native_path
from quenching.git.audit import SAFE, _branch_ok, _commit, _run
from quenching.git.ignored import _is_link, declared_regenerable, ignored_entries, undeclared
from quenching.git.push import remote_probe
from quenching.git.pull import _Quiet, _spec_vs_head
from quenching.specs.commands.fields import cmd_record
from quenching.specs.config import find_repo_root

def _repo_root(cwd: str) -> str:
    return native_path(_git(cwd, "rev-parse", "--show-toplevel").strip())


def _declared_paths(config: dict) -> list[str]:
    values = namespace(config, "shared").get("sharedPaths")
    if not isinstance(values, list):
        return []

    paths = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        relative = os.path.normpath(value.strip())
        if os.path.isabs(relative) or relative == "." or relative == "..":
            continue
        if relative.startswith(f"..{os.sep}"):
            continue
        paths.append(relative)
    return paths


def _store_names(repo: str, relative: str) -> tuple[str, str] | None:
    """`(store, legacy)`: the sibling store of `relative` and the name it had before the leading
    dots were dropped (`.tmp` was `<repo>..tmp`, is `<repo>.tmp`); equal when nothing changed."""
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    if not common:
        return None
    legacy = relative.replace(os.sep, ".")
    name = legacy.lstrip(".") or legacy
    base = os.path.dirname(native_path(common))
    return f"{base}.{name}", f"{base}.{legacy}"


def _migrate_store(store: str, legacy: str) -> str | None:
    """Rename the legacy sibling store to its new name, when only the legacy one exists; the
    path it came from, or None when there was nothing to move. Both present → FileExistsError,
    and nothing is touched."""
    if legacy == store or not os.path.lexists(legacy):
        return None
    if os.path.lexists(store):
        raise FileExistsError(store)
    os.rename(legacy, store)
    return legacy


def _link_path(repo: str, relative: str) -> str:
    return os.path.join(repo, relative)


def _make_link(store: str, link: str) -> None:
    """Symlink `link` to `store`; on Windows, when the symlink is refused, a junction, which needs no privilege."""
    try:
        os.symlink(store, link, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        # `cmd` reads a `/` inside `C:/x` as a switch, so the junction gets native paths
        result = subprocess.run(["cmd", "/c", "mklink", "/J", native_path(link), native_path(store)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            raise


def _remove_link(link: str) -> None:
    if os.path.islink(link):
        os.unlink(link)
    else:
        os.rmdir(link)


def _materialise(repo: str, relative: str, store: str) -> dict:
    link = _link_path(repo, relative)
    if _is_link(link):
        if os.path.realpath(link) == os.path.realpath(store):
            return {"path": relative, "store": store, "state": "unchanged"}
        _remove_link(link)
        _make_link(store, link)
        return {"path": relative, "store": store, "state": "repointed"}

    empty_directory = False
    if os.path.lexists(link):
        if os.path.isdir(link) and not os.listdir(link):
            empty_directory = True
        else:
            raise FileExistsError(link)

    os.makedirs(store, exist_ok=True)
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if empty_directory:
        os.rmdir(link)
    _make_link(store, link)
    return {"path": relative, "store": store, "state": "created"}


def _ignore_warning(repo: str, relative: str) -> str | None:
    code, _stdout, _stderr = _git_run(repo, "check-ignore", "--quiet", "--", relative)
    if code == 0:
        return None
    return f"'{relative}' is not covered by git check-ignore"


def _link(repo: str) -> tuple[list[dict] | None, dict | None]:
    """The declared shared paths of `repo` materialised, or the refusal that stopped it."""
    config = load_config(repo)
    if config.get("migrationRefusal"):
        return None, config["migrationRefusal"]
    results = []
    for relative in _declared_paths(config):
        names = _store_names(repo, relative)
        if names is None:
            return None, {"code": "git-worktree-no-common-dir", "path": relative,
                          "message": "git could not resolve the repository's common directory"}
        store, legacy = names
        try:
            migrated = _migrate_store(store, legacy)
        except FileExistsError:
            return None, {"code": "git-worktree-store-conflict", "path": relative,
                          "store": store, "legacy": legacy,
                          "message": f"both the shared store '{store}' and its legacy name "
                                     f"'{legacy}' exist; merge them into '{store}' by hand and "
                                     "remove the legacy one, then link again"}
        except OSError as exc:
            return None, {"code": "git-worktree-store-migrate-failed", "path": relative,
                          "store": store, "legacy": legacy,
                          "message": f"could not rename '{legacy}' to '{store}': {exc}"}
        try:
            item = _materialise(repo, relative, store)
        except FileExistsError:
            return None, {"code": "git-worktree-path-not-empty", "path": relative,
                          "message": f"'{relative}' is a real path with content; merge it into "
                                     "the shared store by hand before linking it"}
        if migrated:
            item["migratedFrom"] = migrated
        warning = _ignore_warning(repo, relative)
        if warning:
            item["warning"] = warning
        results.append(item)
    return results, None


def _link_lines(results: list[dict]) -> list[str]:
    human = []
    for item in results:
        line = f"{item['path']}: {item['state']} -> {item['store']}"
        if item.get("migratedFrom"):
            line += f" (renamed from {item['migratedFrom']})"
        if item.get("warning"):
            line += f" [warning: {item['warning']}]"
        human.append(line)
    return human


def _stamp_branch(spec: str, base: str, work: str) -> dict:
    """Merge the spec's `branch` record through the same function `cq specs record` runs."""
    quiet = _Quiet()
    ns = SimpleNamespace(spec=spec, name="branch", json=True,
                         set=[f"base={base}", f"work={work}"])
    try:
        code = cmd_record(ns, find_repo_root(os.getcwd()), quiet)
    except Exception as e:  # the worktree exists already: a stamp that blew up is reported
        return {"ok": False, "message": str(e)}
    if code != 0:
        return {"ok": False, "message": quiet.seen.get("message", f"record exited {code}")}
    return {"ok": True, "value": quiet.seen.get("value")}


def _setup(path: str) -> dict | None:
    """Run the new worktree's declared `worktreeSetup` once, with cwd inside it.

    The declaration is read from the worktree just cut — the fetched base, not the caller's tree —
    and is its own consent (git/isolation.md): it is printed verbatim before it runs, its output
    goes to stderr so `--json` stays parseable, and a failure is reported, never undone."""
    command = namespace(load_config(path), "shared").get("worktreeSetup")
    if not isinstance(command, str) or not command.strip():
        return None
    command = command.strip()
    print(f"setup: {command}", file=sys.stderr, flush=True)
    try:
        code = subprocess.run(command, shell=True, cwd=path, stdin=subprocess.DEVNULL,
                              stdout=sys.stderr, stderr=sys.stderr).returncode
    except OSError as exc:
        return {"command": command, "exit": None, "ok": False, "message": str(exc)}
    return {"command": command, "exit": code, "ok": code == 0}


def _add(args) -> int:
    """Cut `--branch` from `<remote>/<base>` into a new worktree at `--path`, then link it.

    Every git call is built here with `audit.SAFE`; the start point is resolved to a commit
    before `worktree add`, so the new branch has no upstream and a push without a destination
    never targets the base. Only a remote that does not exist falls back to the local base."""
    cwd = os.getcwd()
    missing = [f"--{k}" for k in ("path", "branch", "base") if not getattr(args, k)]
    if missing:
        return refuse({"code": "git-worktree-add-args",
                       "message": f"`worktree add` needs {', '.join(missing)}"}, args.json)
    path = os.path.abspath(args.path)
    if os.path.lexists(path):
        return refuse({"code": "git-worktree-path-exists", "path": path,
                       "message": f"the worktree path already exists: {path}"}, args.json)
    for label, value in (("branch", args.branch), ("base", args.base), ("remote", args.remote)):
        if not value or value.startswith("-") or not _branch_ok(cwd, value):
            return refuse({"code": "git-worktree-ref-invalid",
                           "message": f"{label} is not a usable name: {value}"}, args.json)
    if args.spec:
        bad = _spec_vs_head(args.spec, args.branch, by_name=False)
        if bad:
            return refuse({**bad, "code": bad["code"].replace("git-pr-", "git-worktree-")},
                          args.json)
    if _commit(cwd, f"refs/heads/{args.branch}") is not None:
        return refuse({"code": "git-worktree-branch-exists",
                       "message": f"the branch already exists: {args.branch}"}, args.json)
    has_remote = remote_probe(cwd, args.remote)
    if has_remote is None:
        return refuse({"code": "git-worktree-remote-unreadable",
                       "message": f"git could not read the remote: {args.remote}"}, args.json)
    if has_remote:
        code, _out, err = _git_run(cwd, *SAFE, "fetch", "--no-recurse-submodules", "--",
                                   args.remote, f"refs/heads/{args.base}")
        if code != 0:
            return refuse({"code": "git-worktree-fetch-failed",
                           "message": f"git fetch {args.remote} {args.base} failed: {err.strip()}"},
                          args.json)
        start_ref = f"refs/remotes/{args.remote}/{args.base}"
        sha = _commit(cwd, "FETCH_HEAD")
    else:
        start_ref = f"refs/heads/{args.base}"
        sha = _commit(cwd, start_ref)
    if sha is None:
        return refuse({"code": "git-worktree-ref-invalid",
                       "message": f"the base does not resolve to a commit: {start_ref}"}, args.json)
    code, _out, err = _git_run(cwd, *SAFE, "worktree", "add", "--no-track", "-b", args.branch,
                               "--", path, sha)
    if code != 0:
        return refuse({"code": "git-worktree-add-failed",
                       "message": f"git worktree add failed: {err.strip()}"}, args.json)
    results, refusal = _link(path)
    stamp = _stamp_branch(args.spec, args.base, args.branch) if args.spec else None
    setup = None if refusal or args.no_setup else _setup(path)
    payload = {"ok": refusal is None and (stamp is None or stamp["ok"])
                     and (setup is None or setup["ok"]),
               "path": path, "branch": args.branch, "base": args.base,
               "start": f"{args.remote}/{args.base}" if has_remote else args.base,
               "fromRemote": has_remote, "startSha": sha, "paths": results or [],
               "linkRefusal": refusal}
    if stamp:
        payload["branchRecord"] = stamp
    payload["setup"] = setup
    human = [f"worktree: {path}", f"branch: {args.branch} from {payload['start']} ({sha[:12]})"]
    if not has_remote:
        human.append(f"NO-REMOTE: {args.remote} is not configured; cut from the local {args.base}")
    human += _link_lines(results or [])
    if refusal:
        human.append(f"link refused: {refusal.get('message')}")
    if stamp:
        human.append(f"`branch` record stamped for spec {args.spec}" if stamp["ok"]
                     else f"`branch` record not stamped: {stamp['message']}")
    if setup:
        human.append(f"setup ran: {setup['command']}" if setup["ok"]
                     else f"setup failed (exit {setup['exit']}), the worktree stands: "
                          f"{setup['command']}")
    elif args.no_setup:
        human.append("setup skipped (--no-setup)")
    emit(args.json, payload, "\n".join(human))
    return 0 if payload["ok"] else FINDINGS


def _registered_worktrees(cwd: str) -> list[tuple[str, str | None]]:
    """(path, branch) of every worktree git registers, the primary checkout first."""
    code, out, _err = _git_run(cwd, *SAFE, "worktree", "list", "--porcelain")
    found: list[tuple[str, str | None]] = []
    path = branch = None
    for line in (out.splitlines() if code == 0 else []) + [""]:
        if line.startswith("worktree "):
            path = native_path(line[len("worktree "):])
        elif line.startswith("branch "):
            branch = line[len("branch "):].removeprefix("refs/heads/")
        elif not line and path is not None:
            found.append((path, branch))
            path = branch = None
    return found


def _is_codex_artifact(entry: str, sep: str = os.sep) -> bool:
    """Whether an ignored entry is `.quenching/codex` or sits under it; `os.walk` entries carry
    the platform separator, so it is normalized to `/` before the comparison."""
    entry = entry.replace(sep, "/")
    return entry == ".quenching/codex" or entry.startswith(".quenching/codex/")


def _retire(args) -> int:
    """Remove a merged spec's worktree and delete its branch with `branch -d`, never `-D`.

    Merged means the branch tip is an ancestor of `<remote>/<base>` (fetched here; the local base
    when the remote does not exist), because a PR merged through `gh` moves the remote base. Every
    preflight refusal (exit 2) happens before a write. The worktree goes first, since git will not
    delete a branch checked out in one; `worktree remove` runs without `--force`, so a dirty worktree
    stands, and is read before any write (exit 1, `dirty` lists the paths). A worktree holding
    ignored files that are not symlinks stands too (exit 1, `ignored` lists them) unless
    `shared.regenerablePaths` declares them regenerable or `--discard-ignored` is passed.
    A real `.quenching/codex` directory is archived in the primary checkout before removal, and put
    back (`restored`) when `worktree remove` still refuses.
    `branch -d` compares the branch with its upstream when one is configured, so a published branch is
    deleted even while the local base is behind; with no upstream it compares with the checked-out
    HEAD and refuses when the local base is behind: the branch stands, exit 1."""
    cwd = os.getcwd()
    missing = [f"--{k}" for k in ("path", "branch", "base") if not getattr(args, k)]
    if missing:
        return refuse({"code": "git-worktree-retire-args",
                       "message": f"`worktree retire` needs {', '.join(missing)}"}, args.json)
    for label, value in (("branch", args.branch), ("base", args.base), ("remote", args.remote)):
        if not value or value.startswith("-") or not _branch_ok(cwd, value):
            return refuse({"code": "git-worktree-ref-invalid",
                           "message": f"{label} is not a usable name: {value}"}, args.json)
    if args.branch == args.base:
        return refuse({"code": "git-worktree-retire-base",
                       "message": f"the branch is the base: {args.branch}"}, args.json)
    registered = _registered_worktrees(cwd)
    want = os.path.realpath(args.path)
    match = [(p, b) for p, b in registered if os.path.realpath(p) == want]
    if not match:
        return refuse({"code": "git-worktree-retire-unregistered",
                       "message": f"not a worktree this repository registers: {args.path}"},
                      args.json)
    path, registered_branch = match[0]
    if want in (os.path.realpath(registered[0][0]), os.path.realpath(_repo_root(cwd))):
        return refuse({"code": "git-worktree-retire-primary",
                       "message": "the primary checkout, or the one this runs in, is never retired"},
                      args.json)
    if registered_branch != args.branch:
        return refuse({"code": "git-worktree-retire-mismatch",
                       "message": f"{args.path} holds {registered_branch or 'a detached HEAD'}, "
                                  f"not {args.branch}"}, args.json)
    has_remote = remote_probe(cwd, args.remote)
    if has_remote is None:
        return refuse({"code": "git-worktree-remote-unreadable",
                       "message": f"git could not read the remote: {args.remote}"}, args.json)
    if has_remote:
        code, _out, err = _git_run(cwd, *SAFE, "fetch", "--no-recurse-submodules", "--",
                                   args.remote, f"refs/heads/{args.base}")
        if code != 0:
            return refuse({"code": "git-worktree-fetch-failed",
                           "message": f"git fetch {args.remote} {args.base} failed: {err.strip()}"},
                          args.json)
        target, base_sha = f"{args.remote}/{args.base}", _commit(cwd, "FETCH_HEAD")
    else:
        target, base_sha = args.base, _commit(cwd, f"refs/heads/{args.base}")
    tip = _commit(cwd, f"refs/heads/{args.branch}")
    if base_sha is None or tip is None:
        return refuse({"code": "git-worktree-ref-invalid",
                       "message": "the base or the branch does not resolve to a commit"}, args.json)
    if _run(cwd, "merge-base", "--is-ancestor", tip, base_sha)[0] != 0:
        return refuse({"code": "git-worktree-retire-not-merged",
                       "message": f"{args.branch} is not merged into {target}; nothing was removed"},
                      args.json)
    payload = {"ok": False, "path": path, "branch": args.branch, "base": target,
               "worktreeRemoved": False, "branchDeleted": False, "archived": None}
    ignored = [] if args.discard_ignored else ignored_entries(path)
    if ignored:
        declared = declared_regenerable(load_config(_repo_root(cwd)))
        ignored = undeclared(path, ignored, declared)
    if ignored is None:
        return refuse({"code": "git-worktree-retire-status-failed",
                       "message": f"git status failed in {path}; nothing was removed"}, args.json)
    ignored = [entry for entry in ignored if not _is_codex_artifact(entry)]
    if ignored:
        payload.update(ignored=ignored, message=f"the worktree holds ignored files that removal "
                       f"would delete: {', '.join(ignored)}; pass --discard-ignored to remove anyway")
        emit(args.json, payload, f"worktree {path} kept: {payload['message']}")
        return FINDINGS
    code, out, _err = _git_run(path, *SAFE, "status", "--porcelain", "--untracked-files=all")
    if code != 0:
        return refuse({"code": "git-worktree-retire-status-failed",
                       "message": f"git status failed in {path}; nothing was removed"}, args.json)
    dirty = [line[3:] for line in out.splitlines() if not _is_codex_artifact(line[3:])]
    if dirty:
        payload.update(dirty=dirty, message=f"the worktree has changes that removal would refuse: "
                       f"{', '.join(dirty)}")
        emit(args.json, payload, f"worktree {path} kept: {payload['message']}")
        return FINDINGS
    source = os.path.join(path, ".quenching", "codex")
    if os.path.isdir(source) and not _is_link(source):
        destination = os.path.join(registered[0][0], ".quenching", "codex", "retired",
                                   os.path.basename(path))
        archived = destination
        suffix = 2
        while os.path.lexists(archived):
            archived = f"{destination}-{suffix}"
            suffix += 1
        try:
            os.makedirs(os.path.dirname(archived), exist_ok=True)
            shutil.move(source, archived)
        except (OSError, shutil.Error) as exc:
            return refuse({"code": "git-worktree-retire-archive-failed",
                           "message": f"could not archive {source}: {exc}"}, args.json)
        payload["archived"] = archived
    code, _out, err = _git_run(cwd, *SAFE, "worktree", "remove", "--", path)
    if code != 0:
        payload["message"] = err.strip()
        if payload["archived"]:
            try:
                shutil.move(payload["archived"], source)
                payload.update(archived=None, restored=True)
            except (OSError, shutil.Error) as exc:
                payload.update(restored=False, message=f"{err.strip()}; the Codex state stays at "
                               f"{payload['archived']}: {exc}")
        emit(args.json, payload, f"worktree {path} refused: {payload['message']}")
        return FINDINGS
    payload["worktreeRemoved"] = True
    code, _out, err = _git_run(cwd, *SAFE, "branch", "-d", "--", args.branch)
    if code != 0:
        payload["message"] = err.strip()
        emit(args.json, payload, f"worktree removed; branch {args.branch} stands: {err.strip()}")
        return FINDINGS
    payload.update(ok=True, branchDeleted=True)
    emit(args.json, payload, f"retired {path} and branch {args.branch}")
    return 0


def cmd_worktree(args) -> int:
    if args.action in ("acquire", "release", "slots"):
        from quenching.git import slots
        return getattr(slots, args.action)(args)
    if args.action == "add":
        return _add(args)
    if args.action == "retire":
        return _retire(args)
    repo = _repo_root(os.getcwd())
    if not repo:
        return refuse({"code": "git-worktree-not-repository",
                        "message": "the current directory is not inside a git repository"},
                       args.json)
    results, refusal = _link(repo)
    if refusal:
        return refuse(refusal, args.json)
    emit(args.json, {"ok": True, "paths": results}, "\n".join(_link_lines(results)) or "shared paths: none")
    return 0
