"""The trend check as a prediction interval: Student-t quantiles, the practical-significance floor, the transform."""

from __future__ import annotations

import math
import random

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.limits import BOTH, GREEN, RED
from bricksmith.monitor.trend import INSUFFICIENT_HISTORY, TrendPolicy, critical_value, t_cdf, t_ppf, trend_check


def test_student_t_quantiles_match_published_tables() -> None:
    assert t_ppf(0.975, 4) == pytest.approx(2.7764451, abs=1e-6)
    assert t_ppf(0.975, 10) == pytest.approx(2.2281389, abs=1e-6)
    assert t_ppf(0.995, 1) == pytest.approx(63.6567412, abs=1e-5)
    assert t_ppf(0.1, 2) == pytest.approx(-1.8856181, abs=1e-6)
    assert t_cdf(0.0, 5) == pytest.approx(0.5)
    assert critical_value(2.0, 10_000_000) == pytest.approx(2.0, abs=1e-6)
    with pytest.raises(MonitorError):
        t_ppf(1.0, 3)


def _rates(k: int, runs: int = 6000) -> tuple[float, float]:
    rng = random.Random(20261125 + k)  # noqa: S311 - a seeded simulation, not a secret
    policy = TrendPolicy(window=k, min_points=k)
    yellow = red = 0
    for _ in range(runs):
        history = [rng.gauss(0.0, 1.0) for _ in range(k)]
        found = trend_check(history, rng.gauss(0.0, 1.0), policy, BOTH)
        yellow += found.status != GREEN
        red += found.status == RED
    return yellow / runs, red / runs


@pytest.mark.parametrize("k", [6, 12])
def test_pure_noise_flags_at_the_nominal_rates(k: int) -> None:
    yellow, red = _rates(k)
    assert yellow == pytest.approx(0.0455, abs=0.012)  # was 12% (k = 12) and 22% (k = 6) with the normal cut-offs
    assert red == pytest.approx(0.0027, abs=0.003)  # was 2.9% and 9.5%


def test_flat_history_and_a_one_row_change_is_green() -> None:
    assert trend_check([1000.0] * 8, 1001.0).status == GREEN
    assert trend_check([1000.0] * 8, 1100.0).status == RED
    assert trend_check([1000.0] * 8, 1001.0, TrendPolicy(min_rel_change=0.0)).status == RED


def test_absolute_floor_and_validation() -> None:
    assert trend_check([0.0] * 6, 0.4, TrendPolicy(min_abs_change=0.5)).status == GREEN
    assert trend_check([0.0] * 6, 0.6, TrendPolicy(min_abs_change=0.5)).status == RED
    for bad in ({"min_abs_change": -1}, {"min_rel_change": -0.1}, {"transform": "sqrt"}, {"bogus": 1}):
        with pytest.raises(MonitorError):
            TrendPolicy.from_dict(bad)
    policy = TrendPolicy.from_dict({"transform": "log", "min_abs_change": 2})
    assert TrendPolicy.from_dict(policy.to_dict()) == policy


def test_log_transform_makes_exponential_growth_a_line() -> None:
    growth = [100.0 * 1.2**i for i in range(10)]
    nxt = 100.0 * 1.2**10
    assert trend_check(growth, nxt, TrendPolicy(transform="log", min_rel_change=0.0)).status == GREEN
    plain = trend_check(growth, nxt, TrendPolicy(min_rel_change=0.0))
    assert plain.status != GREEN
    logged = trend_check(growth, nxt, TrendPolicy(transform="log"))
    assert logged.expected == pytest.approx(nxt, rel=0.01)
    assert logged.lower is not None
    assert logged.upper is not None
    assert logged.lower <= nxt <= logged.upper
    negative = trend_check([-2.0, 1.0, 2.0, 3.0, 4.0, 5.0], 6.0, TrendPolicy(transform="log"))
    assert negative.status == INSUFFICIENT_HISTORY


def test_band_is_symmetric_around_the_expected_value() -> None:
    found = trend_check([1.0, 1.2, 0.8, 1.0, 1.2, 0.8], 1.0)
    assert found.expected is not None
    assert found.lower is not None
    assert found.upper is not None
    assert found.upper - found.expected == pytest.approx(found.expected - found.lower)
    assert found.sigma is not None
    assert found.sigma > 0
    assert math.isfinite(found.sigma)
