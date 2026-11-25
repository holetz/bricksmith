"""Reprocessing: re-profile and re-judge explicit periods after a backfill, and the gate over profiled periods."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import Gate, parse_config
from bricksmith.monitor.stability.tables import RUNS, SCHEMAS


def test_declaration_gate_periods_default_and_validation() -> None:
    plain = parse_config({"period": "month", "gate": {"fail_on": ["red"]}})
    profiled = parse_config({"period": "month", "gate": {"fail_on": ["red"], "periods": "profiled"}})
    assert plain.gate.periods == "latest" and profiled.gate.periods == "profiled"
    assert "periods" not in plain.gate.to_dict() and profiled.gate.to_dict()["periods"] == "profiled"
    assert plain.to_dict()["gate"] == Gate(("red",)).to_dict()
    for bad in ("all", 1, None):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", "gate": {"periods": bad}})


def test_schema_runs_records_the_reprocessed_periods() -> None:
    assert SCHEMAS[RUNS]["periods_reprocessed"] == "STRING"
