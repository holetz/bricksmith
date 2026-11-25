"""Pure statistics over counts and lists. Definitions are in the BS-13 specification."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

EPSILON = 1e-4
GAMMA_ITERATIONS = 500
GAMMA_EPSILON = 1e-15


def psi_terms(
    reference: Mapping[str, int],
    current: Mapping[str, int],
    epsilon: float = EPSILON,
    smoothing: float = 0.0,
) -> dict[str, dict[str, float]]:
    """Return per-bin ``{reference, current, contribution}`` after flooring both shares at ``epsilon``.

    A positive ``smoothing`` adds that pseudo-count to every bin of both sides before the shares are taken (Laplace),
    which keeps empty bins of small samples from dominating the index.

    Raises ``ZeroDivisionError`` when either side has no rows; callers report that as an unavailable metric.
    """
    total_r, total_c = sum(reference.values()), sum(current.values())
    if total_r == 0 or total_c == 0:
        raise ZeroDivisionError
    keys = sorted(set(reference) | set(current))
    total_r += smoothing * len(keys)
    total_c += smoothing * len(keys)
    terms: dict[str, dict[str, float]] = {}
    for key in keys:
        p = max((reference.get(key, 0) + smoothing) / total_r, epsilon)
        q = max((current.get(key, 0) + smoothing) / total_c, epsilon)
        terms[key] = {"contribution": (q - p) * math.log(q / p), "current": q, "reference": p}
    return terms


def psi(reference: Mapping[str, int], current: Mapping[str, int], epsilon: float = EPSILON) -> float:
    """Return the population stability index of two bin-count mappings."""
    return sum(t["contribution"] for t in psi_terms(reference, current, epsilon).values())


def mid_ranks(values: Sequence[float]) -> list[float]:
    """Return 1-based ranks where tied values share the mean of their positions."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def auc_rank(scores: Sequence[float], labels: Sequence[int]) -> float:
    """Return the AUC through the rank-sum statistic (ties count one half); needs both classes."""
    ranks = mid_ranks(scores)
    positives = sum(labels)
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        raise ZeroDivisionError
    rank_sum = sum(r for r, y in zip(ranks, labels, strict=True) if y == 1)
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def auc_groups(groups: Mapping[float, tuple[int, int]]) -> float:
    """Return the AUC from ``score -> (positives, negatives)``: pairs won plus half of tied pairs."""
    positives = sum(p for p, _ in groups.values())
    negatives = sum(n for _, n in groups.values())
    if positives == 0 or negatives == 0:
        raise ZeroDivisionError
    below = 0
    wins = 0.0
    for score in sorted(groups):
        pos, neg = groups[score]
        wins += pos * (below + neg / 2)
        below += neg
    return wins / (positives * negatives)


def ks_groups(groups: Mapping[float, tuple[int, int]]) -> float:
    """Return the Kolmogorov-Smirnov distance between positive and negative score distributions."""
    positives = sum(p for p, _ in groups.values())
    negatives = sum(n for _, n in groups.values())
    if positives == 0 or negatives == 0:
        raise ZeroDivisionError
    cum_p = cum_n = 0
    best = 0.0
    for score in sorted(groups):
        cum_p += groups[score][0]
        cum_n += groups[score][1]
        best = max(best, abs(cum_p / positives - cum_n / negatives))
    return best


def groups_from_lists(scores: Sequence[float], labels: Sequence[int]) -> dict[float, tuple[int, int]]:
    """Group labeled scores into ``score -> (positives, negatives)``."""
    groups: dict[float, tuple[int, int]] = {}
    for score, label in zip(scores, labels, strict=True):
        pos, neg = groups.get(score, (0, 0))
        groups[score] = (pos + (label == 1), neg + (label == 0))
    return groups


def calibration_bin(score: float, bins: int) -> int:
    """Return the equal-width calibration bin of a score in [0, 1]."""
    return min(math.floor(score * bins), bins - 1)


def _gamma_series(a: float, x: float) -> float:
    term = total = 1.0 / a
    ap = a
    for _ in range(GAMMA_ITERATIONS):
        ap += 1
        term *= x / ap
        total += term
        if abs(term) < abs(total) * GAMMA_EPSILON:
            break
    return total * math.exp(-x + a * math.log(x) - math.lgamma(a))


def _gamma_fraction(a: float, x: float) -> float:
    tiny = 1e-300
    b = x + 1 - a
    c = 1 / tiny
    d = 1 / b
    h = d
    for i in range(1, GAMMA_ITERATIONS):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < GAMMA_EPSILON:
            break
    return math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def gamma_p(a: float, x: float) -> float:
    """Return the regularized lower incomplete gamma function ``P(a, x)``."""
    if x <= 0:
        return 0.0
    return _gamma_series(a, x) if x < a + 1 else 1.0 - _gamma_fraction(a, x)


def chi2_ppf(p: float, df: int) -> float:
    """Return the ``p`` quantile of the chi-square distribution with ``df`` degrees of freedom (by bisection)."""
    low, high = 0.0, max(1.0, float(df))
    while gamma_p(df / 2, high / 2) < p:
        high *= 2
    for _ in range(200):
        mid = (low + high) / 2
        if gamma_p(df / 2, mid / 2) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def psi_critical(bins: int, n: float, m: float, alpha: float = 0.05) -> float | None:
    """Return the sample-size-aware PSI critical value ``chi2_{bins-1}(1 - alpha) * (1/n + 1/m)``."""
    if bins < 2 or n <= 0 or m <= 0:  # noqa: PLR2004 - one bin has no degrees of freedom
        return None
    return chi2_ppf(1 - alpha, bins - 1) * (1 / n + 1 / m)
