"""Credit statistics: incomplete beta, calibration tests per grade, horizon rescaling and inversions."""

from __future__ import annotations

import math

import pytest

from bricksmith.monitor.credit import (
    adjusted_binomial_p,
    binomial_p,
    incomplete_beta,
    inversions,
    jeffreys_p,
    rescale,
)


def _beta_cdf_by_integration(x: float, a: float, b: float, steps: int = 20000) -> float:
    """Integrate the Beta(a, b) density from 0 to ``x`` with Simpson's rule (a, b >= 1, so the density is finite)."""
    norm = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b))
    h = x / steps
    total = 0.0
    for i in range(steps + 1):
        t = i * h
        weight = 1 if i in (0, steps) else 4 if i % 2 else 2
        total += weight * (t ** (a - 1) * (1 - t) ** (b - 1) if 0 < t < 1 else (1.0 if a == 1 and t == 0 else 0.0))
    return norm * total * h / 3


def _binomial_tail(n: int, d: int, p: float) -> float:
    return sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(d, n + 1))


@pytest.mark.parametrize("x", [0.0, 0.1, 0.37, 0.5, 0.9, 1.0])
def test_incomplete_beta_of_uniform_is_identity(x: float) -> None:
    assert incomplete_beta(x, 1, 1) == pytest.approx(x, abs=1e-14)


@pytest.mark.parametrize(("x", "a", "b"), [(0.2, 2.5, 7.5), (0.8, 30.5, 3.5), (0.01, 0.5, 200.5), (0.6, 4, 4)])
def test_incomplete_beta_symmetry(x: float, a: float, b: float) -> None:
    assert incomplete_beta(x, a, b) == pytest.approx(1 - incomplete_beta(1 - x, b, a), abs=1e-13)


@pytest.mark.parametrize(("x", "a", "b"), [(0.3, 2.5, 7.5), (0.05, 3.5, 40.5), (0.7, 6, 2)])
def test_incomplete_beta_matches_numerical_integration(x: float, a: float, b: float) -> None:
    assert incomplete_beta(x, a, b) == pytest.approx(_beta_cdf_by_integration(x, a, b), abs=1e-9)


def test_incomplete_beta_refuses_non_positive_shapes() -> None:
    with pytest.raises(ValueError, match="a > 0"):
        incomplete_beta(0.5, 0, 1)


@pytest.mark.parametrize(("n", "d", "pd"), [(10, 0, 0.05), (10, 3, 0.1), (40, 7, 0.08), (25, 25, 0.6), (60, 1, 0.001)])
def test_binomial_p_equals_the_direct_tail(n: int, d: int, pd: float) -> None:
    assert binomial_p(n, d, pd) == pytest.approx(_binomial_tail(n, d, pd), rel=1e-10, abs=1e-15)


def test_jeffreys_p_is_the_jeffreys_posterior_cdf() -> None:
    assert jeffreys_p(40, 3, 0.05) == pytest.approx(_beta_cdf_by_integration(0.05, 3.5, 37.5), abs=1e-9)


def test_p_values_fall_as_observed_events_exceed_the_prediction() -> None:
    jeffreys = [jeffreys_p(1000, d, 0.02) for d in (10, 20, 30, 40)]
    binomial = [binomial_p(1000, d, 0.02) for d in (10, 20, 30, 40)]
    for values in (jeffreys, binomial):
        assert values == sorted(values, reverse=True)
        assert values[-1] < 0.01
        assert values[0] > 0.5


def test_tests_refuse_impossible_grades() -> None:
    with pytest.raises(ValueError, match="0 <= d <= n"):
        jeffreys_p(10, 11, 0.1)
    with pytest.raises(ValueError, match="pd <= 1"):
        binomial_p(10, 1, 1.5)


def test_adjusted_binomial_is_more_lenient_with_correlation() -> None:
    plain = binomial_p(1000, 40, 0.02)
    adjusted = adjusted_binomial_p(1000, 40, 0.02, 0.12)
    weak = adjusted_binomial_p(1000, 40, 0.02, 0.001)
    assert plain < adjusted < 0.5
    assert weak < adjusted
    assert adjusted_binomial_p(1000, 10, 0.02, 0.12) > 0.5  # fewer events than predicted is not significant


def test_adjusted_binomial_edges() -> None:
    assert adjusted_binomial_p(100, 0, 0.02, 0.1) == 1.0
    assert adjusted_binomial_p(100, 100, 0.02, 0.1) == 0.0
    assert adjusted_binomial_p(100, 3, 0.0, 0.1) == 0.0
    with pytest.raises(ValueError, match="correlation"):
        adjusted_binomial_p(100, 3, 0.02, 0.0)


def test_rescale_spreads_a_constant_hazard_over_the_horizon() -> None:
    assert rescale(0.2, 12, 12) == 0.2
    quarter = rescale(0.2, 3, 12)
    assert quarter == pytest.approx(1 - 0.8**0.25)
    assert 1 - (1 - quarter) ** 4 == pytest.approx(0.2)
    assert rescale(1.2, 3, 12) == 1.0
    assert rescale(-0.1, 3, 12) == 0.0
    with pytest.raises(ValueError, match="positive"):
        rescale(0.1, 0, 12)


def test_inversions_count_strict_falls_only() -> None:
    assert inversions([0.01, 0.02, 0.05, 0.2]) == 0
    assert inversions([0.01, 0.03, 0.02, 0.2]) == 1
    assert inversions([0.05, 0.05, 0.04, 0.01]) == 2
    assert inversions([0.1]) == 0
    assert inversions([]) == 0
