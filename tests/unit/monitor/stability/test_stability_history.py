"""Judgment history: every judgment a run reports is appended, so what was said at a date stays auditable."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.hook import resolve
from bricksmith.monitor.stability.tables import (
    ALERT_EVENTS,
    ALL_KINDS,
    HISTORY_KINDS,
    KEYS,
    RESULTS,
    RESULTS_HISTORY,
    SCHEMAS,
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
