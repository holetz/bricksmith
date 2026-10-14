"""The ``tasks`` command group: offline discovery, graph and validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bricksmith.cli.common import emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION

if TYPE_CHECKING:
    import argparse

    from bricksmith.graph import TaskGraph


def _graph(args: argparse.Namespace) -> TaskGraph:
    from bricksmith.graph import load_graph  # noqa: PLC0415 - keeps parser construction light

    return load_graph(args.project)


def _list(args: argparse.Namespace) -> int:
    graph = _graph(args)
    lines = [f"{node.id}  kind={node.kind}  outputs={','.join(node.outputs) or '-'}" for node in graph.tasks]
    emit(args, {"tasks": [node.to_dict() for node in graph.tasks]}, "\n".join(lines) or "no tasks found")
    return EXIT_OK


def _show(args: argparse.Namespace) -> int:
    graph = _graph(args)
    lines = [
        f"{edge.consumer} <- {edge.producer or edge.target or '?'}  ({edge.reference}; {edge.kind}, {edge.provenance})"
        for edge in graph.edges
    ]
    emit(args, graph.to_dict(), "\n".join(lines) or "no edges found")
    return EXIT_OK


def _validate(args: argparse.Namespace) -> int:
    graph = _graph(args)
    payload: dict[str, object] = {
        "diagnostics": [item.to_dict() for item in graph.diagnostics],
        "edges": len(graph.edges),
        "ok": graph.ok,
        "tasks": len(graph.tasks),
    }
    summary = f"{len(graph.tasks)} tasks, {len(graph.edges)} edges, {len(graph.errors)} errors"
    emit(args, payload, "\n".join([*(item.text() for item in graph.diagnostics), summary]))
    return EXIT_OK if graph.ok else EXIT_VALIDATION


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``tasks list``, ``tasks graph`` and ``tasks validate``; none makes a remote call."""
    group = subparsers.add_parser("tasks", help="discover tasks and inspect their graph", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)
    for name, handler, text in (
        ("list", _list, "list discovered tasks"),
        ("graph", _show, "print the dependency graph with provenance"),
        ("validate", _validate, "validate the graph; exit 4 when it has errors"),
    ):
        command = commands.add_parser(name, help=text, parents=[global_parent()])
        command.add_argument("--project", default=".", help="project root holding bricksmith.toml (default: .)")
        command.set_defaults(handler=handler)
