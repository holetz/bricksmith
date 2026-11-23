"""`cq git identity` — judge the author and committer emails against the declared allowlist
(`shared.gitIdentity.allowedEmails`), and nothing else. The hooks `cq deploy` ships call it:
`--staged` from `pre-commit`, `--pre-push --remote <name>` from `pre-push` with git's ref lines on
stdin. Exit 0 clean or undeclared, 1 findings, 2 a malformed declaration (fail closed)."""
from __future__ import annotations

import os
import subprocess
import sys

from quenching.common import identity
from quenching.common.output import emit, refuse


def cmd_identity(args) -> int:
    root = os.getcwd()
    try:
        allow = identity.load_allowlist(root)
    except identity.AllowlistInvalid as invalid:
        return refuse(identity.refusal(invalid), args.json)
    mode = "pre-push" if args.pre_push else "staged"
    hook_input = sys.stdin.read() if args.pre_push else ""
    try:
        found = identity.findings(mode, root, allow, hook_input, args.remote)
    except subprocess.CalledProcessError as failed:
        return refuse({"code": "identity-git-failed",
                       "message": f"git failed while reading identities: "
                                  f"{(failed.stderr or '').strip()}"}, args.json)
    payload = {"ok": not found, "mode": mode, "declared": allow is not None,
               "allowedEmails": allow or [], "findings": [f.as_dict() for f in found]}
    text = (f"identity gate ({mode}): no {identity.KEY} declared; nothing judged"
            if allow is None else identity.render("identity gate", mode, found))
    emit(args.json, payload, text)
    return 1 if found else 0
