"""`cq git prune` — delete ONE item `cq git stale` reports, and nothing it does not.

The report is re-read here, at delete time, so a name the caller passes is acted on only when
the fresh report still lists it under the matching class. The deletes are the safe forms only —
`branch -d`, `worktree remove`, `push --delete` of a reported remote branch leased on the sha
the report read (a tip that moved since is refused; a branch already gone from the remote only loses its
tracking ref) — never `-D`, a plain `--force` or a
refspec the caller wrote. Runs with `audit.SAFE`.

`--slot` is the one non-delete: a held slot the report lists as a `kind: slot` candidate (its
spec archived, HEAD in the remote base, clean, nobody in it) is given back to the pool through
`cq git worktree release` itself, never removed.

Exit 0 deleted or released · 1 git refused (not fully merged, dirty worktree) and the item
stands · 2 refusal: not in the report."""
from __future__ import annotations

import argparse
import os

from quenching.common.git import _git, _git_run
from quenching.common.output import FINDINGS, OK, emit, refuse
from quenching.git.audit import SAFE
from quenching.git.push import remote_ok
from quenching.git.stale import entry_id, stale_report


def _same_path(a: str | None, b: str) -> bool:
    return bool(a) and (a == b or os.path.realpath(a) == os.path.realpath(b))


def _release_slot(args, report: dict) -> int:
    """`--slot`: release a `kind: slot` candidate of the fresh report, at the approved tip and
    id, and only on the owner's inactivity confirmation."""
    from quenching.git import slots

    entry = next((e for e in report["manifest"] if e["kind"] == "slot"
                  and e["status"] == "candidate" and _same_path(e["ref"], args.slot)), None)
    if entry is None:
        return refuse({"code": "git-prune-not-reported",
                       "message": f"`cq git stale` does not report the slot {args.slot} as "
                                  "reclaimable"}, args.json)
    if not args.confirm_inactive:
        return refuse({"code": "git-prune-inactivity-unconfirmed",
                       "message": f"slot {args.slot} needs --confirm-inactive: the evidence is "
                                  "not the owner's word that nobody is using it"}, args.json)
    if not args.expect_tip:
        return refuse({"code": "git-prune-tip-required",
                       "message": "a slot is released only against its approved tip"}, args.json)
    if entry["tip"] != args.expect_tip:
        return refuse({"code": "git-prune-tip-changed",
                       "message": f"slot {args.slot} is at {entry['tip'] or 'no tip'}, not the "
                                  f"approved {args.expect_tip}; read a fresh report"}, args.json)
    if args.id and args.id != entry["id"]:
        return refuse({"code": "git-prune-id-mismatch",
                       "message": f"{args.id} does not identify the slot {args.slot}"}, args.json)
    return slots.release(argparse.Namespace(json=args.json, path=entry["ref"], branch=None,
                                            base=report["base"], remote=args.remote))


def cmd_prune(args) -> int:
    cwd = os.getcwd()
    if args.remote_branch is not None and not remote_ok(cwd, args.remote):
        return refuse({"code": "git-prune-remote-unknown",
                       "message": f"not a configured remote: {args.remote}"}, args.json)
    report = stale_report(cwd, args.remote)
    if getattr(args, "slot", None) is not None:
        return _release_slot(args, report)
    live = None
    if args.branch is not None:
        kind, target = "branch", args.branch
        listed = any(b["branch"] == target for b in report["staleBranches"])
        argv = ("branch", "-d", "--", target)
    elif args.worktree is not None:
        kind, target = "worktree", args.worktree
        orphan = any(_same_path(w["path"], target) for w in report["orphanWorktrees"])
        live = next((w for w in report["worktrees"]
                     if w["eligible"] and _same_path(w["path"], target)), None)
        listed = orphan or live is not None
        argv = ("worktree", "remove", "--", target)
    else:
        kind, target = "remote-branch", args.remote_branch
        listed = any(b["branch"] == target and b["remote"] == args.remote
                     for b in report["remoteBranches"])
        seen = _git(cwd, "rev-parse", "--verify", "--quiet",
                    f"refs/remotes/{args.remote}/{target}").strip()
        argv = ("push", "--delete", "--porcelain",
                f"--force-with-lease=refs/heads/{target}:{seen}",
                "--", args.remote, f"refs/heads/{target}")
    if not listed:
        return refuse({"code": "git-prune-not-reported",
                       "message": f"`cq git stale` does not report the {kind} {target}"},
                      args.json)
    if listed and live is not None:
        if not args.confirm_inactive:
            return refuse({"code": "git-prune-inactivity-unconfirmed",
                           "message": f"worktree {target} needs --confirm-inactive: eligibility "
                                      "is not evidence that nobody is using it"}, args.json)
        if not args.expect_tip:
            return refuse({"code": "git-prune-tip-required",
                           "message": "a live worktree is removed only against its approved tip"},
                          args.json)
    observed = {"branch": lambda: _git(cwd, "rev-parse", "--verify", "--quiet",
                                       f"refs/heads/{target}").strip(),
                "remote-branch": lambda: seen if kind == "remote-branch" else "",
                "worktree": lambda: live["tip"] if live else None}[kind]()
    if listed and args.expect_tip and observed is not None and observed != args.expect_tip:
        return refuse({"code": "git-prune-tip-changed",
                       "message": f"{kind} {target} is at {observed or 'no tip'}, not the "
                                  f"approved {args.expect_tip}; read a fresh report"}, args.json)
    if listed and args.id:
        ref = f"{args.remote}/{target}" if kind == "remote-branch" else (
            live["path"] if live else target)
        entry_kind = "orphan-worktree" if kind == "worktree" and live is None else kind
        if args.id != entry_id(entry_kind, ref):
            return refuse({"code": "git-prune-id-mismatch",
                           "message": f"{args.id} does not identify the {kind} {target}"},
                          args.json)
    if kind == "remote-branch" and not seen:
        return refuse({"code": "git-prune-no-lease",
                       "message": f"no tracking ref to lease for {args.remote}/{target}"},
                      args.json)
    code, out, err = _git_run(cwd, *SAFE, *argv)
    payload = {"ok": code == 0, "kind": kind, "target": target, "remote": args.remote}
    if code != 0 and kind == "remote-branch":
        gone, _o, _e = _git_run(cwd, *SAFE, "ls-remote", "--exit-code", "--heads",
                                args.remote, f"refs/heads/{target}")
        if gone == 2:
            code, _o, err = _git_run(cwd, *SAFE, "branch", "-dr", f"{args.remote}/{target}")
            if code == 0:
                payload.update(ok=True, message="already gone on the remote; tracking ref removed")
                emit(args.json, payload, f"pruned {kind} {target} (already gone on the remote)")
                return OK
    if code != 0:
        reason = "\n".join(x for x in (out.strip(), err.strip()) if x)
        payload["message"] = reason
        emit(args.json, payload, f"{kind} {target} refused: {reason}")
        return FINDINGS
    emit(args.json, payload, f"pruned {kind} {target}")
    return OK
