"""Model monitoring tables: keys, typed rows, merge and views that read only the latest finished run."""
# ruff: noqa: PT018 - dense assertions on table rows

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.tables import (
    ACTIONS,
    ALERTS,
    KEYS,
    KINDS,
    RESULTS,
    RUNS,
    SCHEMAS,
    merge_rows,
    table_name,
    typed_row,
    view_statements,
    write_options,
)


def test_every_key_is_a_column_and_results_are_keyed_by_run() -> None:
    assert set(KINDS) == set(SCHEMAS) == set(KEYS)
    for kind in KINDS:
        assert set(KEYS[kind]) <= set(SCHEMAS[kind])
    assert "run_id" in KEYS[RESULTS] and "data_period" in SCHEMAS[RESULTS]
    assert {"development_value", "out_of_time_value", "monitoring_period"} <= set(SCHEMAS[RESULTS])
    options = write_options(RUNS)
    assert options["write"] == "merge" and options["keys"] == ["run_id", "model"]
    assert options["on_empty"] == "allow_empty" and options["evolution"] == {}


def test_judgment_and_alert_span_columns_are_additive() -> None:
    assert {"limit_kind", "limit_reference", "compared_value", "baseline_mean", "baseline_sd", "baseline_points",
            "trend_status", "trend_expected", "trend_deviation"} <= set(SCHEMAS[RESULTS])  # fmt: skip
    assert {"kind", "state", "opened_period", "escalated_period", "closed_period", "last_period",
            "flagged_periods", "recurrence"} <= set(SCHEMAS[ALERTS])  # fmt: skip
    assert {"open_alerts", "critical_alerts"} <= set(SCHEMAS[RUNS])
    assert KEYS[ALERTS] == ("model", "run_id", "alert_id")


def test_typed_rows_and_merge() -> None:
    row = {"model": "m", "model_version": "1", "run_id": "r", "monitoring_period": "2026-01", "segment": "",
           "column": "score", "metric": "psi", "value": 1, "rows": 3.0, "period_date": "2026-01-01"}  # fmt: skip
    typed = dict(zip(SCHEMAS[RESULTS], typed_row(RESULTS, row), strict=True))
    assert typed["value"] == 1.0 and isinstance(typed["value"], float)
    assert typed["rows"] == 3 and typed["period_date"] == date(2026, 1, 1) and typed["light"] is None
    merged = merge_rows(RESULTS, [row], [{**row, "value": 2.0}, {**row, "run_id": "s"}])
    assert sorted((r["run_id"], r["value"]) for r in merged) == [("r", 2.0), ("s", 1)]


def test_table_names_reject_unsafe_identifiers() -> None:
    assert table_name("main.audit", "model_monitoring_", "runs") == "main.audit.model_monitoring_runs"
    with pytest.raises(MonitorError, match=r"scope must be catalog\.schema"):
        table_name("main.audit; DROP", "x_", "runs")


def _create(db: sqlite3.Connection, kind: str) -> None:
    columns = ", ".join(f"`{c}` {t}" for c, t in SCHEMAS[kind].items())
    db.execute(f"CREATE TABLE main.model_monitoring_{kind} ({columns})")


def _insert(db: sqlite3.Connection, kind: str, rows: list[dict[str, object]]) -> None:
    names = list(SCHEMAS[kind])
    marks = ", ".join("?" for _ in names)
    db.executemany(
        f"INSERT INTO main.model_monitoring_{kind} VALUES ({marks})",  # noqa: S608 - test constants
        [tuple(r.get(c) for c in names) for r in rows],
    )


def _result(run: str, period: str, value: float, light: str, rank: int) -> dict[str, object]:
    return {"model": "m", "model_version": "1", "run_id": run, "monitoring_period": period, "segment": "",
            "column": "score", "metric": "psi", "value": value, "light": light, "light_rank": rank,
            "reason": ""}  # fmt: skip


def test_views_read_only_the_latest_finished_run() -> None:
    db = sqlite3.connect(":memory:")
    for kind in KINDS:
        _create(db, kind)
    _insert(db, RESULTS, [
        _result("r1", "2026-01", 0.05, "green", 0), _result("r1", "2026-02", 0.30, "red", 2),
        _result("r2", "2026-02", 0.12, "yellow", 1),  # a later partial run recomputed February
        _result("r3", "2026-02", 0.01, "green", 0),  # a run that died before its record: never read
        _result("r4", "2026-02", 0.02, "green", 0),  # a failed run: never read
    ])  # fmt: skip
    _insert(db, RUNS, [
        {"run_id": "r1", "model": "m", "status": "success", "finished_at": "2026-03-01T00:00:00"},
        {"run_id": "r2", "model": "m", "status": "partial", "finished_at": "2026-03-02T00:00:00"},
        {"run_id": "r4", "model": "m", "status": "failed", "finished_at": "2026-03-04T00:00:00"},
    ])  # fmt: skip
    for statement in view_statements("main"):
        db.execute(statement.replace("CREATE OR REPLACE VIEW", "CREATE VIEW"))
    latest = db.execute(
        "SELECT monitoring_period, run_id, value FROM main.model_monitoring_latest ORDER BY 1"
    ).fetchall()
    assert latest == [("2026-01", "r1", 0.05), ("2026-02", "r2", 0.12)]
    status = db.execute(
        "SELECT monitoring_period, light, red, yellow FROM main.model_monitoring_model_status ORDER BY 1"
    ).fetchall()
    assert status == [("2026-01", "green", 0, 0), ("2026-02", "yellow", 0, 1)]
    assert db.execute("SELECT count(*) FROM main.model_monitoring_open_alerts").fetchone() == (0,)


def test_open_alerts_view_keeps_open_spans_only() -> None:
    db = sqlite3.connect(":memory:")
    for kind in KINDS:
        _create(db, kind)
    alert = {"model": "m", "model_version": "1", "run_id": "r1", "monitoring_period": "2026-02", "metric": "psi"}
    _insert(db, ALERTS, [
        {**alert, "alert_id": "a", "kind": "light", "state": "open"},
        {**alert, "alert_id": "b", "kind": "persistence", "state": "closed"},
        {**alert, "alert_id": "c", "kind": "trend", "state": "open"},
        {**alert, "alert_id": "d"},  # written before alert states existed
    ])  # fmt: skip
    _insert(db, RUNS, [{"run_id": "r1", "model": "m", "status": "success", "finished_at": "2026-03-01T00:00:00"}])
    for statement in view_statements("main"):
        db.execute(statement.replace("CREATE OR REPLACE VIEW", "CREATE VIEW"))
    found = db.execute("SELECT alert_id FROM main.model_monitoring_open_alerts ORDER BY 1").fetchall()
    assert found == [("a",), ("c",), ("d",)]


def test_actions_table_is_keyed_by_the_action_and_alerts_carry_the_applied_one() -> None:
    assert KEYS[ACTIONS] == ("model", "action_id") and "run_id" not in SCHEMAS[ACTIONS]
    assert {"alert_id", "action", "reason", "author", "valid_until", "created_at"} <= set(SCHEMAS[ACTIONS])
    assert {"action", "action_reason", "action_author", "action_at", "valid_until"} <= set(SCHEMAS[ALERTS])
    assert KINDS[-1] == RUNS and ACTIONS in KINDS  # the run record stays the last kind
    assert write_options(ACTIONS)["keys"] == ["model", "action_id"]


def _days(end: str | None, start: str | None) -> int | None:
    if end is None or start is None:
        return None
    return (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days


def _governed() -> sqlite3.Connection:
    """Return a database with the views over a month of alerts, runs and actions (dates as text)."""
    db = sqlite3.connect(":memory:")
    db.create_function("datediff", 2, _days)
    db.create_function("to_date", 1, lambda v: None if v is None else v[:10])
    for kind in KINDS:
        _create(db, kind)
    alert = {"model": "m", "model_version": "1", "metric": "psi", "run_id": "r1", "monitoring_period": "2026-01"}
    _insert(db, ALERTS, [
        {**alert, "alert_id": "a", "kind": "persistence", "state": "open", "created_at": "2026-03-01T00:00:00"},
        {**alert, "alert_id": "b", "kind": "light", "state": "open", "created_at": "2026-03-01T00:00:00"},
        {**alert, "alert_id": "a", "run_id": "r2", "kind": "persistence", "state": "closed",
         "created_at": "2026-03-10T00:00:00"},
    ])  # fmt: skip
    _insert(db, RUNS, [
        {"run_id": "r1", "model": "m", "status": "success", "finished_at": "2026-03-01T00:00:00"},
        {"run_id": "r2", "model": "m", "status": "success", "finished_at": "2026-03-10T00:00:00"},
    ])  # fmt: skip
    _insert(db, RESULTS, [
        {"model": "m", "model_version": "1", "run_id": "r2", "monitoring_period": p, "light": "green",
         "light_rank": 0} for p in ("2026-01", "2026-02")
    ])  # fmt: skip
    action = {"model": "m", "reason": "why", "author": "ana"}
    _insert(db, ACTIONS, [
        {**action, "action_id": "x1", "alert_id": "a", "action": "analysis", "created_at": "2026-03-03T00:00:00"},
        {**action, "action_id": "x2", "alert_id": "a", "action": "closed", "created_at": "2026-03-05T00:00:00"},
        {**action, "action_id": "x3", "alert_id": "b", "action": "risk_accepted", "valid_until": "2000-01-01",
         "created_at": "2026-03-02T00:00:00"},
        {**action, "action_id": "x4", "alert_id": "c", "action": "risk_accepted", "valid_until": "2999-01-01",
         "created_at": "2026-03-02T00:00:00"},
        {**action, "action_id": "x5", "alert_id": "c", "action": "analysis", "created_at": "2026-03-04T00:00:00"},
    ])  # fmt: skip
    for statement in view_statements("main"):
        db.execute(statement.replace("CREATE OR REPLACE VIEW", "CREATE VIEW"))
    return db


def test_governance_views_list_latest_actions_and_acceptances() -> None:
    db = _governed()
    latest = db.execute("SELECT alert_id, action FROM main.model_monitoring_actions_latest ORDER BY 1").fetchall()
    assert latest == [("a", "closed"), ("b", "risk_accepted"), ("c", "analysis")]
    found = db.execute("SELECT alert_id, status FROM main.model_monitoring_acceptances ORDER BY 1").fetchall()
    assert found == [("b", "expired"), ("c", "superseded")]
    assert db.execute(
        "SELECT days_left < 0 FROM main.model_monitoring_acceptances WHERE alert_id = 'b'"
    ).fetchone() == (1,)


def test_governance_views_measure_coverage_and_response_times() -> None:
    db = _governed()
    coverage = db.execute("SELECT model, periods, first_period, latest_period FROM main.model_monitoring_coverage")
    assert coverage.fetchall() == [("m", 2, "2026-01", "2026-02")]
    assert db.execute("SELECT days_since_run > 0 FROM main.model_monitoring_coverage").fetchone() == (1,)
    response = db.execute(
        "SELECT alert_id, days_to_first_action, days_to_resolve FROM main.model_monitoring_alert_response ORDER BY 1"
    ).fetchall()
    assert response == [("a", 2, 4), ("b", 1, None)]
    summary = db.execute("SELECT model, alerts, acted, resolved, mean_days_to_resolve FROM "
                         "main.model_monitoring_response_summary").fetchall()  # fmt: skip
    assert summary == [("m", 2, 2, 1, 4.0)]


def test_lag_columns_are_additive_result_columns() -> None:
    assert {"lag", "lag_unit"} <= set(SCHEMAS[RESULTS])
    assert "lag" not in KEYS[RESULTS]
    row = {"model": "m", "lag": 2, "lag_unit": "months"}
    assert merge_rows(RESULTS, [], [row])[0]["lag_unit"] == "months"
