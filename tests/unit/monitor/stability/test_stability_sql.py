"""The Statement Execution wrapper and the named-parameter writes."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.sql import SqlRunner, write_row


def _response(state: str, data: list[list[str]] | None = None, *, message: str = "") -> SimpleNamespace:
    columns = [SimpleNamespace(name="dataset"), SimpleNamespace(name="period")]
    return SimpleNamespace(
        statement_id="s1",
        status=SimpleNamespace(state=state, error=SimpleNamespace(message=message) if message else None),
        result=SimpleNamespace(data_array=data),
        manifest=SimpleNamespace(schema=SimpleNamespace(columns=columns)),
    )


class _Api:
    def __init__(self, *responses: SimpleNamespace) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.polled = 0

    def execute_statement(self, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        self.calls.append(kwargs)
        return self.responses.pop(0)

    def get_statement(self, _: str) -> SimpleNamespace:
        self.polled += 1
        return self.responses.pop(0)


def _runner(api: _Api) -> SqlRunner:
    return SqlRunner(SimpleNamespace(statement_execution=api), "wh1", parameter=lambda n, v: (n, v), poll_seconds=0)


def test_rows_come_back_as_dictionaries_and_values_travel_as_parameters() -> None:
    api = _Api(_response("SUCCEEDED", [["orders", "2025-04"]]))
    rows = _runner(api).run("SELECT * FROM t WHERE dataset = :dataset", {"dataset": "o'; DROP TABLE x"})
    assert rows == [{"dataset": "orders", "period": "2025-04"}]
    call = api.calls[0]
    assert call["warehouse_id"] == "wh1" and call["parameters"] == [("dataset", "o'; DROP TABLE x")]
    assert "DROP" not in call["statement"]


def test_a_running_statement_is_polled_and_a_failure_is_reported() -> None:
    api = _Api(_response("RUNNING"), _response("SUCCEEDED", []))
    assert _runner(api).run("SELECT 1") == [] and api.polled == 1
    with pytest.raises(MonitorError, match="failed: boom"):
        _runner(_Api(_response("FAILED", message="boom"))).run("SELECT 1")
    with pytest.raises(MonitorError, match="did not finish"):
        SqlRunner(
            SimpleNamespace(statement_execution=_Api(_response("RUNNING"), _response("RUNNING"))),
            "wh1", parameter=lambda n, v: (n, v), poll_seconds=0, max_polls=1,
        ).run("SELECT 1")  # fmt: skip


def test_a_blank_warehouse_and_an_unsafe_parameter_name_are_refused() -> None:
    with pytest.raises(MonitorError, match="warehouse"):
        SqlRunner(object(), " ")
    with pytest.raises(MonitorError, match="plain name"):
        _runner(_Api(_response("SUCCEEDED"))).run("SELECT 1", {"a b": 1})


def test_write_row_keeps_every_value_out_of_the_statement() -> None:
    row = {"mark_id": "m1", "dataset": "o'rders", "valid_until": "2026-01-01", "reason": ""}
    statement, params = write_row("main.mon.stability_marks", row, ("mark_id", "dataset", "valid_until", "reason"))
    assert statement == (
        "INSERT INTO main.mon.stability_marks (`mark_id`, `dataset`, `valid_until`, `reason`) "
        "VALUES (:mark_id, :dataset, CAST(:valid_until AS DATE), :reason)"
    )
    assert params == {"mark_id": "m1", "dataset": "o'rders", "valid_until": "2026-01-01", "reason": ""}
    assert "o'rders" not in statement
    assert write_row("t", {"valid_until": ""}, ("valid_until",))[1] == {"valid_until": None}
