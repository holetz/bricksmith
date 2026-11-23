"""`cq git history` — read a range's log, one commit's show, or a diff, with a fixed argv. The
reviewer's `Bash(git diff:*)` prefix admitted `--output=<path>`, which writes a file; this verb has
no such option, refuses a revision that starts with `-`, and runs with `--no-ext-diff
--no-textconv` under `audit._inert_env`. Read-only: stdout is the git output, nothing is written."""
from __future__ import annotations

import os

from quenching.common.git import _git_run
from quenching.common.output import FINDINGS, emit, refuse
from quenching.git.audit import SAFE, _inert_env

_PLAIN = ("--no-color", "--no-ext-diff", "--no-textconv")


def cmd_history(args) -> int:
    cwd = os.getcwd()
    revs = list(args.revs)
    bad = [r for r in revs if not r or r.startswith("-")]
    if bad:
        return refuse({"code": "git-history-bad-rev",
                       "message": f"a revision may not start with '-': {bad[0]!r}"}, args.json)
    if args.kind == "diff" and len(revs) not in (1, 2):
        return refuse({"code": "git-history-arity", "message": "diff takes one or two revisions"},
                      args.json)
    if args.kind == "show" and len(revs) != 1:
        return refuse({"code": "git-history-arity", "message": "show takes exactly one revision"},
                      args.json)
    if args.kind == "log" and not revs:
        return refuse({"code": "git-history-arity", "message": "log takes at least one revision"},
                      args.json)
    errors: list[dict] = []
    env = _inert_env(cwd, errors)
    if args.kind == "log":
        argv = ["log", *_PLAIN, f"--max-count={args.max_count}", "--stat", *revs]
    elif args.kind == "show":
        argv = ["show", *_PLAIN, "--stat", "--patch", *revs]
    else:
        argv = ["diff", *_PLAIN, *revs]
    argv += ["--", *args.path]
    code, out, err = _git_run(cwd, *SAFE, *argv, env=env)
    if code != 0:
        emit(args.json, {"ok": False, "code": code, "message": err.strip()},
             f"error: {err.strip()}")
        return FINDINGS
    emit(args.json, {"ok": True, "kind": args.kind, "output": out}, out.rstrip("\n"))
    return 0
