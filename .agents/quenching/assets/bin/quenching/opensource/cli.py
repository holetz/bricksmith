"""The ``cq opensource`` route: read-only ``doctor``, ``status`` and ``readiness``, and the bounded ``apply``."""
from __future__ import annotations

import argparse
import os

from quenching.common.front import build_parser as build_front_parser, resolve_root
from quenching.common.output import emit, refuse
from quenching.common.version import VERSION
from quenching.opensource.aggregate import doctor, status
from quenching.opensource.apply import Refusal, apply
from quenching.opensource.readiness import DEFAULT_TIMEOUT_S, assess


def build_parser() -> argparse.ArgumentParser:
    parser = build_front_parser(
        "cq opensource", "aggregate the open source catalog by owner and report the baseline level",
        (("doctor", "probe applicability, template integrity and the opensource findings"),
         ("status", "read every owner's catalog answers and the baseline level, read-only")),
    )
    parser.add_argument("--version", action="store_true", help="print the opensource version")
    sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    child = sub.add_parser("apply", help="write one community artifact from its pinned template")
    child.add_argument("--artifact", required=True, help="the template's artifact name")
    child.add_argument("--var", action="append", default=[], metavar="KEY=VALUE",
                       help="a declared placeholder value (repeatable); `spdx` selects the license")
    child.add_argument("--dry-run", action="store_true", help="report without writing")
    child.add_argument("--json", action="store_true", help="print machine-readable output")
    child = sub.add_parser("readiness", help="assess tree and history before making the repository public, read-only")
    child.add_argument("--evidence", metavar="PATH",
                       help="the owner's reviewed dependency-license and history-consent evidence (JSON)")
    child.add_argument("--denylist", metavar="PATH",
                       help="the owner's sensitive terms, one per line, in a file outside the target")
    child.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S, metavar="SECONDS",
                       help="per-scanner bound")
    child.add_argument("--json", action="store_true", help="print machine-readable output")
    return parser


def _human(payload: dict) -> str:
    baseline = payload["baseline"]
    codes = ", ".join(sorted({row["code"] for row in payload["findings"]})) or "none"
    return (f"opensource {payload.get('applicability', {}).get('state', 'status')} — {payload['root']}; "
            f"baseline {baseline['version']} level {baseline['level']}; findings: {codes}")


def _readiness_human(payload: dict) -> str:
    unmeasured = ", ".join(row["class"] for row in payload["coverage"] if row["state"] != "measured") or "none"
    return (f"opensource readiness — {len(payload['blockers'])} blocker(s), {len(payload['warnings'])} "
            f"warning(s); unmeasured: {unmeasured}; never a publication approval")


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"cq opensource {VERSION}")
        return 0
    args = build_parser().parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    if not args.cmd:
        return refuse({"code": "os-no-command", "message": "choose `doctor` or `status`"}, as_json)
    root = resolve_root(args.root)
    if not os.path.isdir(root):
        return refuse({"code": "os-root-missing", "root": root,
                       "message": f"repository root does not exist: {root}"}, as_json)
    if args.cmd == "apply":
        values = dict(item.partition("=")[::2] for item in args.var)
        try:
            result = apply(root, args.artifact, values, dry_run=args.dry_run)
        except Refusal as refusal:
            return refuse(refusal.payload, as_json)
        verb = "would write" if result["dryRun"] else "wrote"
        emit(as_json, result, f"opensource apply — {verb} {result['target']} from {result['source']}")
        return 0
    if args.cmd == "readiness":
        payload, code = assess(root, args.evidence, args.timeout, denylist=args.denylist)
        emit(as_json, payload, _readiness_human(payload))
        return code
    payload = doctor(root) if args.cmd == "doctor" else status(root)
    emit(as_json, payload, _human(payload))
    return 0 if payload["ok"] else 1
