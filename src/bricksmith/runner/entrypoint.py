"""The common deployed entrypoint: ``python -m bricksmith.runner.entrypoint`` from a job task.

It resolves the same explicit context as the local CLI (``--env``, ``--profile``, ``--target``
and the process environment) and applies either a reviewed plan or a selection.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from bricksmith.cli.common import BricksmithParser, add_global_options, context_from_args, emit_error
from bricksmith.errors import EXIT_UNEXPECTED, BricksmithError
from bricksmith.runner.service import (
    add_run_options,
    add_selection_options,
    apply_from_args,
    plan_from_args,
    read_plan,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bricksmith.runner.io import TableIO


def run_entrypoint(argv: Sequence[str] | None = None, *, table_io: TableIO | None = None) -> int:
    """Apply a plan or selection from ``argv``; return the CLI exit code (``table_io`` injects a backend)."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = BricksmithParser(prog="python -m bricksmith.runner.entrypoint")
    add_global_options(parser, top=True)
    add_selection_options(parser)
    add_run_options(parser)
    parser.add_argument("--plan", help="reviewed plan file; without it the selection options build one")
    try:
        args = parser.parse_args(args_list)
        if args.plan:
            from bricksmith.graph import load_graph  # noqa: PLC0415 - keeps import light

            graph = load_graph(args.project, context=context_from_args(args))
            plan = read_plan(args.plan)
        else:
            plan, graph = plan_from_args(args)
        return apply_from_args(args, plan, graph, table_io)
    except BricksmithError as error:
        emit_error(error, as_json="--json" in args_list)
        return error.exit_code
    except Exception as error:  # noqa: BLE001 - last-resort mapping to the unexpected exit code
        emit_error(BricksmithError(f"{type(error).__name__}: {error}"), as_json="--json" in args_list)
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    sys.exit(run_entrypoint())
