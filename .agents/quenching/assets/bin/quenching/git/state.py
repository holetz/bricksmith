"""`cq git state` — the checkout facts the git commands read before they act: the current branch,
the porcelain status, the staged paths, the stash entries and the remotes. One fixed argv, run with `audit.SAFE` under
`audit._inert_env` — the repository's `filter.<x>` drivers blanked and the lazy fetch off — so a caller needs no `Bash(git …)` grant to read
them. A REPORT only, exit 0 on any facts; a read that failed is an entry of `errors`
(`complete: false`), never an empty list."""
from __future__ import annotations

import os

from quenching.common.output import emit, refuse
from quenching.git.audit import _error, _inert_env, _lines, _run


def cmd_state(args) -> int:
    cwd = os.getcwd()
    errors: list[dict] = []
    env = _inert_env(cwd, errors)
    code, top = _run(cwd, "rev-parse", "--show-toplevel", env=env)
    if code != 0:
        return refuse({"code": "git-state-not-repository",
                       "message": "the current directory is not inside a git repository"},
                      args.json)
    remotes = {}
    for name in _lines(cwd, "remote", env=env, errors=errors):
        code, url = _run(cwd, "remote", "get-url", "--", name, env=env)
        remotes[name] = url.strip() if code == 0 else None
        if code != 0:
            errors.append(_error(("remote", "get-url"), code, f"remote {name} url unreadable"))
    payload = {
        "ok": True,
        "root": top.strip(),
        "branch": "".join(_lines(cwd, "branch", "--show-current", env=env, errors=errors)) or None,
        "status": _lines(cwd, "status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=all", env=env, errors=errors),
        "staged": _lines(cwd, "diff", "--cached", "--name-only", env=env, errors=errors),
        "stash": _lines(cwd, "stash", "list", env=env, errors=errors),
        "remotes": remotes,
        "errors": errors,
        "complete": not errors,
    }
    human = [f"branch: {payload['branch'] or '(detached)'}",
             f"status: {len(payload['status'])} entries", f"staged: {len(payload['staged'])} path(s)",
             f"stash: {len(payload['stash'])} entries",
             *(f"remote {n}: {u}" for n, u in remotes.items())]
    human += [f"ERROR {e['read']} (exit {e['code']}): {e['message']}" for e in errors]
    emit(args.json, payload, "\n".join(human))
    return 0
