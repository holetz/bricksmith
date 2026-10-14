"""The ``context`` command group."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bricksmith.cli.common import context_from_args, emit, global_parent

if TYPE_CHECKING:
    import argparse

    from bricksmith.context import ExecutionContext


def _text(context: ExecutionContext) -> str:
    lines = [f"{key}: {value}" for key, value in context.to_dict().items() if key != "sources"]
    lines.append("sources: " + ", ".join(f"{k}={v}" for k, v in context.sources.items()))
    return "\n".join(lines)


def _show(args: argparse.Namespace) -> int:
    context = context_from_args(args)
    emit(args, context.to_dict(), _text(context))
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``context show``, which resolves the context offline."""
    group = subparsers.add_parser("context", help="inspect the resolved execution context", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show", help="print the resolved context (no remote calls)", parents=[global_parent()])
    show.set_defaults(handler=_show)
