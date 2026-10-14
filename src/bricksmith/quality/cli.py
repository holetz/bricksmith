"""The ``quality`` command group: ``compare`` two tables and run ``check`` declarations, with a gate exit code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.quality.checks import evaluate_checks, load_checks
from bricksmith.quality.compare import compare, plan_for_frames
from bricksmith.quality.errors import QualityError
from bricksmith.quality.plan import Tolerance
from bricksmith.quality.regression import GatePolicy, RegressionReport, build_regression_report

if TYPE_CHECKING:
    import argparse


def _table(args: argparse.Namespace, name: str) -> Any:  # noqa: ANN401
    """Open a table as a Spark DataFrame in the explicit context (replaced by fakes in tests)."""
    from bricksmith.clients import Clients  # noqa: PLC0415

    return Clients(context_from_args(args)).spark().table(name)


def parse_tolerance(text: str) -> tuple[str, Tolerance]:
    """Parse ``column=abs:0.5,rel:0.01`` (either part optional) into a column and a tolerance."""
    column, _, spec = text.partition("=")
    values = {"abs": 0.0, "rel": 0.0}
    for part in filter(None, spec.split(",")):
        key, _, number = part.partition(":")
        if key not in values:
            msg = f"bad tolerance {text!r}: use column=abs:N,rel:N"
            raise QualityError(msg)
        try:
            values[key] = float(number)
        except ValueError as error:
            msg = f"bad tolerance {text!r}: {number!r} is not a number"
            raise QualityError(msg) from error
    if not column or not spec:
        msg = f"bad tolerance {text!r}: use column=abs:N,rel:N"
        raise QualityError(msg)
    return column, Tolerance(values["abs"], values["rel"])


def _policy(path: str | None) -> GatePolicy:
    if not path:
        return GatePolicy()
    return GatePolicy.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _finish(args: argparse.Namespace, report: RegressionReport) -> int:
    if args.report_out:
        Path(args.report_out).write_text(report.to_json() + "\n", encoding="utf-8")
    emit(args, report.to_dict(), report.to_text())
    return report.exit_code


def _compare(args: argparse.Namespace) -> int:
    left, right = _table(args, args.left), _table(args, args.right)
    tolerances = dict(parse_tolerance(t) for t in args.tolerance)
    default = Tolerance(args.default_abs, args.default_rel)
    plan = plan_for_frames(
        left,
        right,
        args.key,
        columns=args.column or None,
        exclude=args.exclude,
        tolerances=tolerances,
        default_tolerance=default,
        left_name=args.left,
        right_name=args.right,
    )
    result = compare(left, right, plan, sample_limit=args.sample_limit)
    report = build_regression_report(
        args.name or f"{args.left} vs {args.right}", comparisons={"comparison": result}, policy=_policy(args.policy)
    )
    return _finish(args, report)


def _check(args: argparse.Namespace) -> int:
    path = Path(args.checks)
    checks = load_checks(path.read_text(encoding="utf-8"), fmt="toml" if path.suffix == ".toml" else "json")
    data = _table(args, args.table)
    other = _table(args, args.other) if args.other else None
    results = evaluate_checks(checks, data, other, sample_limit=args.sample_limit)
    policy = GatePolicy(allow_unavailable=args.allow_unavailable)
    return _finish(args, build_regression_report(args.name or args.table, checks=results, policy=policy))


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``quality compare`` and ``quality check``."""
    group = subparsers.add_parser(
        "quality", help="data comparison, checks and regression gates", parents=[global_parent()]
    )
    commands = group.add_subparsers(dest="command", required=True)
    for name, handler, text in (
        ("compare", _compare, "compare two tables"),
        ("check", _check, "run declared checks on a table"),
    ):
        cmd = commands.add_parser(name, help=text, parents=[global_parent()])
        cmd.add_argument("--name", help="report name (default: derived from the tables)")
        cmd.add_argument("--sample-limit", type=int, help="return at most N discrepancy samples (default: none)")
        cmd.add_argument("--report-out", help="also write the stable JSON report to this file")
        cmd.set_defaults(handler=handler)
        if name == "compare":
            cmd.add_argument("--left", required=True, help="reference table (catalog.schema.table)")
            cmd.add_argument("--right", required=True, help="candidate table")
            cmd.add_argument("--key", action="append", required=True, help="key column (repeatable)")
            cmd.add_argument("--column", action="append", help="compare only this column (repeatable)")
            cmd.add_argument("--exclude", action="append", default=[], help="skip this column (repeatable)")
            cmd.add_argument("--tolerance", action="append", default=[], help="column=abs:N,rel:N (repeatable)")
            cmd.add_argument("--default-abs", type=float, default=0.0, help="absolute tolerance for numeric columns")
            cmd.add_argument("--default-rel", type=float, default=0.0, help="relative tolerance for numeric columns")
            cmd.add_argument("--policy", help="JSON file with gate thresholds")
        else:
            cmd.add_argument("--table", required=True, help="table to check")
            cmd.add_argument("--other", help="second table, for cardinality checks")
            cmd.add_argument("--checks", required=True, help="JSON or TOML file declaring the checks")
            cmd.add_argument("--allow-unavailable", action="store_true", help="do not fail on unavailable checks")
