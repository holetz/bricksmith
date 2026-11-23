"""The ``model-monitoring`` command group: print the dashboard ``views`` and replay ``alerts`` from stored rows."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from bricksmith.cli.common import emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, UsageError
from bricksmith.monitor.cli import _json  # pyright: ignore[reportPrivateUsage]
from bricksmith.monitor.limits import RED
from bricksmith.monitor.model.alerts import replay_alerts
from bricksmith.monitor.model.config import parse_config
from bricksmith.monitor.model.tables import RESULTS, RUNS, view_statements

if TYPE_CHECKING:
    import argparse


def _plain(value: object) -> object:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _views(args: argparse.Namespace) -> int:
    statements = view_statements(args.scope, args.prefix)
    emit(args, {"statements": statements}, ";\n\n".join(statements) + ";")
    return EXIT_OK


def _alerts(args: argparse.Namespace) -> int:
    state = _json(args.state)
    if not isinstance(state, dict) or not isinstance(state.get(RESULTS), list) or not isinstance(state.get(RUNS), list):
        msg = f"{args.state} must hold a mapping with 'results' and 'runs' row lists"
        raise UsageError(msg)
    config = parse_config(_json(args.config)) if args.config else None
    created = datetime.now(UTC).isoformat(timespec="seconds")
    rows, alerts = replay_alerts(state[RESULTS], state[RUNS], config=config, created_at=created)
    payload = {"results": len(rows), "alerts": [{c: _plain(v) for c, v in a.items()} for a in alerts]}
    lines = [f"model monitoring alerts: {len(alerts)} from {len(rows)} trusted result(s)"]
    lines += [f"  {a['severity']}: {a['message']}" for a in alerts]
    emit(args, payload, "\n".join(lines))
    return EXIT_VALIDATION if args.fail_on_red and any(a["light"] == RED for a in alerts) else EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``model-monitoring views|alerts``."""
    group = subparsers.add_parser(
        "model-monitoring", help="model monitoring dashboard views and alert replay", parents=[global_parent()]
    )
    commands = group.add_subparsers(dest="command", required=True)
    views = commands.add_parser("views", help="print CREATE VIEW statements for dashboards", parents=[global_parent()])
    views.add_argument("--scope", required=True, help="catalog.schema holding the model monitoring tables")
    views.add_argument("--prefix", default="model_monitoring_", help="table name prefix (default model_monitoring_)")
    views.set_defaults(handler=_views)
    alerts = commands.add_parser(
        "alerts", help="rebuild alerts from stored result lights, without recomputing", parents=[global_parent()]
    )
    alerts.add_argument("--state", required=True, help="JSON file with the stored 'results' and 'runs' rows")
    alerts.add_argument("--config", help="JSON declaration whose current limits recolor the stored values")
    alerts.add_argument("--fail-on-red", action="store_true", help="exit 4 when a red alert is replayed")
    alerts.set_defaults(handler=_alerts)


__all__ = ["register"]
