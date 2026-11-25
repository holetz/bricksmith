"""Distribution shape metrics computed from stored bin counts alone: no scan of the table.

A bin map is ``bin id -> (count, weight, value sum, value sum of squares)`` like the stored profile slots; only
the counts are read. Categorical columns get the vanished rate and the Jensen-Shannon distance, numeric columns the
quantile shifts and an approximate Wasserstein distance. Numeric positions are approximations: a bin is a point at
its midpoint for the distance and a linear interpolation between its edges for a quantile; the outer bins sit at
the edge of the fitted range and the zero bin at 0.
"""

from __future__ import annotations

import itertools
import math
from typing import TYPE_CHECKING

from bricksmith.monitor.bins import ABOVE, BELOW, NULL, UNSEEN, ZERO

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.monitor.bins import BinSpec

type Bins = Mapping[str, Sequence[float]]
type Segment = tuple[float, float, float]  # low, high, count
QUANTILES: dict[str, float] = {"p05_shift": 0.05, "p50_shift": 0.50, "p95_shift": 0.95}


def _count(bins: Bins, bin_id: str) -> float:
    slot = bins.get(bin_id)
    return float(slot[0]) if slot else 0.0


def vanished_rate(baseline: Bins, current: Bins) -> float | None:
    """Return the share of the baseline rows in categories the period no longer has (``None`` without a baseline)."""
    total = sum(float(s[0]) for s in baseline.values())
    if total <= 0:
        return None
    gone = sum(
        float(s[0]) for b, s in baseline.items() if b.startswith("cat:") and _count(current, b) <= 0 and s[0] > 0
    )
    return gone / total


def js_distance(baseline: Bins, current: Bins) -> float | None:
    """Return the Jensen-Shannon distance (square root of the base-2 divergence, 0..1) of two bin share vectors."""
    base_total = sum(float(s[0]) for s in baseline.values())
    cur_total = sum(float(s[0]) for s in current.values())
    if base_total <= 0 or cur_total <= 0:
        return None
    divergence = 0.0
    for bin_id in {*baseline, *current}:
        p = _count(baseline, bin_id) / base_total
        q = _count(current, bin_id) / cur_total
        mid = (p + q) / 2
        for share in (p, q):
            if share > 0:
                divergence += share * math.log2(share / mid) / 2
    return math.sqrt(max(divergence, 0.0))


def _segments(spec: BinSpec, bins: Bins) -> list[Segment]:
    """Return the non-empty numeric bins as ``(low, high, count)`` in value order (null bins are left out)."""
    edges = spec.edges
    found: list[Segment] = []
    for bin_id in spec.bin_ids():
        count = _count(bins, bin_id)
        if bin_id in (NULL, UNSEEN) or count <= 0:
            continue
        if bin_id == BELOW:
            low = high = edges[0]
        elif bin_id == ABOVE:
            low = high = edges[-1]
        elif bin_id == ZERO:
            low = high = 0.0
        else:
            i = int(bin_id.split("_")[1])
            low, high = edges[i], edges[i + 1]
        found.append((low, high, count))
    return sorted(found, key=lambda s: (s[0] + s[1]) / 2)


def quantile(spec: BinSpec, bins: Bins, q: float) -> float | None:
    """Return the ``q`` quantile (0..1) of a numeric column interpolated from its bin counts (``None`` when empty)."""
    segments = _segments(spec, bins)
    total = sum(s[2] for s in segments)
    if total <= 0:
        return None
    target = q * total
    seen = 0.0
    for low, high, count in segments:
        if seen + count >= target:
            return low + (high - low) * ((target - seen) / count)
        seen += count
    return segments[-1][1]


def quantile_shift(
    spec: BinSpec, baseline: Bins, current: Bins, q: float, std: float | None
) -> tuple[float | None, str]:
    """Return ``(q_now - q_baseline) / baseline std`` of a numeric column and the reason when there is none."""
    now, before = quantile(spec, current, q), quantile(spec, baseline, q)
    if now is None or before is None:
        return None, "no values"
    if not std:
        return (0.0, "") if now == before else (None, "baseline has no variance")
    return (now - before) / std, ""


def wasserstein(spec: BinSpec, baseline: Bins, current: Bins, std: float | None) -> tuple[float | None, str]:
    """Return the approximate Wasserstein-1 distance of two numeric bin maps over the baseline std, and a reason."""
    base, cur = _segments(spec, baseline), _segments(spec, current)
    base_total, cur_total = sum(s[2] for s in base), sum(s[2] for s in cur)
    if base_total <= 0 or cur_total <= 0:
        return None, "no values"
    points: dict[float, list[float]] = {}
    for index, (segments, total) in enumerate(((base, base_total), (cur, cur_total))):
        for low, high, count in segments:
            points.setdefault((low + high) / 2, [0.0, 0.0])[index] += count / total
    cdf = [0.0, 0.0]
    area = 0.0
    ordered = sorted(points)
    for here, nxt in itertools.pairwise(ordered):
        cdf[0] += points[here][0]
        cdf[1] += points[here][1]
        area += abs(cdf[0] - cdf[1]) * (nxt - here)
    if not std:
        return (0.0, "") if area == 0 else (None, "baseline has no variance")
    return area / std, ""
