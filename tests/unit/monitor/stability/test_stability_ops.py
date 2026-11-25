"""The workspace operations behind the verbs and the console, over a fake SQL runner and a fake jobs client."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.ops import Operations
from bricksmith.monitor.stability.tables import VIEW_COLUMNS

NOW = "2026-02-01T10:00:00+00:00"


class FakeSql:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.rows = rows or []

    def run(self, statement: str, params: Any = None) -> list[dict[str, Any]]:  # noqa: ANN401
        self.calls.append((statement, dict(params or {})))
        return self.rows


class FakeJobs:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run_now(self, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        self.calls.append(kwargs)
        return SimpleNamespace(run_id=77)


def _ops(sql: FakeSql, jobs: FakeJobs | None = None) -> Operations:
    return Operations(sql, "main.mon", jobs=jobs, now=lambda: NOW)


def test_status_reads_the_dataset_view_contract_worst_first() -> None:
    sql = FakeSql([{"dataset": "orders"}])
    assert _ops(sql).status() == [{"dataset": "orders"}]
    statement, params = sql.calls[0]
    assert statement.startswith("SELECT `dataset`, `version`") and "FROM main.mon.stability_datasets" in statement
    assert "ORDER BY `status_rank` DESC" in statement and params == {}
    assert all(f"`{c}`" in statement for c in VIEW_COLUMNS["datasets"])


def test_series_filters_are_parameters_and_unknown_filters_are_refused() -> None:
    sql = FakeSql()
    _ops(sql).series("o'rders; DROP", metric="psi", column="age")
    statement, params = sql.calls[0]
    assert "WHERE `dataset` = :dataset AND `metric` = :metric AND `column` = :column" in statement
    assert params == {"dataset": "o'rders; DROP", "metric": "psi", "column": "age"} and "DROP" not in statement
    with pytest.raises(MonitorError, match="unknown filter"):
        _ops(sql).series("orders", bogus="x")
    with pytest.raises(MonitorError, match="dataset is required"):
        _ops(sql).series(" ")


def test_mark_and_unmark_append_the_rows_the_core_validates() -> None:
    sql = FakeSql()
    ops = _ops(sql)
    row = ops.mark(dataset="orders", period="2025-04", mark="excluded", reason="migration", author="ana")
    statement, params = sql.calls[0]
    assert statement.startswith("INSERT INTO main.mon.stability_marks") and "VALUES (:mark_id" in statement
    assert params["mark"] == "excluded" and params["created_at"] == NOW and row["mark_id"] == params["mark_id"]
    ops.mark(unmark=True, dataset="orders", period="2025-04", reason="back", author="ana")
    assert sql.calls[1][1]["mark"] == "unmarked" and sql.calls[1][1]["valid_until"] is None
    with pytest.raises(MonitorError):
        ops.mark(dataset="orders", period="2025-04", mark="excluded", reason="", author="ana")
    assert len(sql.calls) == 2  # a refused decision writes nothing


def test_marks_in_force_drop_the_unmarked() -> None:
    held = {
        "mark_id": "1", "dataset": "orders", "period": "2025-04", "segment": "", "column": "", "metric": "",
        "mark": "excluded", "reason": "r", "author": "a", "valid_until": None,
        "created_at": "2026-01-01T00:00:00+00:00",
    }  # fmt: skip
    undone = {**held, "mark_id": "2", "mark": "unmarked", "created_at": "2026-01-02T00:00:00+00:00"}
    assert [m["mark"] for m in _ops(FakeSql([held])).marks("orders")] == ["excluded"]
    assert _ops(FakeSql([held, undone])).marks("orders") == []


def test_act_validates_and_appends() -> None:
    sql = FakeSql()
    row = _ops(sql).act(dataset="orders", alert_id="a1", action="closed", resolution="fixed", reason="r", author="ana")
    assert sql.calls[0][0].startswith("INSERT INTO main.mon.stability_actions")
    assert row["resolution"] == "fixed" and sql.calls[0][1]["action_id"] == row["action_id"]
    with pytest.raises(MonitorError):
        _ops(sql).act(dataset="orders", alert_id="a1", action="risk_accepted", reason="r", author="ana")


def test_reprocess_starts_the_job_with_the_periods() -> None:
    jobs = FakeJobs()
    started = _ops(FakeSql(), jobs).reprocess("42", "2026-01..2026-03,2026-05")
    assert jobs.calls == [{"job_id": 42, "job_parameters": {"reprocess": "2026-01..2026-03,2026-05"}}]
    assert started["run_id"] == 77
    for bad in ("", "a;b", "x' OR 1"):
        with pytest.raises(MonitorError, match="periods"):
            _ops(FakeSql(), jobs).reprocess("42", bad)
    with pytest.raises(MonitorError, match="job id"):
        _ops(FakeSql(), jobs).reprocess("abc", "2026-01")
    with pytest.raises(MonitorError, match="jobs client"):
        _ops(FakeSql()).reprocess("42", "2026-01")
    assert len(jobs.calls) == 1


def test_alerts_actions_and_runs_read_their_tables() -> None:
    sql = FakeSql()
    ops = _ops(sql)
    ops.alerts("orders")
    ops.actions()
    ops.runs("orders", limit=5)
    tables = [c[0].split(" FROM ")[1].split()[0] for c in sql.calls]
    assert tables == ["main.mon.stability_open_alerts", "main.mon.stability_actions", "main.mon.stability_runs"]
    assert sql.calls[2][0].endswith("ORDER BY `started_at` DESC LIMIT 5") and sql.calls[2][1] == {"dataset": "orders"}


def test_a_bad_scope_is_refused_at_construction() -> None:
    with pytest.raises(MonitorError):
        Operations(FakeSql(), "main.mon; DROP")
