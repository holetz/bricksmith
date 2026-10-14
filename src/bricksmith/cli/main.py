"""Entry point: ``bricksmith <group> <command>``."""

from __future__ import annotations

import importlib
import sys
from typing import TYPE_CHECKING

from bricksmith.cli.common import BricksmithParser, add_global_options, emit_error
from bricksmith.cli.groups import GROUPS
from bricksmith.errors import EXIT_UNEXPECTED, BricksmithError

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> BricksmithParser:
    """Build the parser and let every registered group add itself."""
    parser = BricksmithParser(prog="bricksmith", description="Databricks data project framework.")
    add_global_options(parser, top=True)
    subparsers = parser.add_subparsers(dest="group", required=True, parser_class=BricksmithParser)
    for path in GROUPS:
        importlib.import_module(path).register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the documented exit code."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args_list
    try:
        args = build_parser().parse_args(args_list)
        return int(args.handler(args))
    except BricksmithError as error:
        emit_error(error, as_json=as_json)
        return error.exit_code
    except Exception as error:  # noqa: BLE001 - last-resort mapping to the unexpected exit code
        emit_error(BricksmithError(f"{type(error).__name__}: {error}"), as_json=as_json)
        return EXIT_UNEXPECTED
