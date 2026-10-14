"""The ``schema`` command group: ``diff``, ``plan``, ``apply`` and ``maintain``."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION
from bricksmith.evolution.declaration import load_declaration, load_live_snapshot
from bricksmith.evolution.diff import diff_schemas
from bricksmith.evolution.maintenance import OPERATIONS, plan_maintenance, run_maintenance
from bricksmith.evolution.planner import EvolutionPolicy, plan_migration

if TYPE_CHECKING:
    import argparse

    from bricksmith.evolution.model import TableSchema


def _spark(args: argparse.Namespace) -> object:
    from bricksmith.clients import Clients  # noqa: PLC0415 - keeps parser construction light

    return Clients(context_from_args(args)).spark()


def _policy(args: argparse.Namespace, base: EvolutionPolicy) -> EvolutionPolicy:
    return dataclasses.replace(
        base,
        narrowing="checked" if args.narrowing else base.narrowing,
        incompatible="checked" if args.incompatible else base.incompatible,
        destructive=base.destructive or args.allow_destructive,
    )


def _live(args: argparse.Namespace) -> TableSchema:
    if args.live:
        return load_live_snapshot(args.live)
    from bricksmith.evolution.apply import read_live  # noqa: PLC0415 - Spark only when reading a table

    return read_live(_spark(args), args.table)


def _diff(args: argparse.Namespace) -> int:
    declaration = load_declaration(args.declared)
    policy = _policy(args, declaration.policy)
    changes = diff_schemas(declaration.schema, _live(args), policy.renames)
    text = "\n".join(f"{c.kind} {c.path}: {c.before} -> {c.after}" for c in changes) or "no differences"
    emit(args, {"changes": [c.to_dict() for c in changes], "table": args.table}, text)
    return EXIT_VALIDATION if changes and args.check else EXIT_OK


def _plan(args: argparse.Namespace) -> int:
    declaration = load_declaration(args.declared)
    plan = plan_migration(args.table, declaration.schema, _live(args), _policy(args, declaration.policy))
    emit(args, plan.to_dict(), plan.to_text())
    return EXIT_OK if plan.ok else EXIT_VALIDATION


def _apply(args: argparse.Namespace) -> int:
    from bricksmith.evolution.apply import migrate  # noqa: PLC0415 - Spark only when applying

    declaration = load_declaration(args.declared)
    result = migrate(_spark(args), args.table, declaration.schema, _policy(args, declaration.policy))
    text = result.plan.to_text() + ("\napplied" if result.applied else "")
    emit(args, result.to_dict(), text)
    return EXIT_OK


def _maintain(args: argparse.Namespace) -> int:
    declaration = load_declaration(args.declared)
    plan = plan_maintenance(args.table, declaration.maintenance, tuple(args.only) if args.only else None)
    if args.run:
        run_maintenance(_spark(args), plan)  # type: ignore[arg-type]
    emit(
        args,
        {**plan.to_dict(), "ran": args.run},
        plan.to_text() + ("\nran" if args.run else "\n(plan only; add --run)"),
    )
    return EXIT_OK


def _common(parser: argparse.ArgumentParser, *, policy: bool) -> None:
    parser.add_argument("table", help="three-part table name")
    parser.add_argument("--declared", required=True, help="declaration file (.toml or .json)")
    if policy:
        parser.add_argument("--narrowing", action="store_true", help="allow narrowing after a lossless check")
        parser.add_argument("--incompatible", action="store_true", help="allow incompatible families after a check")
        parser.add_argument("--allow-destructive", action="store_true", help="allow dropping columns")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``schema`` group."""
    group = subparsers.add_parser("schema", help="schema evolution and maintenance", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)
    diff = commands.add_parser("diff", help="compare declared and live state", parents=[global_parent()])
    _common(diff, policy=True)
    diff.add_argument("--live", help="read the live state from a snapshot file instead of the workspace")
    diff.add_argument("--check", action="store_true", help="exit 4 when there are differences")
    diff.set_defaults(handler=_diff)
    plan = commands.add_parser("plan", help="plan the migration (no effects)", parents=[global_parent()])
    _common(plan, policy=True)
    plan.add_argument("--live", help="read the live state from a snapshot file instead of the workspace")
    plan.set_defaults(handler=_plan)
    apply = commands.add_parser("apply", help="plan, apply and verify the migration", parents=[global_parent()])
    _common(apply, policy=True)
    apply.set_defaults(handler=_apply)
    maintain = commands.add_parser("maintain", help="plan (and with --run, run) maintenance", parents=[global_parent()])
    _common(maintain, policy=False)
    maintain.add_argument("--only", action="append", choices=OPERATIONS, help="restrict to one operation (repeatable)")
    maintain.add_argument("--run", action="store_true", help="run the maintenance; without it only the plan is shown")
    maintain.set_defaults(handler=_maintain)
