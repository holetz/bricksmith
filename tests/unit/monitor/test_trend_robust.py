"""Robust trend fits (Theil-Sen, Huber), the MAD scale and the exclusion rule, on fixed synthetic series."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.limits import GREEN, RED, YELLOW
from bricksmith.monitor.trend import TrendPolicy, keeps, trend_check

# One outlier (60.0) in an otherwise linear series; reference values from an independent hand implementation.
SERIES = [10.1, 11.9, 14.2, 15.8, 18.1, 60.0, 22.0, 24.1]
CLEAN = [10.1, 11.9, 14.2, 15.8, 18.1, 19.9, 22.0, 24.1]


def _policy(fit: str, **extra: object) -> TrendPolicy:
    return TrendPolicy(window=12, min_points=6, fit=fit, **extra)  # type: ignore[arg-type]


def test_theil_sen_matches_the_reference_values() -> None:
    found = trend_check(SERIES, 26.0, _policy("theil_sen"))
    assert found.slope == pytest.approx(2.01, abs=1e-9)
    assert found.expected == pytest.approx(26.125, abs=1e-9)


def test_huber_matches_the_reference_values() -> None:
    found = trend_check(SERIES, 26.0, _policy("huber"))
    assert found.slope == pytest.approx(2.0140541, abs=1e-6)
    assert found.expected == pytest.approx(26.1322525, abs=1e-6)


def test_ols_is_pulled_by_the_outlier_and_the_robust_fits_are_not() -> None:
    ols = trend_check(SERIES, 26.0, _policy("ols"))
    assert ols.expected is not None and ols.expected > 35
    for fit in ("theil_sen", "huber"):
        found = trend_check(SERIES, 26.0, _policy(fit))
        assert found.expected is not None and abs(found.expected - 26.1) < 0.1


def test_robust_fits_agree_with_ols_on_exact_lines() -> None:
    line = [3.0 + 2.0 * i for i in range(8)]
    for fit in ("theil_sen", "huber"):
        found = trend_check(line, 19.0, _policy(fit))
        assert found.expected == pytest.approx(19.0) and found.slope == pytest.approx(2.0)
        assert found.status == GREEN


def test_a_robust_fit_catches_a_jump_the_ols_fit_misses() -> None:
    history = [30.0, 30.4, 29.8, 30.2, 30.1, 90.0, 29.9, 30.3]
    assert trend_check(history, 60.0, _policy("ols")).status == GREEN
    assert trend_check(history, 60.0, _policy("theil_sen", scale="mad")).status == RED


def test_mad_scale_is_robust_and_std_is_the_default() -> None:
    std = trend_check(SERIES, 26.0, _policy("theil_sen"))
    mad = trend_check(SERIES, 26.0, _policy("theil_sen", scale="mad"))
    assert std.sigma is not None and mad.sigma is not None
    assert mad.sigma < std.sigma / 5
    assert TrendPolicy().scale == "std" and TrendPolicy().fit == "ols"


def test_policy_validates_and_round_trips_the_new_keys() -> None:
    for bad in ({"fit": "lasso"}, {"scale": "iqr"}, {"exclude": "yellow"}):
        with pytest.raises(MonitorError):
            TrendPolicy.from_dict(bad)
    policy = TrendPolicy.from_dict({"fit": "huber", "scale": "mad", "exclude": "flagged"})
    assert TrendPolicy.from_dict(policy.to_dict()) == policy
    assert TrendPolicy().exclude == "red"


def test_keeps_follows_the_exclude_mode() -> None:
    assert not keeps(TrendPolicy(exclude="red"), RED, GREEN)
    assert not keeps(TrendPolicy(exclude="red"), GREEN, RED)
    assert keeps(TrendPolicy(exclude="red"), YELLOW, YELLOW)
    assert not keeps(TrendPolicy(exclude="flagged"), YELLOW, GREEN)
    assert keeps(TrendPolicy(exclude="none"), RED, RED)
