"""`cq git refresh` — bring the LOCAL base branch up to `<remote>/<base>`, fast-forward only.

A PR merged through the provider moves no local ref, so the base the next piece of work starts
from sits commits behind the remote. This verb is the one safe pull the close of a cycle runs:

- it fetches `<base>` into `refs/remotes/<remote>/<base>` and nothing else;
- the local base moves only when it is an ancestor of the remote tip — a diverged or ahead base
  is reported and never touched, so no commit is ever discarded;
- where a worktree has the base checked out, `git merge --ff-only` runs THERE: git itself keeps
  uncommitted changes that do not collide with the incoming diff and refuses (exit 1) the ones
  that do — nothing is stashed, reset or overwritten, and no branch is switched;
- where no worktree holds the base, the ref moves with `update-ref` against the tip it read.

A base the remote does not have (never pushed) is a skip, told apart from a failed fetch by
`ls-remote --exit-code`. Hooks stay off (`SAFE`). Exit 0 up to date, updated or skipped · 1 refused (fetch failed, diverged, git
refused the merge) · 2 refusal (a malformed name)."""
from __future__ import annotations

import os

from quenching.common.git import _git_run
from quenching.common.output import FINDINGS, OK, emit, refuse
from quenching.git.audit import SAFE, _branch_ok
from quenching.git.base import resolve_base
from quenching.git.worktree import _registered_worktrees


def _rev(cwd: str, ref: str) -> str | None:
    code, out, _err = _git_run(cwd, *SAFE, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return out.strip() if code == 0 and out.strip() else None


def _is_ancestor(cwd: str, older: str, newer: str) -> bool:
    return _git_run(cwd, *SAFE, "merge-base", "--is-ancestor", older, newer)[0] == 0


def _dirty(path: str) -> bool:
    code, out, _err = _git_run(path, *SAFE, "status", "--porcelain")
    return code == 0 and bool(out.strip())


def _report(args, payload: dict, human: str, code: int) -> int:
    emit(args.json, payload, human)
    return code


def cmd_refresh(args) -> int:
    cwd = os.getcwd()
    base = args.base or resolve_base(cwd)[0]
    for label, value in (("base", base), ("remote", args.remote)):
        if not value or value.startswith("-") or not _branch_ok(cwd, value):
            return refuse({"code": "git-refresh-ref-invalid",
                           "message": f"{label} is not a valid name: {value}"}, args.json)
    remote_ref = f"refs/remotes/{args.remote}/{base}"
    payload: dict = {"ok": False, "base": base, "remote": args.remote}
    code, _out, err = _git_run(cwd, *SAFE, "fetch", "--quiet", args.remote,
                               f"+refs/heads/{base}:{remote_ref}")
    if code != 0:
        # ls-remote's exit 2 means the remote answered and has no such branch: a base never
        # pushed is a skip, not a failure. Any other code keeps the fetch's own error.
        gone = _git_run(cwd, *SAFE, "ls-remote", "--exit-code", "--heads", args.remote,
                        f"refs/heads/{base}")[0]
        if gone == 2:
            payload.update(ok=True, action="skipped", reason="no-remote-base")
            return _report(args, payload, f"nothing to refresh: no-remote-base for {base}", OK)
        payload.update(action="refused", reason="fetch-failed", message=err.strip())
        return _report(args, payload, f"not refreshed: fetch of {args.remote}/{base} failed: "
                                      f"{err.strip()}", FINDINGS)
    remote = _rev(cwd, remote_ref)
    local = _rev(cwd, f"refs/heads/{base}")
    if remote is None or local is None:
        payload.update(ok=True, action="skipped",
                       reason="no-local-base" if local is None else "no-remote-base")
        return _report(args, payload, f"nothing to refresh: {payload['reason']} for {base}", OK)
    payload.update({"from": local, "to": remote})
    if local == remote:
        payload.update(ok=True, action="current")
        return _report(args, payload, f"{base} is already at {args.remote}/{base}", OK)
    if _is_ancestor(cwd, remote, local):
        payload.update(ok=True, action="ahead")
        return _report(args, payload, f"{base} is ahead of {args.remote}/{base}; left as it is", OK)
    if not _is_ancestor(cwd, local, remote):
        payload.update(action="diverged", reason="diverged")
        return _report(args, payload, f"not refreshed: {base} and {args.remote}/{base} diverged; "
                                      "left untouched", FINDINGS)
    holder = next((p for p, branch in _registered_worktrees(cwd) if branch == base), None)
    if holder:
        payload.update(where=holder, dirty=_dirty(holder))
        code, _out, err = _git_run(holder, *SAFE, "merge", "--ff-only", "--quiet", remote)
        if code != 0:
            payload.update(action="refused", reason="merge-refused", message=err.strip())
            return _report(args, payload, f"not refreshed: git refused the fast-forward in "
                                          f"{holder}: {err.strip()}", FINDINGS)
    else:
        code, _out, err = _git_run(cwd, *SAFE, "update-ref", "-m", f"cq git refresh: {remote}",
                                   f"refs/heads/{base}", remote, local)
        if code != 0:
            payload.update(action="refused", reason="update-refused", message=err.strip())
            return _report(args, payload, f"not refreshed: {err.strip()}", FINDINGS)
    payload.update(ok=True, action="updated")
    where = f" in {holder}" if holder else ""
    return _report(args, payload, f"{base} fast-forwarded {local[:7]} -> {remote[:7]}{where}", OK)
