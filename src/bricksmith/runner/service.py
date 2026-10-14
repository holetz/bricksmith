"""Shared command-line plumbing for ``bricksmith run`` and the deployed entrypoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from bricksmith.cli.common import context_from_args, emit
from bricksmith.errors import EXIT_OK, EXIT_RUN_FAILED, UsageError
from bricksmith.plan import Authorization, Plan, build_plan
from bricksmith.runner.execute import RetryPolicy, apply_plan
from bricksmith.runner.io import default_table_io

if TYPE_CHECKING:
    import argparse

    from bricksmith.graph import TaskGraph
    from bricksmith.runner.io import ExternalProvider, TableIO
    from bricksmith.runner.report import RunReport


def add_selection_options(parser: argparse.ArgumentParser) -> None:
    """Add the options that select tasks for a plan."""
    parser.add_argument("--project", default=".", help="project root holding bricksmith.toml (default: .)")
    parser.add_argument("--task", action="append", default=[], help="root task id or pattern (default: all)")
    parser.add_argument("--include", action="append", default=[], help="keep only tasks matching this pattern")
    parser.add_argument("--exclude", action="append", default=[], help="drop tasks matching this pattern")
    parser.add_argument("--no-upstream", action="store_true", help="do not add the upstream closure")
    parser.add_argument("--downstream", action="store_true", help="add the downstream closure")
    parser.add_argument("--include-static", action="store_true", help="recompute static tasks in the closure")
    parser.add_argument("--partial", action="store_true", help="accept an incomplete closure (warnings, not errors)")


def add_run_options(parser: argparse.ArgumentParser) -> None:
    """Add the options that control an application of a plan."""
    parser.add_argument("--dry-run", action="store_true", help="validate and list effects; execute nothing")
    parser.add_argument("--authorize", action="append", default=[], metavar="OBJECT:OP[,OP]", help="grant operations")
    parser.add_argument("--concurrency", type=int, help="maximum parallel tasks (default: the context's)")
    parser.add_argument("--retries", type=int, default=0, help="extra attempts per failing task (default: 0)")
    parser.add_argument("--retry-delay", type=float, default=0.0, help="seconds before the first retry")
    parser.add_argument("--fail-fast", action="store_true", help="start no new task after a failure")
    parser.add_argument("--report", help="write the run report JSON to this file")


def plan_from_args(args: argparse.Namespace) -> tuple[Plan, TaskGraph]:
    """Load the graph with the explicit context and build a plan from the selection options."""
    from bricksmith.graph import load_graph  # noqa: PLC0415 - keeps parser construction light

    graph = load_graph(args.project, context=context_from_args(args))
    plan = build_plan(
        graph,
        roots=args.task,
        include=args.include,
        exclude=args.exclude,
        upstream=not args.no_upstream,
        downstream=args.downstream,
        include_static=args.include_static,
        partial=args.partial,
    )
    return plan, graph


def read_plan(path: str) -> Plan:
    """Read a plan file."""
    return Plan.from_json(Path(path).read_text(encoding="utf-8"))


def grants_from(values: list[str]) -> list[Authorization]:
    """Parse ``OBJECT:OP[,OP]`` options."""
    grants = []
    for value in values:
        physical, _, operations = value.rpartition(":")
        if not physical or not operations:
            msg = f"--authorize expects OBJECT:OPERATION[,OPERATION], got {value!r}"
            raise UsageError(msg)
        grants.append(Authorization(physical, tuple(sorted(operations.split(",")))))
    return grants


def run_summary(report: RunReport) -> str:
    """Return one line per task plus the counts."""
    lines = [f"{r.state:10} {r.task_id}  {r.reason}".rstrip() for r in report.results]
    counts = report.to_dict()["counts"]
    assert isinstance(counts, dict)  # noqa: S101 - narrowing for the type checker
    lines.append(", ".join(f"{k}={v}" for k, v in counts.items() if v))
    return "\n".join(lines)


def apply_from_args(
    args: argparse.Namespace,
    plan: Plan,
    graph: TaskGraph,
    table_io: TableIO | None = None,
    externals: ExternalProvider | None = None,
) -> int:
    """Authorize, validate and run ``plan``; print and optionally save the report; return the exit code."""
    context = context_from_args(args)
    grants = grants_from(args.authorize)
    reviewed = plan.authorize(*grants) if grants else plan
    report = apply_plan(
        reviewed,
        graph,
        project_root=args.project,
        execution=context,
        table_io=table_io or default_table_io(context),
        concurrency=args.concurrency,
        retry=RetryPolicy(args.retries + 1, args.retry_delay),
        dry_run=args.dry_run,
        fail_fast=args.fail_fast,
        externals=externals,
    )
    if args.report:
        Path(args.report).write_text(report.to_json(), encoding="utf-8")
    emit(args, report.to_dict(), run_summary(report))
    return EXIT_OK if report.ok else EXIT_RUN_FAILED


def write_or_emit_plan(args: argparse.Namespace, plan: Plan, out: str | None) -> None:
    """Save the plan to ``out`` when given, and print it (JSON with ``--json``)."""
    if out:
        Path(out).write_text(plan.to_json(), encoding="utf-8")
    lines = [f"{e.action:5} {e.task_id}  [{e.selected_by}] {e.reason}" for e in plan.entries]
    lines.extend(
        f"      -> {t.physical} ({', '.join(t.operations)}){' protected' if t.protected else ''}"
        for e in plan.to_run
        for t in e.targets
    )
    lines.extend(f"{i.severity}[{i.code}]: {i.message}" for i in plan.issues)
    lines.append(f"fingerprint {plan.fingerprint}")
    if args.json:
        sys.stdout.write(json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n")
    else:
        emit(args, plan.to_dict(), "\n".join(lines))
