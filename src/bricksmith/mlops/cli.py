"""The ``dataset`` command group: offline ``validate`` and ``describe`` of model dataset declarations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bricksmith.cli.common import emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION
from bricksmith.mlops.project import ProjectScan, scan_project

if TYPE_CHECKING:
    import argparse


def _scan(args: argparse.Namespace) -> ProjectScan:
    scan = scan_project(args.project)
    if args.model:
        scan = ProjectScan(
            {k: v for k, v in scan.specs.items() if k == args.model},
            tuple(t for t in scan.tasks if t.model == args.model),
            scan.findings,
        )
    return scan


def _validate(args: argparse.Namespace) -> int:
    scan = _scan(args)
    lines = [f"{f.code}: {f.message}" for f in scan.findings]
    lines.append(f"{len(scan.specs)} models, {len(scan.tasks)} dataset tasks, {len(scan.findings)} findings")
    emit(args, scan.to_dict(), "\n".join(lines))
    return EXIT_OK if scan.ok else EXIT_VALIDATION


def _describe(args: argparse.Namespace) -> int:
    scan = _scan(args)
    lines: list[str] = []
    for model, info in scan.to_dict()["models"].items():  # type: ignore[attr-defined]
        lines.append(f"{model}  config={info['config'][:12]}  features={','.join(info['features'])}")
        lines += [f"  {role}: {t['task']} -> {','.join(t['outputs'])}" for role, t in sorted(info["tasks"].items())]
    emit(args, scan.to_dict(), "\n".join(lines) or "no model datasets declared")
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``dataset validate`` and ``dataset describe``; neither makes a remote call."""
    group = subparsers.add_parser("dataset", help="model dataset declarations (offline)", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)
    for name, handler, text in (
        ("validate", _validate, "check model identities, specs and graph-visible dependencies; exit 4 on findings"),
        ("describe", _describe, "list models with their feature contract and dataset tasks"),
    ):
        command = commands.add_parser(name, help=text, parents=[global_parent()])
        command.add_argument("--project", default=".", help="project root (default: .)")
        command.add_argument("--model", help="restrict to one explicit model id")
        command.set_defaults(handler=handler)
