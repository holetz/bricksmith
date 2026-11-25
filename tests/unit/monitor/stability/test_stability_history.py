"""Judgment history: every judgment a run reports is appended, so what was said at a date stays auditable."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.hook import resolve
from bricksmith.monitor.stability.tables import (
    ALERT_EVENTS,
    ALL_KINDS,
    HISTORY_KINDS,
    KEYS,
    RESULTS,
    RESULTS_HISTORY,
    SCHEMAS,
    merge_rows,
    table_name,
)


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
    config = parse_config({"period": "month", "min_rows": 10, "baseline": "previous", "history": True, **declared})
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
    config = parse_config({"period": "month", "min_rows": 10})
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
