"""The ``registry`` command group."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

from bricksmith.cli.common import emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION
from bricksmith.registry.checks import load_baseline, run_check, write_baseline
from bricksmith.registry.model import load_registry
from bricksmith.registry.preset import default_preset, load_preset

if TYPE_CHECKING:
    import argparse


def _check(args: argparse.Namespace) -> int:
    registry = load_registry(args.registry)
    preset = load_preset(args.preset) if Path(args.preset).exists() else default_preset()
    today = dt.date.fromisoformat(args.today) if args.today else None
    baseline = load_baseline(args.baseline)
    report = run_check(registry, preset, baseline, today)
    if args.write_baseline:
        write_baseline(args.baseline, report)
        report = run_check(registry, preset, load_baseline(args.baseline), today)
    emit(args, report.to_dict(), report.to_text())
    return EXIT_OK if report.ok else EXIT_VALIDATION


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``registry check``, which runs offline with no Spark."""
    group = subparsers.add_parser("registry", help="column registry and naming checks", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="check the registry offline", parents=[global_parent()])
    check.add_argument("--registry", default="registry/columns.toml", help="registry TOML file")
    check.add_argument("--preset", default="registry/naming.toml", help="preset TOML file (defaults if absent)")
    check.add_argument("--baseline", default="registry/baseline.json", help="ratchet baseline file")
    check.add_argument("--write-baseline", action="store_true", help="tolerate all current errors")
    check.add_argument("--today", help="ISO date used for exception expiry (default: today, UTC)")
    check.set_defaults(handler=_check)
