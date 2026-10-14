"""The ``experiment`` command group: plan, run, compare, promote and retire; mutations need explicit flags."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_RUN_FAILED
from bricksmith.experiments.evidence import Evidence
from bricksmith.experiments.isolation import plan_isolation
from bricksmith.experiments.promotion import (
    apply_promotion,
    apply_retirement,
    plan_promotion,
    plan_retirement,
)
from bricksmith.experiments.revision import load_revision
from bricksmith.experiments.run import compare_revision, run_revision
from bricksmith.quality import GatePolicy, RegressionReport

if TYPE_CHECKING:
    import argparse

    from bricksmith.experiments.store import ExperimentStore


def _store(args: argparse.Namespace) -> ExperimentStore:
    """Build the Spark store for the explicit context (replaced by fakes in tests)."""
    from bricksmith.experiments.spark import SparkExperimentStore  # noqa: PLC0415

    return SparkExperimentStore.for_context(context_from_args(args))


def _table_io(args: argparse.Namespace) -> object:
    from bricksmith.runner.io import default_table_io  # noqa: PLC0415

    return default_table_io(context_from_args(args))


def _plan(args: argparse.Namespace) -> int:
    path = Path(args.revision)
    revision = load_revision(path.read_text(encoding="utf-8"), fmt="json" if path.suffix == ".json" else "toml")
    store = _store(args) if args.check_remote or args.freeze else None
    plan = plan_isolation(args.project, revision, context=context_from_args(args), store=store)
    if args.freeze and plan.ok:
        created = args.created or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        Evidence.freeze(plan, created=created).write(args.freeze)
    emit(args, plan.to_dict(), plan.to_text())
    return EXIT_OK if plan.ok else 4


def _run(args: argparse.Namespace) -> int:
    evidence = Evidence.read(args.evidence)
    outcome = run_revision(
        evidence,
        project_root=args.project,
        context=context_from_args(args),
        table_io=_table_io(args),  # type: ignore[arg-type]
        store=_store(args),
        dry_run=not args.apply,
    )
    if args.report:
        Path(args.report).write_text(outcome.report.to_json(), encoding="utf-8")
    lines = [f"{r.state:10} {r.task_id}  {r.reason}".rstrip() for r in outcome.report.results]
    emit(args, outcome.report.to_dict(), "\n".join(lines))
    return EXIT_OK if outcome.ok else EXIT_RUN_FAILED


def _compare(args: argparse.Namespace) -> int:
    evidence = Evidence.read(args.evidence)
    policy = GatePolicy.from_dict(json.loads(Path(args.policy).read_text(encoding="utf-8"))) if args.policy else None
    report = compare_revision(evidence, _store(args), policy=policy)
    if args.report_out:
        Path(args.report_out).write_text(report.to_json() + "\n", encoding="utf-8")
    emit(args, report.to_dict(), report.to_text())
    return report.exit_code


def _promote(args: argparse.Namespace) -> int:
    evidence = Evidence.read(args.evidence)
    report = RegressionReport.from_dict(json.loads(Path(args.report).read_text(encoding="utf-8")))
    store = _store(args)
    plan = plan_promotion(evidence, report, store, project_root=args.project, context=context_from_args(args))
    if args.apply:
        apply_promotion(plan, store, authorized=set(args.authorize))
    emit(args, plan.to_dict(), plan.to_text())
    return EXIT_OK


def _retire(args: argparse.Namespace) -> int:
    evidence = Evidence.read(args.evidence)
    store = _store(args)
    plan = plan_retirement(evidence, store)
    left: list[str] = []
    if args.apply:
        _, left = apply_retirement(plan, evidence, store)
    payload = {**plan.to_dict(), "applied": args.apply, "left_behind": left}
    emit(args, payload, plan.to_text() + "".join(f"\nleft      {item}" for item in left))
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``experiment plan|run|compare|promote|retire``."""
    group = subparsers.add_parser(
        "experiment", help="isolated experiments and frozen inputs", parents=[global_parent()]
    )
    commands = group.add_subparsers(dest="command", required=True)

    def add(name: str, handler: object, text: str) -> argparse.ArgumentParser:
        cmd = commands.add_parser(name, help=text, parents=[global_parent()])
        cmd.add_argument("--project", default=".", help="project root holding bricksmith.toml (default: .)")
        cmd.set_defaults(handler=handler)
        return cmd

    plan = add("plan", _plan, "plan the isolation of a revision file; --freeze writes immutable evidence")
    plan.add_argument("--revision", required=True, help="revision file (TOML or JSON)")
    plan.add_argument("--freeze", help="write the evidence file here (resolves pins; refuses to overwrite)")
    plan.add_argument("--created", help="timestamp recorded in the evidence (default: now, UTC)")
    plan.add_argument(
        "--check-remote", action="store_true", help="check existing objects and resolve pins without freezing"
    )
    run = add("run", _run, "run the frozen revision in its isolated destination (dry-run unless --apply)")
    run.add_argument("--evidence", required=True)
    run.add_argument("--apply", action="store_true", help="create the destination and run (otherwise a dry run)")
    run.add_argument("--report", help="write the run report JSON here")
    cmp_ = add("compare", _compare, "compare candidate outputs with the baseline and apply the gate")
    cmp_.add_argument("--evidence", required=True)
    cmp_.add_argument("--policy", help="JSON file with gate thresholds")
    cmp_.add_argument("--report-out", help="write the regression report JSON here")
    promote = add("promote", _promote, "plan promotion (applied only with --apply and --authorize per baseline object)")
    promote.add_argument("--evidence", required=True)
    promote.add_argument("--report", required=True, help="regression report from `experiment compare`")
    promote.add_argument("--apply", action="store_true")
    promote.add_argument(
        "--authorize", action="append", default=[], metavar="OBJECT", help="baseline object to replace"
    )
    retire = add("retire", _retire, "plan removal of experimental outputs (applied only with --apply)")
    retire.add_argument("--evidence", required=True)
    retire.add_argument("--apply", action="store_true", help="drop the experimental tables and empty schemas")
