"""Sustained-shift detection over standardized trend deviations: CUSUM, EWMA and change-point search.

A point rule needs many periods to see a small persistent shift. The detectors here read the signed standardized
deviations (one per judged period, in time order) and say whether the level moved: CUSUM (Page; reference value
``k = 0.5``, decision interval ``h = 5``), EWMA (``lambda = 0.2``, an exact-variance limit of ``L = 3`` sigmas) and an
exact penalized least-squares segmentation (the PELT objective without pruning, since a series holds tens of points).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import BOTH, DIRECTIONS, GREEN, HIGHER, LOWER, RED

if TYPE_CHECKING:
    from collections.abc import Sequence

DRIFTS = ("none", "cusum", "ewma")
MIN_SCORES = 3
CUSUM_K = 0.5
CUSUM_H = 5.0
EWMA_LAMBDA = 0.2
EWMA_L = 3.0
MIN_SEGMENT = 2


def _check_direction(direction: str) -> None:
    if direction not in DIRECTIONS:
        msg = f"direction must be one of {', '.join(DIRECTIONS)}"
        raise MonitorError(msg)


def cusum(scores: Sequence[float], direction: str = HIGHER, *, k: float = CUSUM_K, h: float = CUSUM_H) -> bool:
    """Tell whether the Page CUSUM of ``scores`` ends above ``h`` on the side ``direction`` calls worse."""
    _check_direction(direction)
    high = low = 0.0
    for z in scores:
        high = max(0.0, high + z - k)
        low = max(0.0, low - z - k)
    return (direction in (HIGHER, BOTH) and high > h) or (direction in (LOWER, BOTH) and low > h)


def ewma(scores: Sequence[float], direction: str = HIGHER, *, lam: float = EWMA_LAMBDA, width: float = EWMA_L) -> bool:
    """Tell whether the EWMA of ``scores`` ends beyond its exact ``width``-sigma limit on the worse side."""
    _check_direction(direction)
    level = 0.0
    for z in scores:
        level = lam * z + (1.0 - lam) * level
    limit = width * math.sqrt(lam / (2.0 - lam) * (1.0 - (1.0 - lam) ** (2 * len(scores)))) if scores else math.inf
    return (direction in (HIGHER, BOTH) and level > limit) or (direction in (LOWER, BOTH) and level < -limit)


def drift_status(scores: Sequence[float], method: str, direction: str = HIGHER) -> str:
    """Return ``green`` or ``red`` for a detector (``cusum`` or ``ewma``), or ``""`` when there is too little to say."""
    if method not in DRIFTS:
        msg = f"drift must be one of {', '.join(DRIFTS)}"
        raise MonitorError(msg)
    if method == "none" or len(scores) < MIN_SCORES:
        return ""
    return RED if (cusum if method == "cusum" else ewma)(scores, direction) else GREEN


def change_points(scores: Sequence[float], *, penalty: float | None = None, min_size: int = MIN_SEGMENT) -> list[int]:
    """Return the indexes where a new level starts, by the exact penalized least-squares segmentation.

    The default penalty is the BIC ``2 ln(n)`` for unit-variance scores. Every segment holds ``min_size`` points.
    """
    n = len(scores)
    beta = 2.0 * math.log(max(n, 2)) if penalty is None else penalty
    if n < 2 * min_size:
        return []
    total, squares = [0.0], [0.0]
    for z in scores:
        total.append(total[-1] + z)
        squares.append(squares[-1] + z * z)

    def cost(a: int, b: int) -> float:
        return squares[b] - squares[a] - (total[b] - total[a]) ** 2 / (b - a)

    best = {0: -beta}
    back: dict[int, int] = {}
    for t in range(min_size, n + 1):
        starts = [s for s in best if t - s >= min_size]
        if not starts:
            continue
        value, start = min((best[s] + cost(s, t) + beta, s) for s in starts)
        best[t], back[t] = value, start
    found: list[int] = []
    t = n
    while t in back and back[t] > 0:
        t = back[t]
        found.append(t)
    return sorted(found)


def change_point_suggested(scores: Sequence[float], direction: str = HIGHER) -> bool:
    """Tell whether the latest change point moves the level of ``scores`` in the direction called worse."""
    _check_direction(direction)
    found = change_points(scores)
    if not found:
        return False
    latest = found[-1]
    before = found[-2] if len(found) > 1 else 0
    move = sum(scores[latest:]) / (len(scores) - latest) - sum(scores[before:latest]) / (latest - before)
    return {HIGHER: move > 0, LOWER: move < 0, BOTH: move != 0}[direction]


__all__ = [
    "DRIFTS",
    "change_point_suggested",
    "change_points",
    "cusum",
    "drift_status",
    "ewma",
]
