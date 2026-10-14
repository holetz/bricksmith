"""The ``run`` command group: ``plan``, ``apply`` and ``replay``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bricksmith.cli.common import context_from_args, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION
from bricksmith.runner.service import (
    add_run_options,
    add_selection_options,
    apply_from_args,
    plan_from_args,
    read_plan,
    write_or_emit_plan,
)

if TYPE_CHECKING:
    import argparse


def _plan(args: argparse.Namespace) -> int:
    plan, _ = plan_from_args(args)
    write_or_emit_plan(args, plan, args.out)
    return EXIT_VALIDATION if plan.errors else EXIT_OK


def _apply(args: argparse.Namespace) -> int:
    from bricksmith.graph import load_graph  # noqa: PLC0415 - keeps parser construction light

    if args.plan:
        graph = load_graph(args.project, context=context_from_args(args))
        plan = read_plan(args.plan)
    else:
        plan, graph = plan_from_args(args)
    return apply_from_args(args, plan, graph)


def _replay(args: argparse.Namespace) -> int:
    from bricksmith.graph import load_graph  # noqa: PLC0415 - keeps parser construction light
    from bricksmith.runner.replay import replay_plan  # noqa: PLC0415
    from bricksmith.runner.report import RunReport  # noqa: PLC0415

    graph = load_graph(args.project, context=context_from_args(args))
    with open(args.report_in, encoding="utf-8") as handle:  # noqa: PTH123
        report = RunReport.from_json(handle.read())
    plan = replay_plan(read_plan(args.plan), report, graph)
    if not args.apply:
        write_or_emit_plan(args, plan, args.out)
        return EXIT_VALIDATION if plan.errors else EXIT_OK
    if args.out:
        write_or_emit_plan(args, plan, args.out)
    return apply_from_args(args, plan, graph)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``run plan``, ``run apply`` and ``run replay``."""
    group = subparsers.add_parser("run", help="plan, apply and replay task runs", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)

    plan = commands.add_parser(
        "plan", help="select tasks and print the reviewed plan; executes nothing", parents=[global_parent()]
    )
    add_selection_options(plan)
    plan.add_argument("--out", help="also write the plan JSON to this file")
    plan.set_defaults(handler=_plan)

    apply = commands.add_parser("apply", help="validate and run a plan (or a selection)", parents=[global_parent()])
    add_selection_options(apply)
    add_run_options(apply)
    apply.add_argument("--plan", help="reviewed plan file; without it the selection options build one")
    apply.set_defaults(handler=_apply)

    replay = commands.add_parser("replay", help="plan the work a run left undone", parents=[global_parent()])
    replay.add_argument("--project", default=".", help="project root holding bricksmith.toml (default: .)")
    replay.add_argument("--plan", required=True, help="the plan the report belongs to")
    replay.add_argument("--report-in", required=True, help="run report JSON from the previous run")
    replay.add_argument("--out", help="write the replay plan JSON to this file")
    replay.add_argument("--apply", action="store_true", help="run the replay plan instead of only printing it")
    add_run_options(replay)
    replay.set_defaults(handler=_replay)
