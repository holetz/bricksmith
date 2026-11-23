"""The ``codex`` pillar: whether the Codex quota allows a call, and Codex writing one eligible task.

    cq codex probe [--json]    exit 0 available · 1 unavailable · 2 unknown (the quota)
    cq codex eligible --spec <id> --task <tid> [--json]
                               exit 0 Codex may write the task · 1 Claude writes it · 2 refusal
    cq codex implement --spec <id> --task <tid> --worktree <dir> --prompt <file> [--run <name>]
                       [--json]
                               exit 0 written inside files: · 1 Claude writes it ·
                               2 refusal or the git guard saw a divergence
"""
from __future__ import annotations

import argparse
import json
import os

from quenching.codex.config import load_settings
from quenching.codex.eligible import run_eligible
from quenching.codex.implement import run_implement
from quenching.codex.probe import EXIT, probe
from quenching.common.front import resolve_root
from quenching.common.output import NoAbbrevParser, emit, refuse
from quenching.common.version import VERSION


def build_parser() -> argparse.ArgumentParser:
    common = NoAbbrevParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS,
                        help="repository root whose .agents/quenching.json is read")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="print machine-readable output")
    parser = NoAbbrevParser(prog="cq codex", parents=[common],
                            description="read the Codex quota and let Codex write one eligible spec "
                                        "task")
    parser.add_argument("--version", action="store_true", help="print the codex version")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", parents=[common],
                   help="exit 0 when the quota allows a call, 1 when it does not, 2 when unknown")
    eligible = sub.add_parser("eligible", parents=[common],
                              help="exit 0 when Codex may write the task, 1 when Claude writes it")
    eligible.add_argument("--spec", required=True, help="the spec id")
    eligible.add_argument("--task", required=True, help="the task id, e.g. 2.1")
    implement = sub.add_parser("implement", parents=[common],
                               help="let Codex write one eligible task inside a worktree")
    implement.add_argument("--spec", required=True, help="the spec id")
    implement.add_argument("--task", required=True, help="the task id, e.g. 2.1")
    implement.add_argument("--worktree", required=True, help="the checkout Codex writes in")
    implement.add_argument("--prompt", required=True, help="file whose text is sent on stdin")
    implement.add_argument("--run", help="run name under .quenching/codex/ (default: UTC stamp)")
    return parser


def _implement(root: str, args, as_json: bool) -> int:
    settings, warnings = load_settings(root)
    payload, code = run_implement(root, settings, args.spec, args.task, args.worktree,
                                  args.prompt, args.run)
    if code == 2 and payload.get("code") != "codex-git-guard":
        return refuse({k: v for k, v in payload.items() if k != "ok"}, as_json)
    payload["warnings"] = warnings
    tokens = payload.get("tokens", "not measured")
    if isinstance(tokens, dict):
        tokens = tokens.get("total_tokens", json.dumps(tokens))
    emit(as_json, payload,
         f"codex implement — {args.spec} {args.task}: {payload['reason']}; tokens: {tokens}; "
         f"quota delta: {payload['quotaDelta']}; stop-loss: {payload['stopLoss']}")
    return code


def _eligible(root: str, args, as_json: bool) -> int:
    settings, warnings = load_settings(root)
    payload, code = run_eligible(root, settings, args.spec, args.task)
    if code == 2:
        return refuse({k: v for k, v in payload.items() if k != "ok"}, as_json)
    payload["warnings"] = warnings
    verdict = f"{payload['class']} -> {payload['model']}" if code == 0 else "claude"
    emit(as_json, payload, f"codex eligible — {args.spec} {args.task}: {verdict}; "
                           f"{payload['reason']}")
    return code


def _probe(root: str, as_json: bool) -> int:
    settings, warnings = load_settings(root)
    payload = probe(settings)
    payload = {"ok": payload["state"] == "available", **payload, "warnings": warnings}
    source = payload["source"] or "no source"
    emit(as_json, payload, f"codex probe — {payload['state']} ({source}): {payload['reason']}")
    return EXIT[payload["state"]]


def main(argv: list[str]) -> int:
    if "--version" in argv:
        print(f"cq codex {VERSION}")
        return 0
    args = build_parser().parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    root = resolve_root(getattr(args, "root", None))
    if not os.path.isdir(root):
        return refuse({"code": "codex-root-missing", "root": root,
                       "message": f"repository root does not exist: {root}"}, as_json)
    if args.command == "probe":
        return _probe(root, as_json)
    if args.command == "eligible":
        return _eligible(root, args, as_json)
    return _implement(root, args, as_json)
