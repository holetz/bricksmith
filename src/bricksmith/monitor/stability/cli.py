"""The ``stability`` command group: ``check`` a table outside a project and print the dashboard ``views``."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.cli.common import emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, UsageError
from bricksmith.monitor.actions import ACTIONS as ACTION_NAMES
from bricksmith.monitor.actions import RESOLUTIONS, action_row, insert_statement
from bricksmith.monitor.cli import _data, _json, _source  # pyright: ignore[reportPrivateUsage]
from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.engine import MemoryStore, check
from bricksmith.monitor.stability.tables import ACTIONS, ALL_KINDS, SCHEMAS, merge_rows, table_name, view_statements

if TYPE_CHECKING:
    import argparse


def _plain(value: object) -> object:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _check(args: argparse.Namespace) -> int:
    config = parse_config(_json(args.config))
    state = Path(args.state) if args.state else None
    tables: dict[str, list[dict[str, Any]]] = {}
    if state is not None and state.is_file():
        loaded = _json(str(state))
        if not isinstance(loaded, dict):
            msg = f"{state} must hold a mapping of stability table to rows"
            raise UsageError(msg)
        tables = {k: list(v) for k, v in loaded.items() if k in ALL_KINDS}
    dataset = args.dataset or config.dataset or args.table or Path(args.data).stem
    result = check(_data(args), config, MemoryStore(tables), dataset=dataset, run_id=args.run_id, as_of=args.as_of)
    if state is not None:
        # A kind the run did not return (revisions while revision is off) is kept as loaded.
        merged = {**tables, **{k: merge_rows(k, tables.get(k, []), rows) for k, rows in result.tables.items()}}
        plain = {k: [{c: _plain(v) for c, v in r.items()} for r in rows] for k, rows in merged.items()}
        state.write_text(json.dumps(plain, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    outcome = result.outcome
    payload = {
        "outcome": outcome.to_dict(),
        "results": [{c: _plain(v) for c, v in r.items()} for r in result.tables["results"]],
        "alerts": result.tables["alerts"],
        "skipped": result.skipped,
    }
    lines = [f"stability {outcome.status}: {outcome.message}"]
    lines += [f"  skipped {c}: {why}" for c, why in sorted(result.skipped.items())]
    lines += [f"  alert {a['state']} {a['severity']}: {a['message']}" for a in result.tables["alerts"]]
    emit(args, payload, "\n".join(lines))
    return EXIT_VALIDATION if outcome.failed else EXIT_OK


def _views(args: argparse.Namespace) -> int:
    statements = view_statements(args.scope, args.prefix)
    emit(args, {"statements": statements}, ";\n\n".join(statements) + ";")
    return EXIT_OK


def _action(args: argparse.Namespace) -> int:
    created = args.created_at or datetime.now(UTC).isoformat(timespec="seconds")
    row = action_row(
        entity="dataset", name=args.dataset, alert_id=args.alert_id, action=args.action, reason=args.reason,
        author=args.author, created_at=created, valid_until=args.valid_until or "",
        resolution=args.resolution or "", resolutions=RESOLUTIONS,
    )  # fmt: skip
    payload: dict[str, object] = {"action": row}
    lines = [f"alert action {row['action_id']}: {row['action']} on {row['alert_id']} by {row['author']}"]
    if args.scope:
        statement = insert_statement(table_name(args.scope, args.prefix, ACTIONS), row, tuple(SCHEMAS[ACTIONS]))
        payload["statement"] = statement
        lines.append(statement + ";")
    if args.state:
        state = _json(args.state)
        if not isinstance(state, dict):
            msg = f"{args.state} must hold a mapping of stability table to rows"
            raise UsageError(msg)
        held = [a for a in state.get(ACTIONS, []) if a.get("action_id") != row["action_id"]]
        Path(args.state).write_text(
            json.dumps({**state, ACTIONS: [*held, row]}, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        lines.append(f"appended to {args.state}")
    emit(args, payload, "\n".join(lines))
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``stability check|views|action``."""
    group = subparsers.add_parser(
        "stability", help="check table stability by period and print dashboard views", parents=[global_parent()]
    )
    commands = group.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "check", help="check a table against its stored history (exit 4 when the gate fails)", parents=[global_parent()]
    )
    run.add_argument("--config", required=True, help="JSON file with a stability declaration (period, columns...)")
    _source(run)
    run.add_argument("--dataset", help="dataset name (default: the declaration's, the table or the file name)")
    run.add_argument("--state", help="local JSON file holding the stability tables; read, then updated")
    run.add_argument("--run-id", default="cli", help="run identity recorded on the rows")
    run.add_argument("--as-of", help="ISO date the declared cadence is judged at (default: today, UTC)")
    run.set_defaults(handler=_check)
    views = commands.add_parser("views", help="print CREATE VIEW statements for dashboards", parents=[global_parent()])
    views.add_argument("--scope", required=True, help="catalog.schema holding the stability tables")
    views.add_argument("--prefix", default="stability_", help="table name prefix (default stability_)")
    views.set_defaults(handler=_views)
    act = commands.add_parser(
        "action",
        help="record a human action on an alert: analysis, closed (with a resolution) or risk_accepted",
        parents=[global_parent()],
    )
    act.add_argument("--dataset", required=True, help="the dataset the alert belongs to")
    act.add_argument("--alert-id", required=True, help="the alert id, as listed by check or the open_alerts view")
    act.add_argument("--action", required=True, choices=ACTION_NAMES, help="what was decided")
    act.add_argument("--resolution", choices=RESOLUTIONS, help="closed only: why the alert was closed")
    act.add_argument("--reason", required=True, help="why")
    act.add_argument("--author", required=True, help="who decided")
    act.add_argument("--valid-until", help="risk_accepted only: last day (YYYY-MM-DD) the acceptance holds")
    act.add_argument("--created-at", help="timestamp of the decision (default now)")
    act.add_argument("--scope", help="catalog.schema of the stability tables: also print the INSERT statement")
    act.add_argument("--prefix", default="stability_", help="table name prefix (default stability_)")
    act.add_argument("--state", help="local JSON file holding the stability tables; the row is appended to 'actions'")
    act.set_defaults(handler=_action)


__all__ = ["register"]
