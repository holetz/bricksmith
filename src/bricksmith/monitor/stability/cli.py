"""The ``stability`` command group: ``check`` a table outside a project and print the dashboard ``views``."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.cli.common import emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, UsageError
from bricksmith.monitor.cli import _data, _json, _source  # pyright: ignore[reportPrivateUsage]
from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.engine import MemoryStore, check
from bricksmith.monitor.stability.tables import KINDS, merge_rows, view_statements

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
        tables = {k: list(v) for k, v in loaded.items() if k in KINDS}
    dataset = args.dataset or config.dataset or args.table or Path(args.data).stem
    result = check(_data(args), config, MemoryStore(tables), dataset=dataset, run_id=args.run_id)
    if state is not None:
        merged = {k: merge_rows(k, tables.get(k, []), result.tables[k]) for k in KINDS}
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


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``stability check|views``."""
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
    run.set_defaults(handler=_check)
    views = commands.add_parser("views", help="print CREATE VIEW statements for dashboards", parents=[global_parent()])
    views.add_argument("--scope", required=True, help="catalog.schema holding the stability tables")
    views.add_argument("--prefix", default="stability_", help="table name prefix (default stability_)")
    views.set_defaults(handler=_views)


__all__ = ["register"]
