"""Pure statistics over counts and lists. Definitions are in the BS-13 specification."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

EPSILON = 1e-4


def psi_terms(
    reference: Mapping[str, int], current: Mapping[str, int], epsilon: float = EPSILON
) -> dict[str, dict[str, float]]:
    """Return per-bin ``{reference, current, contribution}`` after flooring both shares at ``epsilon``.

    Raises ``ZeroDivisionError`` when either side has no rows; callers report that as an unavailable metric.
    """
    total_r, total_c = sum(reference.values()), sum(current.values())
    if total_r == 0 or total_c == 0:
        raise ZeroDivisionError
    terms: dict[str, dict[str, float]] = {}
    for key in sorted(set(reference) | set(current)):
        p = max(reference.get(key, 0) / total_r, epsilon)
        q = max(current.get(key, 0) / total_c, epsilon)
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
