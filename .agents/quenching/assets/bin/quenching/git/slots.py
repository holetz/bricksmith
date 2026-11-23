"""`cq git worktree acquire | release | slots` — reusable worktrees, bounded by two limits.

A slot is a registered worktree at `../<repo>-slot-<n>` that outlives its spec. Acquiring one
switches it to the spec's branch and locks it; releasing it detaches it at the remote base and
unlocks it, keeping `.venv` and every other regenerable cache. A fresh worktree costs a scan of
every file an on-access antivirus sees (tens of thousands with a venv); a branch switch rewrites
only what differs.

`git worktree lock` is the mutex: it fails on a tree already locked, so two sessions racing for
the same free slot cannot both win. Only slots count toward `shared.worktreeSlots.max`; any other
worktree is listed and never counted, never taken.
"""
from __future__ import annotations

import contextlib
import datetime
import os
import re
import shutil
import socket
import sys
import time

from quenching.common.config import load_config, namespace
from quenching.common.git import _git_run
from quenching.common.output import FINDINGS, emit, refuse
from quenching.git.audit import SAFE, _branch_ok, _commit, _reflog, _run
from quenching.git.ignored import (_is_link, classify, declared_regenerable, ignored_entries,
                                   undeclared)
from quenching.git.push import remote_probe

DEFAULTS = {"min": 4, "max": 12, "waitMinutes": 30, "pollSeconds": 30}
REASON_PREFIX = "quenching: "
SEP = " · "


def limits(repo: str) -> dict:
    """`shared.worktreeSlots`, each key falling back to its default when absent or not a
    positive integer; `max` never drops below `min`."""
    raw = namespace(load_config(repo), "shared").get("worktreeSlots")
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, default in DEFAULTS.items():
        value = raw.get(key)
        out[key] = value if isinstance(value, int) and not isinstance(value, bool) \
            and value > 0 else default
    out["max"] = max(out["max"], out["min"])
    return out


def setup_idempotent(repo: str) -> bool:
    """`shared.worktreeSlots.setupIdempotent: true` — the target's word that its `worktreeSetup`
    survives running again over a slot that already holds its dependencies."""
    raw = namespace(load_config(repo), "shared").get("worktreeSlots")
    return isinstance(raw, dict) and raw.get("setupIdempotent") is True


def _worktrees(cwd: str) -> list[dict]:
    """Every registered worktree, the primary first, read NUL-separated so a lock reason arrives
    unquoted."""
    code, out, _err = _git_run(cwd, *SAFE, "worktree", "list", "--porcelain", "-z")
    found, cur = [], {}
    for field in (out.split("\0") if code == 0 else []):
        if not field:
            if cur:
                found.append(cur)
            cur = {}
            continue
        key, _, value = field.partition(" ")
        if key == "worktree":
            cur = {"path": value, "branch": None, "detached": False, "locked": None}
        elif key == "branch":
            cur["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached":
            cur["detached"] = True
        elif key == "locked":
            cur["locked"] = value
        elif key == "prunable":
            cur["prunable"] = True
    if cur:
        found.append(cur)
    return found


def _slot_pattern(primary: str) -> re.Pattern:
    return re.compile(re.escape(os.path.basename(os.path.realpath(primary))) + r"-slot-(\d+)$")


def parse_reason(reason: str | None) -> dict | None:
    """`{spec, session, host, date}` of a quenching lock reason, or None for any other lock."""
    if not reason or not reason.startswith(REASON_PREFIX):
        return None
    parts = reason[len(REASON_PREFIX):].split(SEP)
    parts += [""] * (4 - len(parts))
    return dict(zip(("spec", "session", "host", "date"), parts[:4]))


def lock_reason(holder: str, session: str | None) -> str:
    session = session or os.environ.get("OPENCODE_SESSION_ID") \
        or os.environ.get("CLAUDE_SESSION_ID") or "-"
    date = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return REASON_PREFIX + SEP.join((holder, session, socket.gethostname(), date))


def survey(cwd: str) -> dict:
    """The slots and the other worktrees, as `slots` reports them."""
    trees = _worktrees(cwd)
    primary = trees[0]["path"] if trees else cwd
    pattern = _slot_pattern(primary)
    parent = os.path.dirname(os.path.realpath(primary))
    slots, others = [], []
    for tree in trees[1:]:
        match = pattern.match(os.path.basename(tree["path"]))
        if match and os.path.dirname(os.path.realpath(tree["path"])) == parent:
            slots.append({**tree, "n": int(match.group(1)),
                          "state": "held" if tree["locked"] is not None else "free",
                          "holder": parse_reason(tree["locked"])})
        else:
            kind = "opencode" if "/opencode/worktree/" in tree["path"] else "legacy"
            others.append({**tree, "kind": kind})
    slots.sort(key=lambda s: s["n"])
    return {"primary": primary, "parent": parent, "slots": slots, "others": others,
            "name": os.path.basename(os.path.realpath(primary))}


def _dirty(path: str) -> bool | None:
    code, out, _err = _git_run(path, *SAFE, "status", "--porcelain", "--untracked-files=normal")
    return None if code != 0 else bool(out.strip())


def _fetch_base(cwd: str, remote: str, base: str) -> tuple[str | None, str, dict | None]:
    """`(sha, label, refusal)` of the base to switch from: the fetched remote base, or the local
    one when the remote does not exist."""
    has_remote = remote_probe(cwd, remote)
    if has_remote is None:
        return None, "", {"code": "git-worktree-remote-unreadable",
                          "message": f"git could not read the remote: {remote}"}
    if has_remote:
        for attempt in range(5):
            # Concurrent takers fetch into the same repository: the loser of a ref lock retries,
            # and so does a fetch whose connectivity check read a sibling worktree's HEAD that
            # `git worktree add` had not finished writing (`bad object worktrees/<slot>/HEAD`).
            code, _out, err = _git_run(cwd, *SAFE, "fetch", "--no-recurse-submodules", "--",
                                       remote, f"refs/heads/{base}")
            if code == 0 or not (".lock" in err or "bad object worktrees/" in err):
                break
            time.sleep(0.2 * (attempt + 1))
        if code != 0:
            return None, "", {"code": "git-worktree-fetch-failed",
                              "message": f"git fetch {remote} {base} failed: {err.strip()}"}
        return _commit(cwd, f"refs/remotes/{remote}/{base}"), f"{remote}/{base}", None
    return _commit(cwd, f"refs/heads/{base}"), base, None


def _default_base(cwd: str, remote: str) -> str:
    """The remote's HEAD branch, else `init.defaultBranch`, else `main`."""
    code, out, _err = _git_run(cwd, *SAFE, "symbolic-ref", "--short", f"refs/remotes/{remote}/HEAD")
    if code == 0 and out.strip().startswith(f"{remote}/"):
        return out.strip()[len(remote) + 1:]
    code, out, _err = _git_run(cwd, *SAFE, "config", "init.defaultBranch")
    return out.strip() if code == 0 and out.strip() else "main"


@contextlib.contextmanager
def _creation_lock(cwd: str, timeout: float = 60.0):
    """Serialise slot creation across processes with an `O_EXCL` file in git's common directory;
    a lock older than `timeout` is a crashed taker's and is broken."""
    code, out, _err = _git_run(cwd, *SAFE, "rev-parse", "--path-format=absolute",
                               "--git-common-dir")
    lock = os.path.join(out.strip() if code == 0 else cwd, "quenching-slots.lock")
    start = time.monotonic()
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > timeout:
                    os.unlink(lock)
                    continue
            except OSError:
                continue
            if time.monotonic() - start > timeout:
                raise TimeoutError(f"slot creation lock held: {lock}")
            time.sleep(0.05)
    try:
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            os.unlink(lock)


def _lock(cwd: str, path: str, reason: str) -> bool:
    return _git_run(cwd, *SAFE, "worktree", "lock", "--reason", reason, "--", path)[0] == 0


def _unlock(cwd: str, path: str) -> None:
    _git_run(cwd, *SAFE, "worktree", "unlock", "--", path)


OWN_WORK = ("commit", "cherry-pick", "revert")


def has_own_work(cwd: str, branch: str) -> bool:
    """Whether the local `branch` committed work of its own: a moving reflog entry of
    `refs/heads/<branch>` that is a commit, cherry-pick or revert. A branch just cut, or one that
    only fast-forwarded to its base, has none, and its tip is an ancestor of the base like a
    merged branch's; an empty or expired reflog proves no work either. The one rule both the
    slot reclaim and `cq specs promote`'s merge evidence apply."""
    return any(s.startswith(OWN_WORK) for s in _reflog(cwd, f"refs/heads/{branch}"))


def _merged_holder(cwd: str, slot: dict, base_sha: str | None) -> bool:
    """A held slot whose branch carries work (`has_own_work`) and is already in the base: its
    spec is done and the slot may go. No work → not provably merged, so the slot is never taken."""
    branch = slot.get("branch")
    tip = _commit(cwd, f"refs/heads/{branch}") if branch else None
    if not (tip and base_sha):
        return False
    if not has_own_work(cwd, branch):
        return False
    return _run(cwd, "merge-base", "--is-ancestor", tip, base_sha)[0] == 0


def _take(cwd: str, view: dict, cap: int, reason: str, base_sha: str,
          freed: list) -> tuple[dict | None, bool]:
    """Lock one slot for the caller: a free clean one, else a new one under the cap, else a held
    one whose branch is merged, whose tree is clean, which holds no ignored content `release`
    would refuse on, and where no process runs (released first; without `/proc` the process check
    is skipped). A reused slot's `.quenching/handoff/` is moved to the primary checkout
    (`slot["handoff"]`); a merged holder's `.quenching/codex/` is archived there as `release`
    does (`slot["archived"]`). `(slot, created)`."""
    for slot in view["slots"]:
        if slot["state"] == "free" and _lock(cwd, slot["path"], reason):
            if _dirty(slot["path"]) is False:
                slot["handoff"] = _move_handoffs(slot["path"], view["primary"])
                return slot, False
            _unlock(cwd, slot["path"])
    with _creation_lock(cwd):
        # Counted again under the lock: two takers that both saw room must not both cut.
        current = survey(cwd)["slots"]
        if len(current) < cap:
            used = {s["n"] for s in current}
            n = next(i for i in range(1, cap + 2) if i not in used
                     and not os.path.lexists(os.path.join(view["parent"],
                                                          f"{view['name']}-slot-{i}")))
            path = os.path.join(view["parent"], f"{view['name']}-slot-{n}")
            code, _out, _err = _git_run(cwd, *SAFE, "worktree", "add", "--detach", "--lock",
                                        "--reason", reason, "--", path, base_sha)
            if code == 0:
                return {"path": path, "n": n, "branch": None}, True
    for slot in view["slots"]:
        if slot["state"] == "held" and slot["holder"] and _merged_holder(cwd, slot, base_sha) \
                and _dirty(slot["path"]) is False \
                and _ignored_left(slot["path"], view["primary"]) == [] \
                and not processes_in(slot["path"]):
            # Re-lock under the caller's reason only after the merged holder's lock is gone; a
            # second session racing here loses at `lock`, never both win.
            _unlock(cwd, slot["path"])
            if _lock(cwd, slot["path"], reason):
                freed.append({"path": slot["path"], "branch": slot["branch"],
                              "spec": slot["holder"].get("spec")})
                # The previous spec's handoff and Codex state go to the primary checkout, never
                # left for the next spec and never discarded.
                slot["archived"] = _archive_codex(slot["path"], view["primary"], slot["branch"])
                slot["handoff"] = _move_handoffs(slot["path"], view["primary"])
                return slot, False
    return None, False


def _holders(view: dict) -> list[dict]:
    return [{"slot": s["n"], "path": s["path"], "branch": s["branch"],
             **(s["holder"] or {"lock": s["locked"]}),
             "release": f"cq git worktree release --path {s['path']}"}
            for s in view["slots"] if s["state"] == "held"]


def acquire(args) -> int:
    """Lock a slot, switch it to `--branch`, link the shared paths and run `worktreeSetup`."""
    from quenching.git.worktree import _link, _link_lines, _setup, _stamp_branch
    from quenching.git.pull import _spec_vs_head

    cwd = os.getcwd()
    missing = [f"--{k}" for k in ("branch", "base") if not getattr(args, k)]
    if missing:
        return refuse({"code": "git-worktree-acquire-args",
                       "message": f"`worktree acquire` needs {', '.join(missing)}"}, args.json)
    for label, value in (("branch", args.branch), ("base", args.base), ("remote", args.remote)):
        if not value or value.startswith("-") or not _branch_ok(cwd, value):
            return refuse({"code": "git-worktree-ref-invalid",
                           "message": f"{label} is not a usable name: {value}"}, args.json)
    if args.spec:
        bad = _spec_vs_head(args.spec, args.branch, by_name=False)
        if bad:
            return refuse({**bad, "code": bad["code"].replace("git-pr-", "git-worktree-")},
                          args.json)
    view = survey(cwd)
    for tree in view["slots"] + view["others"]:
        if tree.get("branch") == args.branch:
            mine = tree in view["slots"]
            payload = {"ok": mine, "path": tree["path"], "branch": args.branch, "reused": True,
                       "message": f"{args.branch} is already checked out at {tree['path']}"}
            if not mine:
                return refuse({**payload, "code": "git-worktree-branch-held"}, args.json)
            emit(args.json, payload, f"slot: {tree['path']} (already holds {args.branch})")
            return 0
    base_sha, start, refusal = _fetch_base(cwd, args.remote, args.base)
    if refusal or base_sha is None:
        return refuse(refusal or {"code": "git-worktree-ref-invalid",
                                  "message": f"the base does not resolve: {args.base}"}, args.json)
    cap = limits(view["primary"])
    reason = lock_reason(str(args.spec or args.branch), getattr(args, "session", None))
    wait = getattr(args, "wait", False)
    deadline = time.monotonic() + 60 * (getattr(args, "timeout_minutes", None)
                                        or cap["waitMinutes"])
    freed: list = []
    announced = False
    while True:
        slot, created = _take(cwd, view, cap["max"], reason, base_sha, freed)
        if slot or not wait or time.monotonic() >= deadline:
            break
        if not announced:
            print(f"slot limit reached ({cap['max']}): waiting for a slot — every "
                  f"{cap['pollSeconds']}s, up to {cap['waitMinutes']} min", file=sys.stderr,
                  flush=True)
            announced = True
        time.sleep(cap["pollSeconds"])
        view = survey(cwd)
    if slot is None:
        code = "git-worktree-slots-timeout" if wait else "git-worktree-slots-full"
        holders = _holders(survey(cwd))
        return refuse({"code": code, "max": cap["max"], "holders": holders,
                       "needsHuman": wait,
                       "message": f"all {cap['max']} slots are held"
                                  + ("; the wait timed out" if wait else "")
                                  + " — tell the human the limit was reached; a stale lock is "
                                    "released with the command each holder names"}, args.json)
    path = slot["path"]
    if _commit(cwd, f"refs/heads/{args.branch}") is not None:
        switch = ("switch", args.branch)
    else:
        switch = ("switch", "--no-track", "-c", args.branch, base_sha)
    code, _out, err = _git_run(path, *SAFE, *switch)
    if code != 0:
        _git_run(path, *SAFE, "switch", "--detach", base_sha)
        _unlock(cwd, path)
        return refuse({"code": "git-worktree-switch-failed", "path": path,
                       "message": f"git switch failed in {path}: {err.strip()}"}, args.json)
    results, link_refusal = _link(path)
    stamp = _stamp_branch(args.spec, args.base, args.branch) if args.spec else None
    setup = None if link_refusal or args.no_setup else _setup(path)
    payload = {"ok": link_refusal is None and (stamp is None or stamp["ok"])
                     and (setup is None or setup["ok"]),
               "path": path, "slot": slot["n"], "created": created, "reused": not created,
               "branch": args.branch, "base": args.base, "start": start, "startSha": base_sha,
               "lock": reason, "freed": freed, "archived": slot.get("archived"),
               "handoff": slot.get("handoff", []),
               "paths": results or [],
               "linkRefusal": link_refusal, "setup": setup}
    if stamp:
        payload["branchRecord"] = stamp
    human = [f"slot {slot['n']}: {path} ({'new' if created else 'reused'})",
             f"branch: {args.branch} from {start} ({base_sha[:12]})"]
    human += [f"freed: {f['path']} (merged {f['branch']})" for f in freed]
    if slot.get("archived"):
        human.append(f"codex state archived: {slot['archived']}")
    human += [f"handoff moved: {h['from']} -> {h['to']}" for h in slot.get("handoff", [])]
    human += _link_lines(results or [])
    if link_refusal:
        human.append(f"link refused: {link_refusal.get('message')}")
    if stamp:
        human.append(f"`branch` record stamped for spec {args.spec}" if stamp["ok"]
                     else f"`branch` record not stamped: {stamp['message']}")
    if setup:
        human.append(f"setup ran: {setup['command']}" if setup["ok"]
                     else f"setup failed (exit {setup['exit']}): {setup['command']}")
    emit(args.json, payload, "\n".join(human))
    return 0 if payload["ok"] else FINDINGS


COLLECTED = (".quenching/codex", ".quenching/handoff")


def _collected(entry: str) -> bool:
    """An ignored entry `release` carries to the primary checkout instead of refusing on."""
    entry = entry.replace(os.sep, "/")
    return any(entry == c or entry.startswith(c + "/") for c in COLLECTED)


def _ignored_left(path: str, primary: str) -> list[str] | None:
    """The ignored entries in `path` that neither the built-in caches nor
    `shared.regenerablePaths` cover and that `release` does not carry over; `None` when the
    inspection fails."""
    ignored = ignored_entries(path)
    if ignored is None:
        return None
    rules = declared_regenerable(load_config(primary))
    return [e for e in undeclared(path, ignored, rules) if not _collected(e)]


def _archive_codex(path: str, primary: str, branch: str | None) -> str | None:
    """Move the slot's `.quenching/codex/` under the primary checkout's
    `.quenching/codex/retired/`, never over an earlier archive; the target, or `None`."""
    source = os.path.join(path, ".quenching", "codex")
    if not os.path.isdir(source) or _is_link(source):
        return None
    archived = os.path.join(primary, ".quenching", "codex", "retired",
                            f"{os.path.basename(path)}-{(branch or 'detached').replace('/', '-')}")
    suffix, target = 2, archived
    while os.path.lexists(target):
        target, suffix = f"{archived}-{suffix}", suffix + 1
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(source, target)
    return target


def _move_handoffs(path: str, primary: str) -> list[dict]:
    """Move every file under the slot's `.quenching/handoff/` to the same relative place in the
    primary checkout. A name already taken there gets `-<slot>-<n>` before its extension: a
    handoff is never overwritten and never discarded."""
    source = os.path.join(path, ".quenching", "handoff")
    if not os.path.isdir(source) or _is_link(source):
        return []
    target_root = os.path.join(primary, ".quenching", "handoff")
    moved = []
    for root, _dirs, names in os.walk(source):
        for name in sorted(names):
            rel = os.path.relpath(os.path.join(root, name), source)
            target = os.path.join(target_root, rel)
            stem, ext = os.path.splitext(target)
            n = 1
            while os.path.lexists(target):
                target = f"{stem}-{os.path.basename(path)}-{n}{ext}"
                n += 1
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(os.path.join(root, name), target)
            moved.append({"from": os.path.join(root, name), "to": target})
    shutil.rmtree(source)
    return moved


def processes_in(path: str) -> list[int] | None:
    """The pids whose cwd is `path` or inside it, read from `/proc`; None where there is no
    `/proc` to read. A pid this user may not inspect, or one gone mid-read, is skipped."""
    if not os.path.isdir("/proc/self"):
        return None
    real = os.path.realpath(path)
    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            where = os.readlink(os.path.join("/proc", pid, "cwd"))
        except OSError:
            continue
        if where == real or where.startswith(real + os.sep):
            found.append(int(pid))
    return found


def spec_phases(cwd: str, ids: list[str]) -> dict[str, str | None]:
    """`{id: phase}` read from the spec backend in one batch; None for an id that does not
    resolve, and for every id when the backend does not open."""
    from quenching.specs.backends import open_backend
    from quenching.specs.config import find_repo_root

    if not ids:
        return {}
    backend, err = open_backend(find_repo_root(cwd))
    if err:
        return dict.fromkeys(ids)
    try:
        infos = backend.read_specs(ids)
    except Exception:  # any backend failure leaves the holder unknown, never archived
        return dict.fromkeys(ids)
    return {i: (infos.get(i) or {}).get("phase") for i in ids}


def reclaim_evidence(cwd: str, slot: dict, remote: str, base: str, rules: list[str],
                     phase: str | None) -> dict:
    """Whether a held slot may be released for its spec is done: `eligible` only with every
    piece of evidence in favour — the holder spec in `archive`, HEAD contained in
    `<remote>/<base>`, a clean porcelain, no ignored content outside `rules` and what `release`
    collects, and no process with its cwd in the slot. Each missing or contrary fact is a
    named blocker."""
    path, holder = slot["path"], slot.get("holder")
    spec = (holder or {}).get("spec") or None
    blockers = []
    if holder is None:
        blockers.append("foreign-lock")
    elif not (spec or "").isdigit():
        blockers.append("holder-not-a-spec")
    elif phase is None:
        blockers.append("holder-unknown")
    elif phase != "archive":
        blockers.append("holder-not-archived")
    head = _commit(path, "HEAD")
    remote_base = _commit(cwd, f"refs/remotes/{remote}/{base}")
    if remote_base is None:
        blockers.append("base-unresolved")
    elif not head or _run(cwd, "merge-base", "--is-ancestor", head, remote_base)[0] != 0:
        blockers.append("unmerged-head")
    dirty = _dirty(path)
    entries = ignored_entries(path)
    ignored = {"regenerable": [], "unrecognized": []}
    if dirty is None or entries is None:
        blockers.append("inspection-failed")
    else:
        if dirty:
            blockers.append("dirty")
        regenerable, unrecognized = classify(path, entries, rules)
        ignored = {"regenerable": regenerable,
                   "unrecognized": [e for e in unrecognized if not _collected(e)]}
        if ignored["unrecognized"]:
            blockers.append("ignored-content-unrecognized")
    processes = processes_in(path)
    if processes is None:
        blockers.append("process-lookup-unavailable")
    elif processes:
        blockers.append("process-in-slot")
    return {"path": path, "slot": slot["n"], "branch": slot.get("branch"), "spec": spec,
            "phase": phase, "head": head, "eligible": not blockers, "blockers": blockers,
            "ignored": ignored, "processes": processes}


def release(args) -> int:
    """Detach a clean slot at the remote base and unlock it; `branch -d` its branch when merged.

    A dirty tree, or ignored content no `shared.regenerablePaths` entry covers, keeps the slot
    held (exit 1): nothing of one spec may leak into the next, and nothing is committed or
    discarded to free it. `.quenching/codex/` and `.quenching/handoff/` are carried to the
    primary checkout instead."""
    cwd = os.getcwd()
    view = survey(cwd)
    want = os.path.realpath(args.path) if args.path else None
    match = [s for s in view["slots"]
             if (want and os.path.realpath(s["path"]) == want)
             or (not want and args.branch and s["branch"] == args.branch)]
    if not match:
        return refuse({"code": "git-worktree-release-not-a-slot",
                       "message": f"not a slot of this repository: {args.path or args.branch}"
                                  " — a worktree that is not a slot is removed with `retire`"},
                      args.json)
    slot = match[0]
    path, branch = slot["path"], slot["branch"]
    base = args.base or _default_base(cwd, args.remote)
    if not _branch_ok(cwd, base) or not _branch_ok(cwd, args.remote):
        return refuse({"code": "git-worktree-ref-invalid",
                       "message": f"base or remote is not a usable name: {base}"}, args.json)
    dirty = _dirty(path)
    if dirty is None:
        return refuse({"code": "git-worktree-release-status-failed",
                       "message": f"git status failed in {path}"}, args.json)
    payload = {"ok": False, "path": path, "slot": slot["n"], "branch": branch,
               "released": False, "branchDeleted": False, "archived": None, "handoff": []}
    if dirty:
        payload["message"] = f"{path} has uncommitted changes; the slot stays held"
        emit(args.json, payload, payload["message"])
        return FINDINGS
    left = _ignored_left(path, view["primary"])
    if left is None:
        return refuse({"code": "git-worktree-release-status-failed",
                       "message": f"git status failed in {path}"}, args.json)
    if left:
        payload.update(ignored=left, message=f"{path} holds ignored files no "
                       f"shared.regenerablePaths entry covers: {', '.join(left)}; the slot stays held")
        emit(args.json, payload, payload["message"])
        return FINDINGS
    base_sha, start, refusal = _fetch_base(cwd, args.remote, base)
    if refusal or base_sha is None:
        return refuse(refusal or {"code": "git-worktree-ref-invalid",
                                  "message": f"the base does not resolve: {base}"}, args.json)
    payload["archived"] = _archive_codex(path, view["primary"], branch)
    payload["handoff"] = _move_handoffs(path, view["primary"])
    code, _out, err = _git_run(path, *SAFE, "switch", "--detach", base_sha)
    if code != 0:
        payload["message"] = f"git switch --detach failed in {path}: {err.strip()}"
        emit(args.json, payload, payload["message"])
        return FINDINGS
    _unlock(cwd, path)
    payload.update(ok=True, released=True, base=start)
    tip = _commit(cwd, f"refs/heads/{branch}") if branch else None
    if tip and _run(cwd, "merge-base", "--is-ancestor", tip, base_sha)[0] == 0:
        payload["branchDeleted"] = _git_run(cwd, *SAFE, "branch", "-d", "--", branch)[0] == 0
    emit(args.json, payload, f"released slot {slot['n']}: {path} detached at {start}"
         + (f"; branch {branch} deleted" if payload["branchDeleted"] else
            (f"; branch {branch} kept (not merged)" if branch else "")))
    return 0


def slots(args) -> int:
    """List the slots, who holds each, and the worktrees that are not slots (never counted)."""
    view = survey(os.getcwd())
    cap = limits(view["primary"])
    held = [s for s in view["slots"] if s["state"] == "held"]
    payload = {"ok": True, **cap, "count": len(view["slots"]), "held": len(held),
               "free": len(view["slots"]) - len(held),
               "slots": [{"slot": s["n"], "path": s["path"], "state": s["state"],
                          "branch": s["branch"], "holder": s["holder"], "lock": s["locked"]}
                         for s in view["slots"]],
               "others": [{"path": o["path"], "branch": o["branch"], "kind": o["kind"]}
                          for o in view["others"]]}
    human = [f"slots: {payload['count']} of {cap['max']} ({len(held)} held, "
             f"{payload['free']} free; floor {cap['min']})"]
    for s in view["slots"]:
        who = s["holder"] or {}
        human.append(f"  slot {s['n']:<3} {s['state']:<5} {s['branch'] or '(detached)'}"
                     + (f"  — {who.get('spec')} · {who.get('session')} · {who.get('date')}"
                        if who else ""))
    for o in view["others"]:
        human.append(f"  {o['kind']:<9} {o['path']}  {o['branch'] or '(detached)'}  (not counted)")
    emit(args.json, payload, "\n".join(human))
    return 0
