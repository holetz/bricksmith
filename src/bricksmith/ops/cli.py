"""The ``ops`` command group: ``report``, ``diagnose``, ``readiness`` and ``explain``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, UsageError
from bricksmith.ops.budgets import explain_plan, load_ops_config
from bricksmith.ops.readiness import InputSpec, TableInspector, evaluate_readiness
from bricksmith.ops.report import build_ops_report, diagnose, render_ops_report
from bricksmith.runner.report import RunReport
from bricksmith.runner.service import add_selection_options, plan_from_args, read_plan

if TYPE_CHECKING:
    import argparse

    from bricksmith.plan import Plan


def default_inspector(args: argparse.Namespace) -> TableInspector:
    """Build the Spark/Delta inspector for the explicit context (replaced by fakes in tests)."""
    from bricksmith.clients import Clients  # noqa: PLC0415 - heavy integration, loaded where used
    from bricksmith.ops.inspect_spark import SparkDeltaInspector  # noqa: PLC0415

    return SparkDeltaInspector(Clients(context_from_args(args)).spark())


def _load(args: argparse.Namespace) -> tuple[RunReport, Plan | None]:
    report = RunReport.from_json(Path(args.report_in).read_text(encoding="utf-8"))
    return report, read_plan(args.plan) if args.plan else None


def _names(args: argparse.Namespace) -> dict[str, Any]:
    return {"plan_file": args.plan or "plan.json", "report_file": args.report_in}


def _report(args: argparse.Namespace) -> int:
    report, plan = _load(args)
    data = build_ops_report(report, plan, **_names(args))
    emit(args, data, render_ops_report(data))
    return EXIT_OK


def _diagnose(args: argparse.Namespace) -> int:
    report, plan = _load(args)
    try:
        result = diagnose(report, args.task, plan, **_names(args))
    except KeyError as error:
        raise UsageError(error.args[0]) from None
    emit(args, result.to_dict(), result.to_text())
    return EXIT_OK


def _readiness(args: argparse.Namespace) -> int:
    specs = list(load_ops_config(args.project).inputs)
    specs.extend(InputSpec(t, args.max_age, args.min_rows, tuple(args.require_column)) for t in args.table)
    if not specs:
        msg = "nothing to check: pass --table or declare [[ops.inputs]] in bricksmith.toml"
        raise UsageError(msg)
    result = evaluate_readiness(specs, default_inspector(args), datetime.now(UTC))
    emit(args, result.to_dict(), result.to_text())
    return EXIT_OK if result.ready else EXIT_VALIDATION


def _explain(args: argparse.Namespace) -> int:
    plan, _ = plan_from_args(args)
    config = load_ops_config(args.project)
    concurrency = args.concurrency or context_from_args(args).concurrency
    explanation = explain_plan(plan, config, concurrency=concurrency)
    emit(args, explanation.to_dict(), explanation.to_text())
    return EXIT_OK if explanation.ok and not plan.errors else EXIT_VALIDATION


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``ops report|diagnose|readiness|explain``."""
    group = subparsers.add_parser(
        "ops", help="run reports, diagnosis, readiness and budgets", parents=[global_parent()]
    )
    commands = group.add_subparsers(dest="command", required=True)

    def with_report(name: str, help_text: str) -> argparse.ArgumentParser:
        parser = commands.add_parser(name, help=help_text, parents=[global_parent()])
        parser.add_argument("--report-in", required=True, help="run report JSON written by `run apply --report`")
        parser.add_argument("--plan", help="the plan of the run (adds dependency context and replay hints)")
        return parser

    with_report("report", "render a run report with diagnoses, recovery and usage").set_defaults(handler=_report)
    diag = with_report("diagnose", "explain one failed, blocked, skipped or not-run task")
    diag.add_argument("--task", required=True, help="task id to diagnose")
    diag.set_defaults(handler=_diagnose)

    ready = commands.add_parser(
        "readiness", help="evaluate input freshness and completeness", parents=[global_parent()]
    )
    ready.add_argument("--project", default=".", help="project root holding bricksmith.toml (default: .)")
    ready.add_argument("--table", action="append", default=[], help="physical table to check (repeatable)")
    ready.add_argument("--max-age", type=float, help="maximum age in seconds for --table inputs")
    ready.add_argument("--min-rows", type=int, help="minimum row count for --table inputs")
    ready.add_argument("--require-column", action="append", default=[], help="column that must exist")
    ready.set_defaults(handler=_readiness)

    explain = commands.add_parser(
        "explain", help="dry-run: expensive operations and budget violations", parents=[global_parent()]
    )
    add_selection_options(explain)
    explain.add_argument("--concurrency", type=int, help="requested parallel tasks (default: the context's)")
    explain.set_defaults(handler=_explain)
