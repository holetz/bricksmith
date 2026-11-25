"""The read-only ``security`` pillar: its live questions, and the leak gate's scanner.

``leak`` reads and answers through its exit code (0 clean, 1 findings, 2 configuration); the hook
that calls it is what refuses the commit or the push. ``--staged`` and ``--pre-push`` also judge
the declared email allowlist (`shared.gitIdentity.allowedEmails`); `cq git identity` runs that
check alone, for targets that do not deploy this module.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

from quenching.common import identity, leak
from quenching.common.front import resolve_root
from quenching.common.output import NoAbbrevParser, emit, refuse
from quenching.common.version import VERSION
from quenching.controls.evaluate import owner_controls, summary_line
from quenching.security.probe import build_report


def build_parser() -> argparse.ArgumentParser:
    parser = NoAbbrevParser(
        prog="cq security",
        description="report live security questions without changing the repository",
    )
    parser.add_argument("--root", help="repository root (default: current directory)")
    parser.add_argument("--version", action="store_true", help="print the security version")
    parser.add_argument("command", nargs="?", choices=("audit", "status", "leak"), default="audit",
                        help="read-only audit (the default), its status alias, or the leak gate")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true",
                      help="leak: staged content and the configured author email (pre-commit)")
    mode.add_argument("--pre-push", action="store_true",
                      help="leak: the commits named on stdin by the pre-push hook")
    mode.add_argument("--all", action="store_true", help="leak: every tracked file")
    parser.add_argument("--remote", help="leak --pre-push: the destination remote's name (the "
                                         "hook's first argument)")
    parser.add_argument("--generic-only", action="store_true",
                        help="leak: skip the private denylist (CI)")
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    return parser


def _human(payload: dict) -> str:
    states = ", ".join(f"{item['key']}={item['state']}" for item in payload["questions"])
    return (f"security audit — {payload['root']} (read-only); {states}; "
            f"{summary_line(payload['controls'])}")


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"cq security {VERSION}")
        return 0
    parser = build_parser()
    args = parser.parse_args(argv)
    as_json = bool(args.json)
    root = resolve_root(args.root)
    if not os.path.isdir(root):
        return refuse({
            "code": "security-root-missing",
            "exit": 2,
            "root": root,
            "message": f"repository root does not exist: {root}",
        }, as_json)
    if args.command == "leak":
        return _leak(args, root, as_json)
    if args.staged or args.pre_push or args.all or args.generic_only or args.remote:
        parser.error("--staged, --pre-push, --all, --remote and --generic-only belong to `leak`")
    payload = build_report(root).as_dict()
    payload["controls"] = owner_controls("security", root)
    emit(as_json, payload, _human(payload))
    return 0 if payload["ok"] else 2


def _leak(args: argparse.Namespace, root: str, as_json: bool) -> int:
    if not (args.staged or args.pre_push or args.all):
        return refuse({"code": "leak-mode-missing",
                       "message": "`leak` needs one of --staged, --pre-push or --all"}, as_json)
    try:
        pats = leak.patterns(generic_only=args.generic_only)
    except leak.DenylistMissing as missing:
        return refuse({"code": "leak-denylist-missing", "path": str(missing.path),
                       "message": f"{missing}; create it (one regex per line), set "
                                  f"{leak.DENYLIST_ENV}, or pass --generic-only"}, as_json)
    try:
        allow = identity.load_allowlist(root)
    except identity.AllowlistInvalid as invalid:
        return refuse(identity.refusal(invalid), as_json)
    mode = "staged" if args.staged else "pre-push" if args.pre_push else "all"
    hook_input = sys.stdin.read() if args.pre_push else ""
    try:
        if args.staged:
            found = leak.scan_staged(root, pats)
        elif args.pre_push:
            found = leak.scan_push(root, hook_input, pats, args.remote)
        else:
            found = leak.scan_all(root, pats)
        found += identity.findings(mode, root, allow, hook_input, args.remote)
    except subprocess.CalledProcessError as failed:
        return refuse({"code": "leak-git-failed",
                       "message": f"git failed while scanning: {(failed.stderr or '').strip()}"}, as_json)
    payload = {"ok": not found, "mode": mode, "genericOnly": bool(args.generic_only),
               "findings": [f.as_dict() for f in found]}
    emit(as_json, payload, identity.render("leak gate", mode, found))
    return 1 if found else 0
