"""Shape metrics from stored bins: the declaration, vanished categories, quantile shifts and distribution distances."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import Limits
from bricksmith.monitor.stability.config import (
    METRICS,
    SHAPE_LIMITS,
    SHAPE_METRICS,
    parse_config,
)


def test_config_shape_defaults_leave_serialized_forms_unchanged() -> None:
    plain = parse_config({"period": "month"})
    for metric in (*SHAPE_METRICS, "duplicate_key_rate", "distinct_count", "distinct_change"):
        assert metric in METRICS and metric not in plain.to_dict()["limits"]
    for metric, limits in SHAPE_LIMITS.items():
        assert plain.limits_for(metric, "x") == limits
    assert plain.limits_for("distinct_count", "x") is None
    assert "keys" not in plain.to_dict() and "distinct" not in plain.to_dict()


def test_config_shape_limits_are_configurable_and_can_be_disabled() -> None:
    custom = parse_config({"period": "month", "limits": {"vanished_rate": {"green": 0.1, "red": 0.3}}})
    assert custom.limits_for("vanished_rate", "x") == Limits(0.1, 0.3, "higher")
    assert parse_config({"period": "month", "limits": {"wasserstein": None}}).limits_for("wasserstein", "x") is None


def test_config_keys_and_distinct_are_validated_and_serialized() -> None:
    config = parse_config({"period": "month", "keys": ["id", "line"], "distinct": ["customer", "customer"]})
    assert config.keys == ("id", "line") and config.distinct == ("customer",)
    assert config.to_dict()["keys"] == ["id", "line"] and config.to_dict()["distinct"] == ["customer"]
    for bad in ("id", [1], [""]):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", "keys": bad})
    with pytest.raises(MonitorError):
        parse_config({"period": "month", "distinct": "id"})
