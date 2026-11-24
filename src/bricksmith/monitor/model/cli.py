"""The ``model-monitoring`` command group: dashboard ``views``, ``alerts``, ``act`` and the ``report`` renderer."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, UsageError
from bricksmith.monitor.cli import _json  # pyright: ignore[reportPrivateUsage]
from bricksmith.monitor.limits import RED
from bricksmith.monitor.model.actions import ACTIONS as ACTION_NAMES
from bricksmith.monitor.model.actions import STATE_ACCEPTED, action_row, insert_statement
from bricksmith.monitor.model.alerts import replay_alerts
from bricksmith.monitor.model.config import parse_config
from bricksmith.monitor.model.engine import LIGHT, open_counts
from bricksmith.monitor.model.report import build_report, render_html, render_markdown
from bricksmith.monitor.model.tables import ACTIONS, RESULTS, RUNS, table_name, view_statements

if TYPE_CHECKING:
    import argparse


def _plain(value: object) -> object:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _views(args: argparse.Namespace) -> int:
    declared = [n.strip() for n in (args.declared or "").split(",") if n.strip()]
    statements = view_statements(args.scope, args.prefix, declared)
    emit(args, {"statements": statements}, ";\n\n".join(statements) + ";")
    return EXIT_OK


def _alerts(args: argparse.Namespace) -> int:
    state = _json(args.state)
    if not isinstance(state, dict) or not isinstance(state.get(RESULTS), list) or not isinstance(state.get(RUNS), list):
        msg = f"{args.state} must hold a mapping with 'results' and 'runs' row lists"
        raise UsageError(msg)
    config = parse_config(_json(args.config)) if args.config else None
    created = datetime.now(UTC).isoformat(timespec="seconds")
    if args.as_of:
        try:
            date.fromisoformat(args.as_of)
        except ValueError:
            msg = f"--as-of must be an ISO date (YYYY-MM-DD), got {args.as_of!r}"
            raise UsageError(msg) from None
    actions = state.get(ACTIONS, [])
    if not isinstance(actions, list):
        msg = f"{args.state}: 'actions' must be a list of rows"
        raise UsageError(msg)
    rows, alerts = replay_alerts(
        state[RESULTS], state[RUNS], config=config, created_at=created, actions=actions, as_of=args.as_of or ""
    )
    payload = {"results": len(rows), "alerts": [{c: _plain(v) for c, v in a.items()} for a in alerts]}
    lines = [f"model monitoring alerts: {len(alerts)} from {len(rows)} trusted result(s)"]
    lines += [
        f"  {a['severity']}: {a['message']}" + ("" if a["state"] == "open" else f" ({a['state']})") for a in alerts
    ]
    emit(args, payload, "\n".join(lines))
    red = args.fail_on_red and any(
        a["kind"] == LIGHT and a["light"] == RED and a["state"] != STATE_ACCEPTED for a in alerts
    )
    critical = args.fail_on_critical and open_counts(alerts)[1] > 0
    return EXIT_VALIDATION if red or critical else EXIT_OK


def _act(args: argparse.Namespace) -> int:
    created = args.created_at or datetime.now(UTC).isoformat(timespec="seconds")
    row = action_row(
        model=args.model, alert_id=args.alert_id, action=args.action, reason=args.reason, author=args.author,
        created_at=created, valid_until=args.valid_until or "",
    )  # fmt: skip
    payload: dict[str, object] = {"action": row}
    lines = [f"alert action {row['action_id']}: {row['action']} on {row['alert_id']} by {row['author']}"]
    if args.scope:
        statement = insert_statement(table_name(args.scope, args.prefix, ACTIONS), row)
        payload["statement"] = statement
        lines.append(statement + ";")
    if args.state:
        state = _json(args.state)
        if not isinstance(state, dict):
            msg = f"{args.state} must hold a mapping"
            raise UsageError(msg)
        held = [a for a in state.get(ACTIONS, []) if a.get("action_id") != row["action_id"]]
        Path(args.state).write_text(json.dumps({**state, ACTIONS: [*held, row]}, indent=2) + "\n", encoding="utf-8")
        lines.append(f"appended to {args.state}")
    emit(args, payload, "\n".join(lines))
    return EXIT_OK


def _table_rows(args: argparse.Namespace, spark: Any, kind: str) -> list[dict[str, Any]]:  # noqa: ANN401 - SparkSession
    from pyspark.sql import functions as F  # noqa: N812, PLC0415 - heavy integration, loaded where used

    frame = spark.table(table_name(args.scope, args.prefix, kind))
    if "model" in frame.columns:
        frame = frame.where(F.col("model") == args.model)
    return [{k: _plain(v) for k, v in row.asDict().items()} for row in frame.collect()]


def _state(args: argparse.Namespace) -> dict[str, Any]:
    """Return the stored rows the report reads: from an exported state file, or from the tables through Spark."""
    if bool(args.state) == bool(args.scope):
        msg = "pass exactly one of --state (exported JSON) or --scope (catalog.schema of the tables)"
        raise UsageError(msg)
    if args.state:
        state = _json(args.state)
        if (
            not isinstance(state, dict)
            or not isinstance(state.get(RESULTS), list)
            or not isinstance(state.get(RUNS), list)
        ):
            msg = f"{args.state} must hold a mapping with 'results' and 'runs' row lists"
            raise UsageError(msg)
        return state
    from bricksmith.clients import Clients  # noqa: PLC0415 - heavy integration, loaded where used

    spark = Clients(context_from_args(args)).spark()
    return {kind: _table_rows(args, spark, kind) for kind in (RESULTS, RUNS, ACTIONS)}


def _report(args: argparse.Namespace) -> int:
    if args.as_of:
        try:
            date.fromisoformat(args.as_of)
        except ValueError:
            msg = f"--as-of must be an ISO date (YYYY-MM-DD), got {args.as_of!r}"
            raise UsageError(msg) from None
    report = build_report(_state(args), args.model, as_of=args.as_of or "")
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", args.model)
    renderers = {"html": render_html, "md": render_markdown}
    chosen = renderers if args.format == "both" else {args.format: renderers[args.format]}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for extension, render in chosen.items():
        target = out / f"{stem}.{extension}"
        target.write_text(render(report), encoding="utf-8")
        written.append(str(target))
    series = sum(len(section.series) for section in report.sections)
    emit(
        args,
        {"model": args.model, "periods": report.periods, "series": series, "files": written},
        f"model monitoring report for {args.model}: {len(report.periods)} period(s), {series} series\n"
        + "\n".join(f"  wrote {path}" for path in written),
    )
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``model-monitoring views|alerts|act|report``."""
    group = subparsers.add_parser(
        "model-monitoring", help="model monitoring dashboard views and alert replay", parents=[global_parent()]
    )
    commands = group.add_subparsers(dest="command", required=True)
    views = commands.add_parser("views", help="print CREATE VIEW statements for dashboards", parents=[global_parent()])
    views.add_argument("--scope", required=True, help="catalog.schema holding the model monitoring tables")
    views.add_argument("--prefix", default="model_monitoring_", help="table name prefix (default model_monitoring_)")
    views.add_argument("--declared", help="comma-separated model names counted as declared for coverage, with the runs")
    views.set_defaults(handler=_views)
    alerts = commands.add_parser(
        "alerts", help="rebuild alerts from stored result lights, without recomputing", parents=[global_parent()]
    )
    alerts.add_argument("--state", required=True, help="JSON file with the stored 'results' and 'runs' rows")
    alerts.add_argument("--config", help="JSON declaration whose current limits recolor the stored values")
    alerts.add_argument("--fail-on-red", action="store_true", help="exit 4 when a red alert is replayed")
    alerts.add_argument(
        "--fail-on-critical",
        action="store_true",
        help="exit 4 when an open critical persistence or trend alert remains",
    )
    alerts.add_argument("--as-of", help="evaluation date (YYYY-MM-DD) for risk acceptances; default today")
    alerts.set_defaults(handler=_alerts)
    act = commands.add_parser(
        "act", help="record a human action on an alert: analysis, closed or risk_accepted", parents=[global_parent()]
    )
    act.add_argument("--model", required=True, help="the model the alert belongs to")
    act.add_argument("--alert-id", required=True, help="the alert id, as listed by alerts or the open_alerts view")
    act.add_argument("--action", required=True, choices=ACTION_NAMES, help="what was decided")
    act.add_argument("--reason", required=True, help="why")
    act.add_argument("--author", required=True, help="who decided")
    act.add_argument("--valid-until", help="risk_accepted only: last day (YYYY-MM-DD) the acceptance holds")
    act.add_argument("--created-at", help="timestamp of the decision (default now)")
    act.add_argument("--scope", help="catalog.schema of the monitoring tables: also print the INSERT statement")
    act.add_argument("--prefix", default="model_monitoring_", help="table name prefix (default model_monitoring_)")
    act.add_argument("--state", help="JSON state file whose 'actions' list the row is appended to")
    act.set_defaults(handler=_act)
    report = commands.add_parser(
        "report", help="render a model's monitoring report as HTML and Markdown", parents=[global_parent()]
    )
    report.add_argument("--model", required=True, help="the model to report on")
    report.add_argument("--state", help="JSON file with the stored 'results' and 'runs' (and 'actions') rows")
    report.add_argument("--scope", help="catalog.schema of the monitoring tables, read through the selected context")
    report.add_argument("--prefix", default="model_monitoring_", help="table name prefix (default model_monitoring_)")
    report.add_argument("--out", default=".", help="directory the report files are written to (default .)")
    report.add_argument("--format", choices=("html", "md", "both"), default="both", help="default both")
    report.add_argument("--as-of", help="evaluation date (YYYY-MM-DD) for risk acceptances; default today")
    report.set_defaults(handler=_report)


__all__ = ["register"]
