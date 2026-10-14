"""Shared parser pieces: global options, context building and output helpers."""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING

from bricksmith.context import resolve_context
from bricksmith.env import load_dotenv
from bricksmith.errors import BricksmithError, UsageError

if TYPE_CHECKING:
    from bricksmith.context import ExecutionContext


class BricksmithParser(argparse.ArgumentParser):
    """Parser that raises ``UsageError`` instead of exiting, so exit codes stay documented."""

    def error(self, message: str) -> None:  # type: ignore[override]
        """Raise a usage error."""
        raise UsageError(message)


def add_global_options(parser: argparse.ArgumentParser, *, top: bool) -> None:
    """Add the global options; on subparsers they keep the top-level value unless repeated."""
    keep = {} if top else {"default": argparse.SUPPRESS}
    parser.add_argument("--json", action="store_true", help="machine-readable JSON output", **keep)  # type: ignore[arg-type]
    parser.add_argument("--env", help="environment to activate (default: free)", **keep)  # type: ignore[arg-type]
    parser.add_argument("--env-file", help="dotenv file to load explicitly", **keep)  # type: ignore[arg-type]
    parser.add_argument("--profile", help="Databricks profile (overrides the environment's)", **keep)  # type: ignore[arg-type]
    parser.add_argument("--target", help="bundle target (independent of the workspace)", **keep)  # type: ignore[arg-type]


def global_parent() -> argparse.ArgumentParser:
    """Return a parent parser carrying the global options for group subcommands."""
    parent = BricksmithParser(add_help=False)
    add_global_options(parent, top=False)
    return parent


def context_from_args(args: argparse.Namespace) -> ExecutionContext:
    """Resolve the context from global options, the process environment and an explicit dotenv."""
    dotenv = load_dotenv(args.env_file) if args.env_file else None
    return resolve_context(environment=args.env, profile=args.profile, target=args.target, dotenv=dotenv)


def emit(args: argparse.Namespace, payload: dict[str, object], text: str) -> None:
    """Print ``payload`` as sorted JSON with ``--json``, otherwise ``text``."""
    if args.json:
        sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(text.rstrip("\n") + "\n")


def emit_error(error: BricksmithError, *, as_json: bool) -> None:
    """Write a structured error to stderr."""
    if as_json:
        sys.stderr.write(json.dumps({"error": error.to_dict()}, sort_keys=True) + "\n")
    else:
        sys.stderr.write(f"error[{error.code}]: {error.message}\n")
