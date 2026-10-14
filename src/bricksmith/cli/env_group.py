"""The ``env`` command group: ``env check`` runs the staged test-environment preflight."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.context import DEFAULT_ENVIRONMENT
from bricksmith.errors import EXIT_CONFIG, EXIT_OK, BricksmithError
from bricksmith.preflight import failed_configuration, run_preflight

if TYPE_CHECKING:
    import argparse


def _check(args: argparse.Namespace) -> int:
    try:
        context = context_from_args(args)
    except BricksmithError as error:
        report = failed_configuration(args.env or os.environ.get("BRICKSMITH_TEST_ENV") or DEFAULT_ENVIRONMENT, error)
    else:
        report = run_preflight(context, environ=os.environ, offline_only=args.offline)
    emit(args, report.to_dict(), report.to_text())
    return EXIT_CONFIG if report.failed else EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``env check``."""
    group = subparsers.add_parser("env", help="check the selected test environment", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="staged preflight of the selected environment", parents=[global_parent()])
    check.add_argument("--offline", action="store_true", help="run only the offline configuration stage")
    check.set_defaults(handler=_check)
