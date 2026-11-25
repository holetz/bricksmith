"""The ``stability`` command group: ``check``, ``reprocess`` or ``backtest`` a table outside a project, ``views``."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, UsageError
from bricksmith.monitor.actions import ACTIONS as ACTION_NAMES
from bricksmith.monitor.actions import RESOLUTIONS, action_row, insert_statement
from bricksmith.monitor.cli import _data, _json, _source  # pyright: ignore[reportPrivateUsage]
from bricksmith.monitor.stability.backtest import backtest
from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.dashboard import dashboard_definition
from bricksmith.monitor.stability.engine import MemoryStore, check
from bricksmith.monitor.stability.marks import MARKS as MARK_NAMES
from bricksmith.monitor.stability.marks import UNMARKED, effective_marks, mark_row
from bricksmith.monitor.stability.ops import Operations
from bricksmith.monitor.stability.sql import SqlRunner
from bricksmith.monitor.stability.tables import (
    ACTIONS,
    ALL_KINDS,
    MARKS,
    SCHEMAS,
    merge_rows,
    table_name,
    view_statements,
)

if TYPE_CHECKING:
    import argparse


def _plain(value: object) -> object:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _ops(args: argparse.Namespace) -> Operations:
    """Build the workspace operations from the explicit profile, ``--warehouse`` and ``--scope``."""
    if not args.warehouse or not args.scope:
        msg = "workspace operations need --warehouse and --scope (catalog.schema of the stability tables)"
        raise UsageError(msg)
    from bricksmith.clients import Clients  # noqa: PLC0415 - heavy integration, loaded where used

    workspace = Clients(context_from_args(args)).workspace()
    return Operations(SqlRunner(workspace, args.warehouse), args.scope, prefix=args.prefix, jobs=workspace.jobs)


def _table(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    return "\n".join("  ".join(str(r.get(c) if r.get(c) is not None else "") for c in columns) for r in rows)


def _status(args: argparse.Namespace) -> int:
    rows = _ops(args).status()
    cols = ("dataset", "status", "latest_period", "open_alerts", "last_run_status", "last_run_at")
    emit(args, {"datasets": rows}, f"{len(rows)} dataset(s)\n" + _table(rows, cols))
    return EXIT_OK


def _series(args: argparse.Namespace) -> int:
    filters = {k: getattr(args, k) for k in ("column", "metric", "segment") if getattr(args, k) is not None}
    rows = _ops(args).series(args.dataset, **filters)
    cols = ("period", "column", "metric", "value", "light", "trend_status", "mark", "action")
    emit(args, {"series": rows}, f"{len(rows)} point(s) of {args.dataset}\n" + _table(rows, cols))
    return EXIT_OK


def _actions(args: argparse.Namespace) -> int:
    ops = _ops(args)
    if args.queue:
        rows = ops.alerts(args.dataset)
        cols = ("dataset", "alert_id", "severity", "effective_state", "action", "message")
        emit(args, {"alerts": rows}, f"{len(rows)} open alert(s)\n" + _table(rows, cols))
    else:
        rows = ops.actions(args.dataset)
        cols = ("created_at", "dataset", "alert_id", "action", "resolution", "author", "reason")
        emit(args, {"actions": rows}, f"{len(rows)} action(s)\n" + _table(rows, cols))
    return EXIT_OK


def _reprocess(args: argparse.Namespace) -> int:
    if not args.job:
        if not args.config:
            msg = "reprocess needs --config (offline) or --job with --warehouse and --scope (workspace)"
            raise UsageError(msg)
        return _check(args)
    started = _ops(args).reprocess(args.job, args.periods)
    emit(args, {"reprocess": started}, f"started run {started['run_id']} of job {started['job_id']}: {args.periods}")
    return EXIT_OK


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
    periods = [p.strip() for p in (getattr(args, "periods", None) or "").split(",") if p.strip()]
    result = check(
        _data(args),
        config,
        MemoryStore(tables),
        dataset=dataset,
        run_id=args.run_id,
        as_of=getattr(args, "as_of", None),
        reprocess=periods,
    )
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


def _backtest(args: argparse.Namespace) -> int:
    config = parse_config(_json(args.config))
    loaded = _json(args.state)
    if not isinstance(loaded, dict):
        msg = f"{args.state} must hold a mapping of stability table to rows"
        raise UsageError(msg)
    store = MemoryStore({k: list(v) for k, v in loaded.items() if k in ALL_KINDS})
    dataset = args.dataset or config.dataset
    if not dataset:
        msg = "backtest needs --dataset: the declaration names none"
        raise UsageError(msg)
    periods = [p.strip() for p in (args.periods or "").split(",") if p.strip()]
    report = backtest(config, store, dataset=dataset, periods=periods, noise_runs=args.noise, seed=args.seed)
    emit(args, report.to_dict(), report.summary())
    return EXIT_OK


def _views(args: argparse.Namespace) -> int:
    statements = view_statements(args.scope, args.prefix)
    emit(args, {"statements": statements}, ";\n\n".join(statements) + ";")
    return EXIT_OK


def _dashboard(args: argparse.Namespace) -> int:
    text = json.dumps(dashboard_definition(args.prefix, args.scope), indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        emit(args, {"output": args.output}, f"wrote {args.output}")
    else:
        emit(args, dashboard_definition(args.prefix, args.scope), text.rstrip("\n"))
    return EXIT_OK


def _action(args: argparse.Namespace) -> int:
    if args.warehouse:
        extra = {k: v for k, v in (("valid_until", args.valid_until), ("resolution", args.resolution)) if v}
        row = _ops(args).act(
            dataset=args.dataset, alert_id=args.alert_id, action=args.action, reason=args.reason, author=args.author,
            **extra,
        )  # fmt: skip
        emit(args, {"action": row}, f"alert action {row['action_id']}: {row['action']} on {row['alert_id']} written")
        return EXIT_OK
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


def _mark(args: argparse.Namespace) -> int:
    created = args.created_at or datetime.now(UTC).isoformat(timespec="seconds")
    unmark = args.command == "unmark"
    if args.warehouse:
        row = _ops(args).mark(
            unmark=unmark, mark="" if unmark else args.mark, dataset=args.dataset, period=args.period,
            reason=args.reason, author=args.author, segment=args.segment or "", column=args.column or "",
            metric=args.metric or "", valid_until="" if unmark else args.valid_until or "",
        )  # fmt: skip
        emit(
            args, {"mark": row}, f"mark {row['mark_id']}: {row['mark']} on {row['period']} of {row['dataset']} written"
        )
        return EXIT_OK
    row = mark_row(
        dataset=args.dataset, period=args.period, mark=UNMARKED if unmark else args.mark, reason=args.reason,
        author=args.author, created_at=created, segment=args.segment or "", column=args.column or "",
        metric=args.metric or "", valid_until="" if unmark else args.valid_until or "",
    )  # fmt: skip
    payload: dict[str, object] = {"mark": row}
    lines = [f"mark {row['mark_id']}: {row['mark']} on {row['period']} of {row['dataset']} by {row['author']}"]
    if args.scope:
        statement = insert_statement(table_name(args.scope, args.prefix, MARKS), row, tuple(SCHEMAS[MARKS]))
        payload["statement"] = statement
        lines.append(statement + ";")
    if args.state:
        state = _state_of(args.state)
        held = [m for m in state.get(MARKS, []) if m.get("mark_id") != row["mark_id"]]
        Path(args.state).write_text(
            json.dumps({**state, MARKS: [*held, row]}, indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
        lines.append(f"appended to {args.state}")
    emit(args, payload, "\n".join(lines))
    return EXIT_OK


def _state_of(path: str) -> dict[str, Any]:
    state = _json(path)
    if not isinstance(state, dict):
        msg = f"{path} must hold a mapping of stability table to rows"
        raise UsageError(msg)
    return state


def _marks(args: argparse.Namespace) -> int:
    as_of = args.as_of or datetime.now(UTC).date().isoformat()
    if args.warehouse:
        found = _ops(args).marks(args.dataset, as_of)
    elif args.state:
        found = effective_marks(_state_of(args.state).get(MARKS, []), as_of, args.dataset)
    else:
        msg = "marks needs --state (a file) or --warehouse with --scope (the workspace)"
        raise UsageError(msg)
    lines = [f"{len(found)} mark(s) in force on {as_of}"]
    for m in found:
        scope = ",".join(f"{c}={m[c]}" for c in ("segment", "column", "metric") if m[c])
        until = f" until {m['valid_until']}" if m.get("valid_until") else ""
        lines.append(
            f"  {m['dataset']} {m['period']} {m['mark']}{' [' + scope + ']' if scope else ''}{until}: {m['reason']}"
        )
    emit(args, {"as_of": as_of, "marks": found}, "\n".join(lines))
    return EXIT_OK


def _warehouse(parser: argparse.ArgumentParser, *, scope: bool = False) -> None:
    parser.add_argument("--warehouse", help="SQL warehouse id: run against the workspace (needs --scope) and write")
    if scope:
        parser.add_argument("--scope", help="catalog.schema of the stability tables")
        parser.add_argument("--prefix", default="stability_", help="table name prefix (default stability_)")


def _selectors(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dataset", required=True, help="the dataset the period belongs to")
    parser.add_argument("--period", required=True, help="the period, as the table's period column writes it")
    parser.add_argument("--segment", help="accepted only: narrow the mark to one segment key")
    parser.add_argument("--column", help="accepted only: narrow the mark to one column")
    parser.add_argument("--metric", help="accepted only: narrow the mark to one metric")
    parser.add_argument("--reason", required=True, help="why")
    parser.add_argument("--author", required=True, help="who decided")
    parser.add_argument("--created-at", help="timestamp of the decision (default now)")
    parser.add_argument("--scope", help="catalog.schema of the stability tables: also print the INSERT statement")
    parser.add_argument("--prefix", default="stability_", help="table name prefix (default stability_)")
    parser.add_argument("--state", help="local JSON file holding the stability tables; the row is appended to 'marks'")
    _warehouse(parser)


def _register_marks(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``stability mark|unmark|marks``."""
    mark = commands.add_parser(
        "mark",
        help="mark a period: accepted, excluded (from the fit), regime_change (a new normal) or invalid",
        parents=[global_parent()],
    )
    _selectors(mark)
    mark.add_argument("--mark", required=True, choices=MARK_NAMES, help="what the period is")
    mark.add_argument("--valid-until", help="last day (YYYY-MM-DD) the mark holds (default: until unmarked)")
    mark.set_defaults(handler=_mark)
    unmark = commands.add_parser(
        "unmark", help="cancel the mark of a period selector (appends an 'unmarked' row)", parents=[global_parent()]
    )
    _selectors(unmark)
    unmark.set_defaults(handler=_mark)
    listed = commands.add_parser("marks", help="list the marks in force in a state file", parents=[global_parent()])
    listed.add_argument("--state", help="local JSON file holding the stability tables; only read")
    _warehouse(listed, scope=True)
    listed.add_argument("--dataset", help="only the marks of this dataset")
    listed.add_argument("--as-of", help="the date the marks must hold on (default today)")
    listed.set_defaults(handler=_marks)


def _register_reprocess(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``stability reprocess``."""
    redo = commands.add_parser(
        "reprocess",
        help="profile and judge again the periods of a backfill or correction (exit 4 when the gate fails)",
        parents=[global_parent()],
    )
    redo.add_argument("--config", help="JSON file with a stability declaration (offline reprocess)")
    redo.add_argument("--job", help="workspace job id: start a run that carries --periods to the runner (--reprocess)")
    _warehouse(redo, scope=True)
    _source(redo)
    redo.add_argument("--dataset", help="dataset name (default: the declaration's, the table or the file name)")
    redo.add_argument(
        "--periods", required=True, help="periods to profile again: a list (a,b) or a range (first..last)"
    )
    redo.add_argument("--state", help="local JSON file holding the stability tables; read, then updated")
    redo.add_argument("--run-id", default="cli", help="run identity recorded on the rows")
    redo.set_defaults(handler=_reprocess)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``stability`` verbs: check, reprocess, backtest, views, dashboard, decisions and workspace reads."""
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
    _register_reprocess(commands)
    bt = commands.add_parser(
        "backtest",
        help="replay a candidate configuration over the stored profiles and report what would change (never writes)",
        parents=[global_parent()],
    )
    bt.add_argument("--config", required=True, help="JSON file with the candidate stability declaration")
    bt.add_argument("--dataset", help="dataset name (default: the declaration's)")
    bt.add_argument("--state", required=True, help="local JSON file holding the stability tables; only read")
    bt.add_argument("--periods", help="report only these periods: a list (a,b) or a range (first..last)")
    bt.add_argument(
        "--noise", type=int, default=0, metavar="RUNS", help="also estimate the false-alarm rate on RUNS noise series"
    )
    bt.add_argument("--seed", type=int, default=0, help="seed of the noise estimate (default 0)")
    bt.set_defaults(handler=_backtest)
    views = commands.add_parser("views", help="print CREATE VIEW statements for dashboards", parents=[global_parent()])
    views.add_argument("--scope", required=True, help="catalog.schema holding the stability tables")
    views.add_argument("--prefix", default="stability_", help="table name prefix (default stability_)")
    views.set_defaults(handler=_views)
    board = commands.add_parser(
        "dashboard",
        help="print the AI/BI dashboard definition (.lvdash.json) over the stability views",
        parents=[global_parent()],
    )
    board.add_argument("--scope", help="catalog.schema holding the views (default: unqualified names)")
    board.add_argument("--prefix", default="stability_", help="table name prefix (default stability_)")
    board.add_argument("--output", help="write the definition to this file instead of printing it")
    board.set_defaults(handler=_dashboard)
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
    _warehouse(act)
    act.set_defaults(handler=_action)
    _register_marks(commands)
    _register_reads(commands)


def _register_reads(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the workspace reads ``stability status|series|actions``."""
    for name, handler, text in (
        ("status", _status, "list the datasets with status, latest period, last run and open alerts"),
        ("series", _series, "list the points of a dataset's series with limits, band, marks and actions"),
        ("actions", _actions, "list the actions on alerts, or with --queue the open alerts"),
    ):
        parser = commands.add_parser(name, help=text, parents=[global_parent()])
        parser.add_argument("--warehouse", required=True, help="SQL warehouse id")
        parser.add_argument("--scope", required=True, help="catalog.schema of the stability tables")
        parser.add_argument("--prefix", default="stability_", help="table name prefix (default stability_)")
        if name == "series":
            parser.add_argument("--dataset", required=True)
            for flag in ("column", "metric", "segment"):
                parser.add_argument(f"--{flag}", help=f"only this {flag}")
        if name == "actions":
            parser.add_argument("--dataset", help="only this dataset")
            parser.add_argument("--queue", action="store_true", help="the open alerts with their latest action")
        parser.set_defaults(handler=handler)


__all__ = ["register"]
