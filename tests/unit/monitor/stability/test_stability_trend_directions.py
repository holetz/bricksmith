"""The trend check runs on directional metrics without limits (row_count, mean, ...) with synthetic series."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.stability import MemoryStore, check, parse_config
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


def _rows(counts: list[int]) -> list[dict[str, object]]:
    return [{"month": f"2025-{m:02d}", "x": i % 3} for m, n in enumerate(counts, 1) for i in range(n)]


def _run(counts: list[int], **overrides: object):  # noqa: ANN202 - test helper
    cfg = _config(**{"min_rows": 0, "baseline": "previous", **overrides})
    found = check(_rows(counts), cfg, MemoryStore(), dataset="d", run_id="r1", now="2026-04-01T00:00:00+00:00")
    return found, {(r["period"], r["column"], r["metric"]): r for r in found.tables["results"]}


STEADY = [30] * 11


def test_trend_runs_on_row_count_without_limits_and_opens_a_trend_alert() -> None:
    found, results = _run([*STEADY, 90], trend_metrics=["row_count"])
    last = results[("2025-12", "", "row_count")]
    assert last["status"] == "no_rule" and last["direction"] == ""
    assert last["trend_status"] == "red" and last["trend_expected"] == pytest.approx(30)
    assert results[("2025-03", "", "row_count")]["trend_status"] == "insufficient_history"
    alerts = [a for a in found.tables["alerts"] if a["metric"] == "row_count"]
    assert [(a["kind"], a["severity"], a["opened_period"]) for a in alerts] == [("trend", "critical", "2025-12")]
    assert not any(a["kind"] == "persistence" and a["metric"] == "row_count" for a in found.tables["alerts"])


def test_trend_direction_is_respected_and_a_limit_direction_wins() -> None:
    _, lower = _run([*STEADY, 90], trend_metrics=["row_count"], trend_directions={"row_count": "lower"})
    assert lower[("2025-12", "", "row_count")]["trend_status"] == "green"
    _, higher = _run([*STEADY, 90], trend_metrics=["row_count"], trend_directions={"row_count": "higher"})
    assert higher[("2025-12", "", "row_count")]["trend_status"] == "red"
    _, limited = _run(
        [*STEADY, 90], trend_metrics=["volume_change"], trend_directions={"volume_change": "lower"},
        baseline={"kind": "rolling", "periods": 3, "min_periods": 1},
    )  # fmt: skip
    assert limited[("2025-12", "", "volume_change")]["direction"] == "both"


def test_row_count_is_not_trended_unless_asked() -> None:
    found, results = _run([*STEADY, 90])
    assert results[("2025-12", "", "row_count")]["trend_status"] == ""
    assert not [a for a in found.tables["alerts"] if a["metric"] == "row_count"]
