"""Judgment history: every judgment a run reports is appended, so what was said at a date stays auditable."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

from bricksmith.context import ExecutionContext
from bricksmith.graph import load_graph
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.hook import delta_version, resolve
from bricksmith.monitor.stability.tables import (
    ALERT_EVENTS,
    ALL_KINDS,
    HISTORY_KINDS,
    KEYS,
    RESULTS,
    RESULTS_HISTORY,
    SCHEMAS,
    as_of_statement,
    merge_rows,
    results_as_of,
    table_name,
)
from bricksmith.plan import build_plan
from bricksmith.runner import InMemoryTableIO, apply_plan

if TYPE_CHECKING:
    from pathlib import Path


def test_declaration_history_default_and_validation() -> None:
    plain = parse_config({"period": "month"})
    kept = parse_config({"period": "month", "history": True, "history_periods": 6})
    assert not plain.history and plain.history_periods == 0
    assert "history" not in plain.to_dict() and "history_periods" not in plain.to_dict()
    assert kept.history and kept.history_periods == 6
    assert kept.to_dict()["history"] is True and kept.to_dict()["history_periods"] == 6
    for bad in ({"history": "yes"}, {"history_periods": 3}, {"history": True, "history_periods": -1}):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", **bad})


def test_schema_history_tables_are_keyed_by_run() -> None:
    assert set(HISTORY_KINDS) == {RESULTS_HISTORY, ALERT_EVENTS} <= set(ALL_KINDS)
    assert {"run_id", "judged_at", "judgment_fingerprint", "data_version"} <= set(SCHEMAS[RESULTS_HISTORY])
    judged = {"period", "segment", "column", "metric", "value", "status", "reason", "green_limit", "red_limit"}
    assert judged <= set(SCHEMAS[RESULTS]) and judged <= set(SCHEMAS[RESULTS_HISTORY])
    assert "run_id" in KEYS[RESULTS_HISTORY] and "run_id" in KEYS[ALERT_EVENTS]
    assert table_name("c.s", "stability_", RESULTS_HISTORY) == "c.s.stability_results_history"


def test_declaration_history_adds_the_two_tables_only_when_on() -> None:
    outputs = {"orders": "c.s.orders"}
    off = resolve({"period": "month"}, outputs)[0].tables
    on = resolve({"period": "month", "history": True}, outputs)[0].tables
    assert RESULTS_HISTORY not in off and ALERT_EVENTS not in off
    assert on[RESULTS_HISTORY] == "c.s.stability_results_history"
    assert on[ALERT_EVENTS] == "c.s.stability_alert_events"


def _month(month: str, count: int, shift: float = 0.0) -> list[dict[str, object]]:
    return [{"month": month, "amount": float(i % 20) + i * 0.013 + shift} for i in range(count)]


def _rows(shifted: str = "") -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m in range(1, 6):
        month = f"2026-{m:02d}"
        out += _month(month, 80, shift=40.0 if month == shifted else 0.0)
    return out


def _check(  # noqa: PLR0913 - one keyword per scenario input
    rows: list[dict[str, object]],
    store: MemoryStore,
    run_id: str,
    *,
    now: str = "2026-06-01T00:00:00+00:00",
    reprocess: list[str] | None = None,
    data_version: int | None = None,
    **declared: object,
) -> CheckResult:
    config = parse_config(
        {
            "period": "month",
            "min_rows": 10,
            "psi_limits": "fixed",
            "min_rows_per_bin": 0,
            "baseline": "previous",
            "history": True,
            **declared,
        }
    )
    return check(
        rows, config, store, dataset="orders", run_id=run_id, now=now, reprocess=reprocess or (),
        data_version=data_version,
    )  # fmt: skip


def _commit(store: MemoryStore, result: CheckResult) -> None:
    for kind, rows in result.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), rows)


def _psi(rows: list[dict[str, object]], period: str) -> list[dict[str, object]]:
    return [r for r in rows if r["period"] == period and r["metric"] == "psi" and not r["segment"]]


def test_results_history_keeps_the_earlier_judgment_after_a_reprocess_turns_a_period_red() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(), store, "r1", now="2026-06-01T00:00:00+00:00", data_version=7))
    _commit(store, _check(_rows("2026-02"), store, "r2", now="2026-06-02T00:00:00+00:00", reprocess=["2026-02"]))
    results = [r for r in store.tables["results"] if r["period"] == "2026-02" and r["metric"] == "psi"]
    assert results[0]["status"] == "red"  # the current table says red
    history = _psi(store.tables["results_history"], "2026-02")
    by_run = {str(r["run_id"]): r for r in history}
    assert by_run["r1"]["status"] != "red" and by_run["r2"]["status"] == "red"
    assert by_run["r1"]["judged_at"] == "2026-06-01T00:00:00+00:00" and by_run["r1"]["data_version"] == 7
    assert by_run["r2"]["data_version"] is None
    assert by_run["r1"]["judgment_fingerprint"] and by_run["r2"]["judgment_fingerprint"]


def test_results_history_is_off_by_default() -> None:
    config = parse_config({"period": "month", "min_rows": 10, "psi_limits": "fixed", "min_rows_per_bin": 0})
    result = check(_rows(), config, MemoryStore(), dataset="orders", run_id="r1")
    assert "results_history" not in result.tables and "alert_events" not in result.tables


def test_results_history_rows_of_one_run_never_replace_another_runs() -> None:
    store = MemoryStore()
    for run, now in (("r1", "2026-06-01T00:00:00+00:00"), ("r2", "2026-06-02T00:00:00+00:00")):
        _commit(store, _check(_rows(), store, run, now=now, reprocess=["2026-02"] if run == "r2" else None))
    runs = {str(r["run_id"]) for r in store.tables["results_history"]}
    assert runs == {"r1", "r2"}


def test_retention_history_periods_bounds_the_periods_a_run_appends() -> None:
    result = _check(_rows(), MemoryStore(), "r1", history_periods=2)
    assert {r["period"] for r in result.tables["results_history"]} == {"2026-04", "2026-05"}
    assert len({r["period"] for r in result.tables["results"]}) == 5  # the current table is untouched


def _events(store: MemoryStore) -> list[tuple[str, str]]:
    return [(str(r["alert_id"]), str(r["event"])) for r in store.tables.get("alert_events", [])]


def test_events_record_opened_and_then_stay_silent_on_an_unchanged_replay() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows("2026-05"), store, "r1", persistence={"immediate_red": True}))
    opened = [e for e in store.tables["alert_events"] if e["event"] == "opened"]
    assert opened and all(e["run_id"] == "r1" and e["state"] == "open" and e["occurred_at"] for e in opened)
    before = len(store.tables["alert_events"])
    _commit(store, _check(_rows("2026-05"), store, "r2", persistence={"immediate_red": True}))
    assert len(store.tables["alert_events"]) == before  # nothing transitioned


def test_events_record_closed_and_superseded_after_a_reprocess_changes_the_judgment() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows("2026-02"), store, "r1", persistence={"immediate_red": True}))
    first = set(_events(store))
    assert any(event == "opened" for _, event in first)
    _commit(store, _check(_rows(), store, "r2", reprocess=["2026-02"], persistence={"immediate_red": True}))
    later = {e for e in _events(store) if e not in first}
    kinds = {event for _, event in later}
    assert kinds & {"closed", "superseded"}


def test_events_record_each_human_action_once() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows("2026-05"), store, "r1", persistence={"immediate_red": True}))
    alert_id = str(next(r["alert_id"] for r in store.tables["alerts"] if r["state"] == "open"))
    store.tables["actions"] = [
        {
            "action_id": "a1", "dataset": "orders", "alert_id": alert_id, "action": "risk_accepted",
            "resolution": "", "reason": "known backfill", "author": "ana", "valid_until": "2099-01-01",
            "created_at": "2026-06-01T12:00:00+00:00",
        }
    ]  # fmt: skip
    for run in ("r2", "r3"):
        _commit(store, _check(_rows("2026-05"), store, run, persistence={"immediate_red": True}))
    applied = [r for r in store.tables["alert_events"] if r["event"] == "action_applied"]
    assert len(applied) == 1 and applied[0]["action_id"] == "a1" and applied[0]["alert_id"] == alert_id
    assert applied[0]["state"] == "risk_accepted" and applied[0]["message"] == "known backfill"


def test_as_of_returns_the_judgment_that_stood_at_a_date() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(), store, "r1", now="2026-06-01T08:00:00+00:00"))
    _commit(store, _check(_rows("2026-02"), store, "r2", now="2026-06-03T08:00:00+00:00", reprocess=["2026-02"]))
    history = store.tables["results_history"]

    def status(as_of: str) -> str | None:
        found = _psi(results_as_of(history, as_of), "2026-02")
        return str(found[0]["status"]) if found else None

    assert status("2026-05-31") is None  # nothing was reported yet
    assert status("2026-06-01") not in (None, "red") and status("2026-06-02T00:00:00+00:00") == status("2026-06-01")
    assert status("2026-06-03") == "red"  # a date covers its whole day
    assert status("2026-06-03T07:59:59+00:00") != "red"


def test_as_of_statement_is_one_select_over_the_history_and_refuses_what_is_not_a_date() -> None:
    sql = as_of_statement("c.s", "2026-03-31")
    assert "c.s.stability_results_history" in sql and "substr(judged_at, 1, 10) <= '2026-03-31'" in sql
    assert "judged_at <= '2026-03-31T12:00:00Z'" in as_of_statement("c.s", "2026-03-31T12:00:00Z", "x_").replace(
        "x_results_history", ""
    )
    for bad in ("tomorrow", "2026-03-31' OR 1=1 --", ""):
        with pytest.raises(MonitorError):
            as_of_statement("c.s", bad)


class _Spark:
    def __init__(self, rows: list[dict[str, int]] | Exception) -> None:
        self.rows, self.statements = rows, []

    def sql(self, statement: str) -> _Spark:
        self.statements.append(statement)
        return self

    def collect(self) -> list[dict[str, int]]:
        if isinstance(self.rows, Exception):
            raise self.rows
        return self.rows


def test_data_version_is_the_latest_delta_version_and_never_fails_the_check() -> None:
    spark = _Spark([{"version": 41}])
    assert delta_version(spark, "c.s.orders") == 41
    assert spark.statements == ["DESCRIBE HISTORY c.s.orders LIMIT 1"]
    assert delta_version(_Spark([]), "c.s.orders") is None
    assert delta_version(_Spark(RuntimeError("not a Delta table")), "c.s.orders") is None
    assert delta_version(None, "c.s.orders") is None


def test_hook_writes_both_history_tables_when_declared(tmp_path: Path) -> None:
    path = tmp_path / "catalogs/main/sales/orders.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        textwrap.dedent(
            """
            from bricksmith.tasks import task

            @task(kind="table", outputs="orders", metadata={"stability": {
                "period": "month", "min_rows": 10, "psi_limits": "fixed", "min_rows_per_bin": 0, "baseline": "previous", "history": True}})
            def orders(ctx):
                ctx.write("orders", [
                    {"month": f"2026-{m:02d}", "amount": float(i % 20) + i * 0.013}
                    for m in range(1, 5) for i in range(80)
                ])
            """
        ),
        encoding="utf-8",
    )
    io = InMemoryTableIO()
    execution = ExecutionContext(environment="test")
    graph = load_graph(tmp_path, context=execution)
    assert apply_plan(build_plan(graph), graph, project_root=tmp_path, execution=execution, table_io=io).ok
    history = io.tables["main.sales.stability_results_history"]
    assert isinstance(history, list) and history and {r["data_version"] for r in history} == {None}
    assert isinstance(io.tables["main.sales.stability_alert_events"], list)
