"""Schema drift: the declaration, the schema results and alerts, extend refits and missing columns."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import Limits
from bricksmith.monitor.stability.config import (
    METRICS,
    SCHEMA_LIMITS,
    SCHEMA_METRICS,
    parse_config,
)
from bricksmith.monitor.stability.tables import BINNINGS, RUNS, SCHEMAS


def test_config_schema_defaults_leave_serialized_forms_unchanged() -> None:
    plain = parse_config({"period": "month"})
    extended = parse_config({"period": "month", "auto_columns": "extend"})
    assert plain.auto_columns == "freeze" and "auto_columns" not in plain.to_dict()
    assert extended.to_dict()["auto_columns"] == "extend"
    assert plain.binning_fingerprint() == extended.binning_fingerprint()
    for metric in SCHEMA_METRICS:
        assert metric in METRICS and metric not in plain.to_dict()["limits"]
        assert plain.limits_for(metric, "x") == SCHEMA_LIMITS[metric]


def test_config_schema_limits_are_configurable() -> None:
    custom = parse_config({"period": "month", "limits": {"schema_change": {"green": 1, "red": 4}}})
    assert custom.limits_for("schema_change", "") == Limits(1.0, 4.0, "higher")
    off = parse_config({"period": "month", "limits": {"missing_column": None}})
    assert off.limits_for("missing_column", "x") is None


def test_config_auto_columns_is_validated() -> None:
    for bad in ("grow", True, 1):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", "auto_columns": bad})
    with pytest.raises(MonitorError, match="columns: auto"):
        parse_config({"period": "month", "columns": ["a"], "auto_columns": "extend"})
    assert parse_config({"period": "month", "columns": ["a"], "auto_columns": "freeze"}).auto_columns == "freeze"


def test_binnings_and_runs_tables_carry_the_schema() -> None:
    assert "schema" in SCHEMAS[BINNINGS] and "schema" in SCHEMAS[RUNS]
