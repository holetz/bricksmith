"""`cq git stale` — branches already merged into the base or whose upstream is gone, and
worktrees git still registers whose directory no longer exists. A REPORT only: nothing here
deletes a branch or removes a worktree — task 4.6's `cleanup` command acts on this list, on
the human's own confirmation, the same split every other destructive step in this plugin
keeps between "what is true" and "what to do about it"."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess

from quenching.common.config import detect_provider, load_config
from quenching.common.git import _git, _git_run
from quenching.common.output import emit
from quenching.common.tools import spawn_argv
from quenching.git.audit import _reflog
from quenching.git.base import resolve_base
from quenching.git.ignored import classify, declared_regenerable, ignored_entries
from quenching.git.slots import has_own_work
from quenching.session.worktrees import worktree_branch as _worktree_branch
from quenching.session.worktrees import worktree_gitdir as _worktree_gitdir

UNREGISTERED_WORKTREES_KEY = "unregisteredWorktrees"
DATE_SOURCE = "git show -s --format=%cI <tip> (committer date of the observed tip)"


def entry_id(kind: str, identity: str) -> str:
    """A stable identity: derived from the kind and the exact ref or canonical path only."""
    digest = hashlib.sha256(f"{kind}\0{identity}".encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{digest}"


def _merged_branches(cwd: str, base: str, protected: set[str]) -> set[str]:
    out = _git(cwd, "for-each-ref", "--format=%(refname:short)", "refs/heads", "--merged", base)
    return {b for b in out.splitlines() if b and b not in protected}


def _first_parent_chain(cwd: str, base: str) -> set[str]:
    """Every commit on the base's first-parent history: where a branch just cut from the base
    sits, and where a branch merged through a merge commit (its tip a second parent) does not."""
    return set(_git(cwd, "rev-list", "--first-parent", base).split())


def _has_own_commits(cwd: str, branch: str, chain: set[str]) -> bool:
    """A local branch carried work of its own: `slots.has_own_work`, the rule the slot reclaim
    and `promote`'s merge evidence apply (a commit, cherry-pick or revert in its reflog, so a
    branch only fast-forwarded to its base has none), or, with an empty or expired reflog, its
    tip is off the base's first-parent chain. A branch just cut is an ancestor of the base like a
    merged one, and only this evidence tells them apart."""
    ref = f"refs/heads/{branch}"
    tip = _git(cwd, "rev-parse", "--verify", "--quiet", ref).strip()
    if not tip:
        return False
    if _reflog(cwd, ref):
        return has_own_work(cwd, branch)
    return tip not in chain


def _gone_branches(cwd: str, protected: set[str]) -> set[str]:
    out = _git(cwd, "for-each-ref", "--format=%(refname:short)|%(upstream:track)", "refs/heads")
    gone = set()
    for line in out.splitlines():
        name, _, track = line.partition("|")
        if "[gone]" in track and name not in protected:
            gone.add(name)
    return gone


def _merged_remote_branches(cwd: str, base: str, protected: set[str],
                            remote: str = "origin", chain: set[str] | None = None) -> list[dict]:
    """Report fetched remote branches already reachable from ``base`` that carried work of their
    own: the tip is off the base's first-parent chain, or the local branch of the same name has
    commits of its own (a local fast-forward pushed through the base). A remote-tracking reflog
    starts at a ref that already held the tip, so it proves nothing here.

    This is deliberately a read of remote-tracking refs. It does not fetch, prune local refs, or
    claim that the server has not changed since the last fetch; cleanup owns the later, confirmed
    delete of a selected server branch.
    """
    if chain is None:
        chain = _first_parent_chain(cwd, base)
    prefix = f"{remote}/"
    out = _git(cwd, "for-each-ref", "--format=%(refname:short)|%(objectname)",
               f"refs/remotes/{remote}", "--merged", base)
    branches = []
    for line in out.splitlines():
        ref, _, tip = line.partition("|")
        if not ref.startswith(prefix):
            continue
        branch = ref[len(prefix):]
        if not branch or branch == "HEAD" or branch in protected:
            continue
        if tip in chain and not _has_own_commits(cwd, branch, chain):
            continue
        branches.append({"remote": remote, "branch": branch, "reasons": ["merged"]})
    return branches


def _orphan_worktrees(cwd: str) -> list[dict]:
    out = _git(cwd, "worktree", "list", "--porcelain")
    orphans = []
    path = branch = None
    for line in out.splitlines() + [""]:
        if line.startswith("worktree "):
            path = line[len("worktree "):]
        elif line.startswith("branch "):
            branch = line[len("branch "):].removeprefix("refs/heads/")
        elif not line and path is not None:
            if not os.path.isdir(path):
                orphans.append({"path": path, "branch": branch})
            path = branch = None
    return orphans


def _unregistered_worktrees(cwd: str) -> list[dict]:
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    if not common:
        return []
    common = os.path.realpath(common)
    registered = set()
    for line in _git(cwd, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            registered.add(os.path.realpath(line[len("worktree "):]))

    siblings = os.path.dirname(os.path.dirname(common))
    found = []
    try:
        entries = sorted(os.scandir(siblings), key=lambda entry: entry.name)
    except OSError:
        return []
    for entry in entries:
        if not entry.is_dir(follow_symlinks=False):
            continue
        path = os.path.realpath(entry.path)
        if path in registered:
            continue
        git_file = os.path.join(entry.path, ".git")
        if not os.path.isfile(git_file):
            continue
        gitdir = _worktree_gitdir(git_file)
        if not gitdir:
            continue
        try:
            belongs = os.path.commonpath((gitdir, common)) == common and gitdir != common
        except ValueError:
            belongs = False
        if belongs:
            found.append({"path": path, "branch": _worktree_branch(gitdir),
                          "size": _directory_size(path)})
    return found


def _directory_size(path: str) -> int | None:
    size = 0
    try:
        for root, directories, files in os.walk(path, followlinks=False):
            for name in [*directories, *files]:
                try:
                    entry = os.lstat(os.path.join(root, name))
                    size += (getattr(entry, "st_blocks", 0) * 512
                             or entry.st_size)
                except OSError:
                    continue
    except OSError:
        return None
    return size


def _checked_out_branches(cwd: str) -> set[str]:
    out = _git(cwd, "worktree", "list", "--porcelain")
    return {line[len("branch "):].removeprefix("refs/heads/")
            for line in out.splitlines() if line.startswith("branch ")}


def _open_pr_heads(cwd: str) -> set[str] | None:
    """Head branches of open PRs, or None when the lookup is unavailable (no `gh`/`az`, no
    host). An Azure DevOps origin is read with `az repos pr list`, any other with `gh`."""
    try:
        provider = detect_provider(cwd)[0]
    except Exception:  # an unreadable origin keeps the GitHub route
        provider = None
    if provider == "azure-boards":
        argv = ["az", "repos", "pr", "list", "--status=active", "--top=500", "--detect=true",
                "--output=json"]
        key = "sourceRefName"
    else:
        argv = ["gh", "pr", "list", "--state", "open", "--limit", "500", "--json", "headRefName"]
        key = "headRefName"
    try:
        done = subprocess.run(spawn_argv(argv), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        if done.returncode != 0:
            return None
        return {row[key].removeprefix("refs/heads/") for row in json.loads(done.stdout)}
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError,
            AttributeError):
        return None


def _local_branches(cwd: str) -> set[str]:
    out = _git(cwd, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return {b for b in out.splitlines() if b}


def _tip_date(cwd: str, tip: str | None, cache: dict) -> str | None:
    if not tip:
        return None
    if tip not in cache:
        out = _git(cwd, "show", "-s", "--format=%cI", tip).strip()
        cache[tip] = out or None
    return cache[tip]


def _registered_worktrees(cwd: str) -> list[dict]:
    """Registered worktrees with path, HEAD, branch (None when detached) and lock state."""
    out = _git(cwd, "worktree", "list", "--porcelain")
    rows, row = [], None
    for line in out.splitlines() + [""]:
        if line.startswith("worktree "):
            row = {"path": line[len("worktree "):], "head": None, "branch": None,
                   "locked": False}
        elif row is None:
            continue
        elif line.startswith("HEAD "):
            row["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            row["branch"] = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "locked" or line.startswith("locked "):
            row["locked"] = True
        elif not line:
            rows.append(row)
            row = None
    return rows


def _assess_worktrees(cwd: str, base: str, open_prs: set[str] | None) -> list[dict]:
    """Assess each linked worktree whose directory exists. `eligible` is eligibility for a
    guarded removal, never evidence of inactivity: the owner confirms that separately."""
    registered = _registered_worktrees(cwd)
    here = os.path.realpath(_git(cwd, "rev-parse", "--show-toplevel").strip() or cwd)
    rules = declared_regenerable(load_config(cwd))
    assessed = []
    for index, row in enumerate(registered):
        path = row["path"]
        if index == 0 or not os.path.isdir(path):
            continue
        real = os.path.realpath(path)
        blockers = []
        if real == here:
            blockers.append("current-checkout")
        if row["locked"]:
            blockers.append("locked")
        if row["branch"] == base:
            blockers.append("base-branch")
        if open_prs is None:
            blockers.append("pr-lookup-unavailable")
        elif row["branch"] and row["branch"] in open_prs:
            blockers.append("open-pr")
        tip = row["head"]
        if not tip or _git_run(cwd, "merge-base", "--is-ancestor", tip, base)[0] != 0:
            blockers.append("unmerged-head")
        code, status, _err = _git_run(path, "status", "--porcelain",
                                      "--untracked-files=normal")
        if code != 0:
            blockers.append("inspection-failed")
        else:
            lines = [line for line in status.splitlines() if line.strip()]
            if any(not line.startswith("?? ") for line in lines):
                blockers.append("tracked-changes")
            if any(line.startswith("?? ") for line in lines):
                blockers.append("untracked-files")
        ignored = {"regenerable": [], "unrecognized": []}
        entries = ignored_entries(path)
        if entries is None:
            if "inspection-failed" not in blockers:
                blockers.append("inspection-failed")
        else:
            regenerable, unrecognized = classify(path, entries, rules)
            ignored = {"regenerable": regenerable, "unrecognized": unrecognized}
            if unrecognized:
                blockers.append("ignored-content-unrecognized")
        assessed.append({"path": real, "branch": row["branch"], "detached": row["branch"] is None,
                         "tip": tip, "eligible": not blockers, "blockers": blockers,
                         "ignored": ignored})
    return _keep_the_slot_floor(cwd, assessed)


def _assess_held_slots(cwd: str, base: str, remote: str) -> list[dict]:
    """Every held slot with the evidence that would let it go (`slots.reclaim_evidence`): the
    holder spec's phase is read in one batch from the spec backend."""
    from quenching.git import slots as slot_pool

    held = [s for s in slot_pool.survey(cwd)["slots"]
            if s["state"] == "held" and os.path.isdir(s["path"])]
    if not held:
        return []
    rules = declared_regenerable(load_config(cwd))
    specs = [(s["holder"] or {}).get("spec") or "" for s in held]
    phases = slot_pool.spec_phases(cwd, sorted({i for i in specs if i.isdigit()}))
    return [slot_pool.reclaim_evidence(cwd, s, remote, base, rules, phases.get(spec))
            for s, spec in zip(held, specs)]


def _keep_the_slot_floor(cwd: str, assessed: list[dict]) -> list[dict]:
    """A free slot is warm on purpose: the lowest-numbered `shared.worktreeSlots.min` of them are
    preserved (`slot-floor`); only the free slots above the floor are removal candidates."""
    from quenching.git import slots as slot_pool

    view = slot_pool.survey(cwd)
    numbers = {os.path.realpath(s["path"]): s["n"] for s in view["slots"]}
    floor = slot_pool.limits(view["primary"])["min"]
    free = sorted((w for w in assessed if w["path"] in numbers and w["eligible"]),
                  key=lambda w: numbers[w["path"]])
    for w in assessed:
        if w["path"] in numbers:
            w["slot"] = numbers[w["path"]]
    for w in free[:floor]:
        w["eligible"] = False
        w["blockers"].append("slot-floor")
    return assessed


def _manifest(cwd: str, remote: str, report: dict, worktrees: list[dict],
              reclaims: list[dict]) -> list[dict]:
    cache: dict = {}
    entries = []

    def add(kind, identity, scope, ref, tip, status, reasons, argv, cq, recommended):
        entry = {
            "id": entry_id(kind, identity), "kind": kind, "scope": scope, "ref": ref,
            "tip": tip, "tipDate": _tip_date(cwd, tip, cache),
            "dateSource": DATE_SOURCE if tip else None, "status": status,
            "reasons": reasons, "action": {"argv": argv, "cq": cq} if argv else None,
            "recommended": recommended}
        entries.append(entry)
        return entry

    lookup_ok = report["prLookup"] == "ok"

    def tips(ref: str) -> str | None:
        return _git(cwd, "rev-parse", "--verify", "--quiet", ref).strip() or None

    for b in report["staleBranches"]:
        tip = tips(f"refs/heads/{b['branch']}")
        add("branch", b["branch"], "local", b["branch"], tip, "candidate", b["reasons"],
            ["git", "branch", "-d", "--", b["branch"]],
            ["cq", "git", "prune", "--branch", b["branch"], "--expect-tip", tip or ""],
            lookup_ok and tip is not None)
    for b in report["keptBranches"]:
        add("branch", b["branch"], "local", b["branch"], tips(f"refs/heads/{b['branch']}"),
            "preserved", b["reasons"], None, None, False)
    for b in report["remoteBranches"]:
        tip = tips(f"refs/remotes/{remote}/{b['branch']}")
        add("remote-branch", f"{remote}/{b['branch']}", "remote", f"{remote}/{b['branch']}",
            tip, "candidate", b["reasons"],
            ["git", "push", remote, "--delete", b["branch"]],
            ["cq", "git", "prune", "--remote-branch", b["branch"], "--remote", remote,
             "--expect-tip", tip or ""], lookup_ok and tip is not None)
    held = {os.path.realpath(r["path"]) for r in reclaims}
    for w in worktrees:
        if w["path"] in held:
            continue
        ok = w["eligible"]
        entry = add("worktree", w["path"], "local", w["path"], w["tip"],
                    "candidate" if ok else "preserved",
                    (["merged-clean"] + (["slot-above-floor"] if "slot" in w else [])) if ok
                    else w["blockers"],
                    ["git", "worktree", "remove", "--", w["path"]] if ok else None,
                    ["cq", "git", "prune", "--worktree", w["path"], "--expect-tip", w["tip"] or "",
                     "--confirm-inactive"] if ok else None, ok)
        entry["ignored"] = w["ignored"]
    for r in reclaims:
        ok, path = r["eligible"], os.path.realpath(r["path"])
        entry = add("slot", path, "local", path, r["head"], "candidate" if ok else "preserved",
                    ["archived-holder"] if ok else r["blockers"],
                    ["cq", "git", "worktree", "release", "--path", path, "--base",
                     report["base"], "--remote", remote] if ok else None,
                    ["cq", "git", "prune", "--slot", path, "--expect-tip", r["head"] or "",
                     "--confirm-inactive"] if ok else None, ok)
        entry.update(slot=r["slot"], spec=r["spec"], ignored=r["ignored"])
    for w in report["orphanWorktrees"]:
        add("orphan-worktree", w["path"], "local", w["path"], None, "candidate",
            ["directory-missing"], ["git", "worktree", "remove", "--", w["path"]],
            ["cq", "git", "prune", "--worktree", w["path"]], False)
    return entries


def _groups(entries: list[dict]) -> list[dict]:
    """Homogeneous candidates by scope, kind, reasons and date provenance; each group lists the
    immutable ids it covers, so a group never resolves to a target discovered later."""
    groups: dict[tuple, dict] = {}
    for e in entries:
        if e["status"] != "candidate":
            continue
        key = (e["scope"], e["kind"], tuple(e["reasons"]), e["dateSource"])
        g = groups.setdefault(key, {"scope": e["scope"], "kind": e["kind"],
                                    "reasons": e["reasons"], "dateSource": e["dateSource"],
                                    "ids": [], "oldest": None, "newest": None,
                                    "recommended": True})
        g["ids"].append(e["id"])
        if e["tipDate"]:
            g["oldest"] = min(filter(None, (g["oldest"], e["tipDate"])))
            g["newest"] = max(filter(None, (g["newest"], e["tipDate"])))
        g["recommended"] = g["recommended"] and e["recommended"]
    return list(groups.values())


def stale_report(cwd: str, remote: str = "origin") -> dict:
    """The report `cq git stale` prints, and the one `cq git prune` re-reads before it acts."""
    base, _is_default = resolve_base(cwd)
    current = _git(cwd, "branch", "--show-current").strip()
    # `base` is permanent by convention, not "safe to delete just because its tip is an
    # ancestor of base". `current` is excluded because you cannot delete the branch you are
    # standing on; cleanup (task 4.6) re-checks that at delete time regardless, this list is
    # a report, not a promise nothing else changed since.
    protected = {base, current}

    # `merged` is an ancestor of the base that carried work of its own; `gone` only adds to it,
    # because `prune` deletes with `branch -d`, which refuses a branch the base does not hold.
    chain = _first_parent_chain(cwd, base)
    ancestors = _merged_branches(cwd, base, protected)
    gone = _gone_branches(cwd, protected)
    # Guidance first: a branch that must stay is listed with the reason, never offered for
    # deletion. `open-pr` needs the host (`gh` or `az`); when it is unreachable the report says so.
    open_prs = _open_pr_heads(cwd)
    checked_out = _checked_out_branches(cwd)
    reasons: dict[str, set[str]] = {}
    kept: dict[str, set[str]] = {}
    for b in _local_branches(cwd) - protected:
        found = {"gone"} if b in gone else set()
        why = set()
        if open_prs and b in open_prs:
            why.add("open-pr")
        if b in checked_out:
            why.add("worktree")
        if b not in ancestors:
            why.add("unmerged-commits")
        elif not _has_own_commits(cwd, b, chain):
            why.add("no-own-commits")
        else:
            found.add("merged")
        if why:
            kept[b] = why | (found - {"merged"})
        elif found:
            reasons[b] = found
    branches = [{"branch": b, "reasons": sorted(r)} for b, r in sorted(reasons.items())]
    kept_branches = [{"branch": b, "reasons": sorted(r)} for b, r in sorted(kept.items())]
    remote_branches = _merged_remote_branches(cwd, base, protected, remote, chain)
    report = {"ok": True, "base": base, "remote": remote, "staleBranches": branches,
              "keptBranches": kept_branches,
              "prLookup": "ok" if open_prs is not None else "unavailable",
              "remoteBranches": remote_branches, "orphanWorktrees": _orphan_worktrees(cwd),
              UNREGISTERED_WORKTREES_KEY: _unregistered_worktrees(cwd)}
    worktrees = _assess_worktrees(cwd, base, open_prs)
    reclaims = _assess_held_slots(cwd, base, remote)
    manifest = _manifest(cwd, remote, report, worktrees, reclaims)
    report.update(worktrees=worktrees, heldSlots=reclaims, manifest=manifest,
                  groups=_groups(manifest))
    return report


def cmd_stale(args) -> int:
    report = stale_report(os.getcwd(), getattr(args, "remote", None) or "origin")
    base, branches = report["base"], report["staleBranches"]
    remote_branches, worktrees = report["remoteBranches"], report["orphanWorktrees"]
    unregistered_worktrees = report[UNREGISTERED_WORKTREES_KEY]

    lines = [f"base: {base}"]
    lines.append("stale branches:" if branches else "stale branches: none")
    for b in branches:
        lines.append(f"  {b['branch']} ({', '.join(b['reasons'])})")
    kept_rows = report["keptBranches"]
    lines.append("kept branches (not removable):" if kept_rows else "kept branches: none")
    for b in kept_rows:
        lines.append(f"  {b['branch']} ({', '.join(b['reasons'])})")
    lines.append("stale remote branches:" if remote_branches else "stale remote branches: none")
    for b in remote_branches:
        lines.append(f"  {b['remote']}/{b['branch']} ({', '.join(b['reasons'])})")
    lines.append("orphan worktrees:" if worktrees else "orphan worktrees: none")
    for w in worktrees:
        lines.append(f"  {w['path']} ({w['branch'] or 'detached'})")
    lines.append("assessed worktrees:" if report["worktrees"] else "assessed worktrees: none")
    for w in report["worktrees"]:
        state = "candidate" if w["eligible"] else "kept: " + ", ".join(w["blockers"])
        lines.append(f"  {w['path']} ({w['branch'] or 'detached'}, {state})")
        for classification in ("regenerable", "unrecognized"):
            paths = w["ignored"][classification]
            if paths:
                lines.append(f"    ignored.{classification}: {', '.join(paths)}")
    held = report["heldSlots"]
    lines.append("held slots:" if held else "held slots: none")
    for r in held:
        state = "reclaimable" if r["eligible"] else "kept: " + ", ".join(r["blockers"])
        lines.append(f"  slot {r['slot']} {r['path']} (spec {r['spec'] or '-'}, {state})")
    lines.append("unregistered worktrees (repository siblings):"
                 if unregistered_worktrees
                 else "unregistered worktrees (repository siblings): none")
    for w in unregistered_worktrees:
        branch = w["branch"] or "detached"
        size = f"{w['size']} bytes" if w["size"] is not None else "size unavailable"
        lines.append(f"  {w['path']} ({branch}, {size})")

    emit(args.json, report, "\n".join(lines))
    return 0
