"""Credit-model statistics over counts: calibration tests per grade, horizon rescaling and grade inversions.

Standard library only. A grade has ``n`` rows, ``d`` observed events and a predicted probability ``pd`` (the mean
probability of its rows). The tests are one-sided against underestimation: a small p-value says the grade has
significantly more events than its prediction.
"""

from __future__ import annotations

import itertools
import math
from statistics import NormalDist
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_ITERATIONS = 300
_TOLERANCE = 3e-15
_TINY = 1e-300
_NORMAL = NormalDist()


def _continued_fraction(a: float, b: float, x: float) -> float:
    """Evaluate the continued fraction of the incomplete beta function (modified Lentz)."""
    c, d = 1.0, 1.0 - (a + b) * x / (a + 1.0)
    d = 1.0 / (d if abs(d) > _TINY else _TINY)
    result = d
    for m in range(1, _ITERATIONS + 1):
        for numerator in (
            m * (b - m) * x / ((a + 2 * m - 1) * (a + 2 * m)),
            -(a + m) * (a + b + m) * x / ((a + 2 * m) * (a + 2 * m + 1)),
        ):
            d = 1.0 + numerator * d
            d = 1.0 / (d if abs(d) > _TINY else _TINY)
            c = 1.0 + numerator / c
            c = c if abs(c) > _TINY else _TINY
            result *= d * c
        if abs(d * c - 1.0) < _TOLERANCE:
            break
    return result


def incomplete_beta(x: float, a: float, b: float) -> float:
    """Return the regularized incomplete beta function ``I_x(a, b)`` (the Beta(a, b) CDF at ``x``)."""
    if a <= 0 or b <= 0:
        msg = f"incomplete_beta needs a > 0 and b > 0, got a={a}, b={b}"
        raise ValueError(msg)
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    if x < (a + 1) / (a + b + 2):
        return math.exp(log_front) * _continued_fraction(a, b, x) / a
    return 1.0 - math.exp(log_front) * _continued_fraction(b, a, 1.0 - x) / b


def _check(n: int, d: int, pd: float) -> None:
    if n < 1 or not 0 <= d <= n or not 0 <= pd <= 1:
        msg = f"a grade needs n >= 1, 0 <= d <= n and 0 <= pd <= 1, got n={n}, d={d}, pd={pd}"
        raise ValueError(msg)


def jeffreys_p(n: int, d: int, pd: float) -> float:
    """Return the one-sided Jeffreys p-value: the Beta(d + 0.5, n - d + 0.5) CDF at ``pd``."""
    _check(n, d, pd)
    return incomplete_beta(pd, d + 0.5, n - d + 0.5)


def binomial_p(n: int, d: int, pd: float) -> float:
    """Return the exact one-sided binomial p-value ``P(X >= d)`` for ``X ~ Binomial(n, pd)``."""
    _check(n, d, pd)
    if d == 0:
        return 1.0
    return incomplete_beta(pd, d, n - d + 1)


def adjusted_binomial_p(n: int, d: int, pd: float, correlation: float) -> float:
    """Return the one-factor (Vasicek) p-value of the observed rate ``d / n`` under asset ``correlation``.

    Large-portfolio approximation, ``r`` being the correlation:
    ``P(rate >= d/n) = 1 - Φ((√(1-r)·Φ⁻¹(d/n) - Φ⁻¹(pd)) / √r)``. An observed rate of zero gives 1, a rate of one
    (with ``pd < 1``) gives 0; ``pd`` of 0 or 1 is decided by the observed rate alone.
    """
    _check(n, d, pd)
    if not 0 < correlation < 1:
        msg = f"correlation must be strictly between 0 and 1, got {correlation}"
        raise ValueError(msg)
    rate = d / n
    if rate == 0:
        return 1.0
    if pd <= 0:
        return 0.0
    if rate >= 1 or pd >= 1:
        return 1.0 if pd >= 1 else 0.0
    z = (math.sqrt(1 - correlation) * _NORMAL.inv_cdf(rate) - _NORMAL.inv_cdf(pd)) / math.sqrt(correlation)
    return 1.0 - _NORMAL.cdf(z)


def rescale(probability: float, periods: float, horizon: float) -> float:
    """Return the probability over ``periods`` of an event whose probability over ``horizon`` is ``probability``.

    ``1 - (1 - p)^(periods / horizon)``: a constant hazard spread over the horizon.
    """
    if periods <= 0 or horizon <= 0:
        msg = f"periods and horizon must be positive, got {periods} and {horizon}"
        raise ValueError(msg)
    p = min(max(probability, 0.0), 1.0)
    if periods == horizon:
        return p
    return 1.0 - (1.0 - p) ** (periods / horizon)


def inversions(rates: Sequence[float]) -> int:
    """Return how many consecutive pairs fall: ``rates`` go from the best grade to the worst, so they should rise."""
    return sum(1 for lower, upper in itertools.pairwise(rates) if upper < lower)


__all__ = [
    "adjusted_binomial_p",
    "binomial_p",
    "incomplete_beta",
    "inversions",
    "jeffreys_p",
    "rescale",
]
