"""The trend check runs on directional metrics without limits (row_count, mean, ...) with synthetic series."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.stability import parse_config
from bricksmith.monitor.stability.engine import judgment_fingerprint


def _config(**overrides: object):  # noqa: ANN202 - test helper
    return parse_config({"period": "month", "columns": {"x": {"edges": [0, 1.5, 3]}}, **overrides})


def test_declaration_defaults_and_overrides() -> None:
    plain = _config()
    assert "trend_directions" not in plain.to_dict()
    assert plain.trend_direction("row_count") == "both" and plain.trend_direction("null_rate") == "higher"
    assert plain.trend_direction("psi") == ""
    declared = _config(trend_metrics=["row_count"], trend_directions={"row_count": "higher"})
    assert declared.trend_direction("row_count") == "higher"
    assert declared.to_dict()["trend_directions"] == {"row_count": "higher"}
    assert judgment_fingerprint(declared) != judgment_fingerprint(_config(trend_metrics=["row_count"]))
    assert judgment_fingerprint(plain) == judgment_fingerprint(_config())


def test_declaration_refuses_unknown_metric_direction_and_directionless_trend() -> None:
    with pytest.raises(MonitorError, match="unknown metric 'ks'"):
        _config(trend_directions={"ks": "both"})
    with pytest.raises(MonitorError, match="must be one of higher, lower, both"):
        _config(trend_directions={"row_count": "up"})
    with pytest.raises(MonitorError, match="trend_directions must map"):
        _config(trend_directions=["row_count"])
    with pytest.raises(MonitorError, match="psi have no direction"):
        _config(trend_metrics=["psi"], limits={"psi": None})
    assert _config(trend_metrics=["psi"], limits={"psi": None}, trend_directions={"psi": "higher"})
    assert _config(trend_metrics=["row_count", "mean", "null_rate", "unseen_rate"])
