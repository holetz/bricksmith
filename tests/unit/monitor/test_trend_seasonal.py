"""The seasonal trend: additive seasonal effects around the line, phase-aware history and the policy options."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import math

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.limits import BOTH, GREEN, RED, YELLOW
from bricksmith.monitor.trend import INSUFFICIENT_HISTORY, TrendPolicy, remember, trend_check

# Monthly volume, Jan..Dec repeating: a gentle growth, a 40% December peak and small deterministic noise.
EFFECT = [-6.0, -8.0, -2.0, 0.0, 2.0, 1.0, 0.0, -1.0, 3.0, 4.0, 7.0, 40.0]
NOISE = [0.4, -0.6, 0.3, -0.2, 0.5, -0.4, 0.1, 0.6, -0.5, 0.2, -0.3, 0.4]


def _value(t: int) -> float:
    return 100.0 + 0.5 * t + EFFECT[t % 12] + NOISE[(t * 5 + t // 12 * 3) % 12]


def _series(count: int) -> list[float]:
    return [_value(t) for t in range(count)]


SEASONAL = TrendPolicy(window=24, min_points=6, season=12)
PLAIN = TrendPolicy(window=24, min_points=6)


def test_the_plain_line_flags_the_expected_december_and_the_season_does_not() -> None:
    history = _series(35)  # three years of Jan..Nov: the next period is a December
    december = _value(35)
    plain = trend_check(history, december, PLAIN, BOTH)
    seasonal = trend_check(history, december, SEASONAL, BOTH)
    assert plain.status in (YELLOW, RED) and plain.model == "line"
    assert seasonal.status == GREEN and seasonal.model == "seasonal"
    assert seasonal.expected == pytest.approx(december, abs=2.0)
    assert seasonal.deviation is not None and abs(seasonal.deviation) < 2.0


def test_a_real_december_break_is_red_under_the_season() -> None:
    history = _series(35)
    found = trend_check(history, _value(35) * 0.6, SEASONAL, BOTH)
    assert found.status == RED and found.model == "seasonal"


def test_a_break_in_an_ordinary_month_is_red_too() -> None:
    history = _series(36)  # the next period is a January
    assert trend_check(history, _value(36), SEASONAL, BOTH).status == GREEN
    assert trend_check(history, _value(36) + 25.0, SEASONAL, BOTH).status == RED


def test_a_robust_fit_keeps_the_season() -> None:
    history = _series(35)
    for fit in ("theil_sen", "huber"):
        policy = TrendPolicy(window=24, min_points=6, season=12, fit=fit, scale="mad")
        found = trend_check(history, _value(35), policy, BOTH)
        assert found.model == "seasonal" and found.status == GREEN


def test_a_log_transform_models_a_multiplicative_season() -> None:
    history = [100.0 * (1.2 if t % 12 == 11 else 1.0) * (1.01**t) for t in range(35)]
    policy = TrendPolicy(window=24, min_points=6, season=12, transform="log")
    assert trend_check(history, 100.0 * 1.2 * 1.01**35, policy, BOTH).status == GREEN


def test_fewer_than_two_cycles_fall_back_to_the_plain_line_and_say_so() -> None:
    history = _series(18)
    found = trend_check(history, _value(18), SEASONAL, BOTH)
    assert found.model == "line:insufficient_history" and found.status in (GREEN, RED)
    assert trend_check(history, _value(18), PLAIN, BOTH).model == "line"
    assert trend_check(_series(4), _value(4), SEASONAL, BOTH).status == INSUFFICIENT_HISTORY


def test_a_history_that_left_periods_out_keeps_its_phase() -> None:
    policy = TrendPolicy(window=36, min_points=6, season=12)  # three cycles leave two observations per phase
    history: list[float] = []
    for t in range(47):
        remember(history, _value(t), kept=t not in (20, 21), policy=policy)
    assert len(history) == 47 and math.isnan(history[20])
    found = trend_check(history, _value(47), policy, BOTH)
    assert found.model == "seasonal" and found.status == GREEN
    # two cycles with a left-out period leave one observation in a phase: the plain line, said out loud
    thin = TrendPolicy(window=24, min_points=6, season=12)
    assert trend_check(history[:35], _value(35), thin, BOTH).model == "line:insufficient_history"


def test_remember_drops_a_value_without_a_season_and_marks_it_with_one() -> None:
    plain: list[float] = []
    remember(plain, 1.0, kept=False, policy=PLAIN)
    remember(plain, 2.0, kept=True, policy=PLAIN)
    assert plain == [2.0]
    seasonal: list[float] = []
    remember(seasonal, 1.0, kept=False, policy=SEASONAL)
    assert len(seasonal) == 1 and math.isnan(seasonal[0])


def test_the_season_policy_validates_serializes_and_keeps_the_default_hash() -> None:
    assert TrendPolicy().season == 0 and TrendPolicy().drift == "none" and TrendPolicy().per == "period"
    assert set(TrendPolicy().to_dict()) == {
        "exclude", "fit", "min_abs_change", "min_points", "min_rel_change",
        "red", "scale", "transform", "window", "yellow",
    }  # fmt: skip
    policy = TrendPolicy.from_dict({
        "season": 12, "per": "business_day", "calendar": {"holidays": ["2025-12-25"]}, "drift": "cusum",
        "change_point": True,
    })  # fmt: skip
    assert policy.span == 24 and policy.business_calendar.weekend == (5, 6)
    data = policy.to_dict()
    assert data["season"] == 12 and data["per"] == "business_day" and data["drift"] == "cusum"
    assert data["calendar"]["holidays"] == ["2025-12-25"] and data["change_point"] is True
    assert TrendPolicy.from_dict(data) == policy
    for bad in ({"season": 1}, {"season": -3}, {"per": "week"}, {"drift": "median"}, {"calendar": {"x": 1}}):
        with pytest.raises(MonitorError):
            TrendPolicy.from_dict(bad)
