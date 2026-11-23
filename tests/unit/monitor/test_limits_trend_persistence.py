"""Hand-derived checks of the shared core: traffic-light limits, the trend check and alert persistence."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import math

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.limits import (
    GREEN,
    INSUFFICIENT_VOLUME,
    NO_BASELINE,
    NO_DATA,
    NO_RULE,
    RED,
    YELLOW,
    Limits,
    classify,
    rank,
    worst,
)
from bricksmith.monitor.persistence import CRITICAL, WARNING, PersistencePolicy, alert_spans
from bricksmith.monitor.trend import INSUFFICIENT_HISTORY, TrendPolicy, trend_check


def test_bands_are_inclusive_and_directional() -> None:
    higher = Limits(0.1, 0.25)
    assert [higher.band(v) for v in (0.0, 0.1, 0.1000001, 0.25, 0.26, math.inf)] == [
        GREEN, GREEN, YELLOW, YELLOW, RED, RED,
    ]  # fmt: skip
    lower = Limits(0.9, 0.7, "lower")
    assert [lower.band(v) for v in (1.0, 0.9, 0.8, 0.7, 0.69)] == [GREEN, GREEN, YELLOW, YELLOW, RED]
    both = Limits(0.2, 0.5, "both", center=1.0)
    assert [both.band(v) for v in (1.0, 0.8, 1.2, 1.5, 0.4, 1.6)] == [GREEN, GREEN, GREEN, YELLOW, RED, RED]


def test_limits_validation_and_round_trip() -> None:
    for bad in ((0.3, 0.1, "higher"), (0.1, 0.3, "lower"), (-0.1, 0.2, "both"), (0.1, 0.2, "sideways")):
        with pytest.raises(MonitorError):
            Limits(*bad)
    with pytest.raises(MonitorError, match="green and red"):
        Limits.from_dict({"green": 0.1})
    with pytest.raises(MonitorError, match="numbers"):
        Limits.from_dict({"green": "x", "red": 1})
    limits = Limits.from_dict({"green": 0.2, "red": 0.5, "direction": "both"})
    assert Limits.from_dict(limits.to_dict()) == limits


def test_suppressed_states_win_in_order() -> None:
    limits = Limits(0.1, 0.25)
    assert classify(None, limits).status == NO_DATA
    assert classify(math.nan, limits).status == NO_DATA
    assert classify(0.5, limits, count=3, min_count=10).status == INSUFFICIENT_VOLUME
    assert classify(0.5, limits, has_baseline=False).status == NO_BASELINE
    assert classify(0.5, None).status == NO_RULE
    light = classify(0.5, limits, count=10, min_count=10)
    assert light.status == RED and light.rank == 2 and not light.suppressed
    assert classify(0.5, None).rank == -1 and classify(0.5, None).suppressed
    assert rank(GREEN) == 0 and rank(NO_DATA) == -1
    assert worst([GREEN, NO_RULE, YELLOW]) == YELLOW and worst([NO_DATA]) == ""


def test_trend_on_a_line_and_a_jump() -> None:
    line = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    exact = trend_check(line, 7.0)
    assert exact.status == GREEN and exact.expected == pytest.approx(7.0) and exact.deviation == 0.0
    assert exact.slope == pytest.approx(1.0) and exact.points == 6
    noisy = [1.0, 1.2, 0.8, 1.0, 1.2, 0.8]
    result = trend_check(noisy, 5.0)
    assert result.status == RED and result.deviation is not None and result.deviation > 3
    assert trend_check(noisy, -5.0).status == GREEN  # higher is worse: a drop does not count
    assert trend_check(noisy, -5.0, direction="lower").status == RED
    assert trend_check(noisy, -5.0, direction="both").status == RED


def test_trend_hand_derived_sigma() -> None:
    # ys = [0, 2, 0, 2]: x_mean 1.5, y_mean 1, sxx 5, sxy = (-1.5)(-1)+(-0.5)(1)+(0.5)(-1)+(1.5)(1) = 2 -> slope 0.4,
    # intercept 0.4; fitted 0.4, 0.8, 1.2, 1.6; residuals -0.4, 1.2, -1.2, 0.4; SSE 3.2; sigma = sqrt(3.2 / 2).
    policy = TrendPolicy(window=4, min_points=4)
    result = trend_check([0.0, 2.0, 0.0, 2.0], 2.0 + 2.5 * math.sqrt(1.6), policy)
    assert result.expected == pytest.approx(2.0) and result.deviation == pytest.approx(2.5)
    assert result.status == YELLOW


def test_trend_edge_cases() -> None:
    assert trend_check([1.0] * 5, 1.0).status == INSUFFICIENT_HISTORY
    flat = [0.1] * 6
    assert trend_check(flat, 0.1).status == GREEN
    assert trend_check(flat, 0.2).status == RED and trend_check(flat, 0.0).status == GREEN
    assert trend_check([*([0.0] * 20), *([1.0] * 6)], 1.0).points == 12  # only the window counts
    with pytest.raises(MonitorError):
        TrendPolicy(window=4, min_points=6)
    with pytest.raises(MonitorError):
        TrendPolicy(yellow=3, red=2)
    assert TrendPolicy.from_dict({"window": 8}).window == 8
    with pytest.raises(MonitorError):
        TrendPolicy.from_dict({"size": 3})


def series(statuses: str) -> list[tuple[str, str]]:
    names = {"g": GREEN, "y": YELLOW, "r": RED, "-": NO_DATA}
    return [(f"2026-{i + 1:02d}", names[s]) for i, s in enumerate(statuses)]


def test_persistence_opens_escalates_and_closes() -> None:
    spans = alert_spans(series("gyyyrrggg"))
    assert len(spans) == 1
    span = spans[0]
    assert span.opened == "2026-04" and span.severity == CRITICAL and span.escalated == "2026-06"
    assert span.closed == "2026-09" and span.state == "closed" and span.flagged == ["2026-04", "2026-05", "2026-06"]


def test_persistence_red_opens_critical_and_never_downgrades() -> None:
    spans = alert_spans(series("rryy"))
    assert spans[0].opened == "2026-02" and spans[0].severity == CRITICAL and spans[0].state == "open"
    assert spans[0].last_status == YELLOW and spans[0].escalated == ""
    immediate = alert_spans(series("gr"), PersistencePolicy(immediate_red=True))
    assert immediate[0].opened == "2026-02" and immediate[0].severity == CRITICAL


def test_persistence_skips_suppressed_and_flags_recurrence() -> None:
    # Suppressed periods neither count nor break the run: y - y - y opens on the third yellow.
    assert alert_spans(series("y-y-y"))[0].opened == "2026-05"
    # Greens in between break it.
    assert alert_spans(series("yygyy")) == []
    spans = alert_spans(series("yyyggg" + "yyy"))
    assert [s.severity for s in spans] == [WARNING, WARNING] and spans[1].recurrence and not spans[0].recurrence
    far = alert_spans(series("yyyggg" + "g" * 12 + "yyy"))
    assert not far[1].recurrence
    # Unsorted input is replayed in period order.
    assert alert_spans(list(reversed(series("rr"))))[0].opened == "2026-02"


def test_persistence_policy_validation() -> None:
    assert alert_spans(series("yyyyyy"), PersistencePolicy(yellow=0)) == []
    with pytest.raises(MonitorError):
        PersistencePolicy(close_after=0)
    with pytest.raises(MonitorError):
        PersistencePolicy(red=-1)
    policy = PersistencePolicy.from_dict({"yellow": 2, "immediate_red": True})
    assert PersistencePolicy.from_dict(policy.to_dict()) == policy
    with pytest.raises(MonitorError):
        PersistencePolicy.from_dict({"amber": 2})
