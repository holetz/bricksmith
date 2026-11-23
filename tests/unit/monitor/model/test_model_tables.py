"""Model monitoring tables: keys, typed rows, merge and views that read only the latest finished run."""
# ruff: noqa: PT018 - dense assertions on table rows

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.tables import (
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
    assert options["on_empty"] == "allow_empty"


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
