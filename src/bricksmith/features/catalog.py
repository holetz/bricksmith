"""The aggregate catalog: every windowed aggregate, its family, its output type and its null semantics.

A window of ``w`` periods holds ``v[k]`` for lags ``k = 0`` (the current closing) to ``w - 1``; missing closings
and null values are both null. ``n`` is the number of non-null values. The chronological position of lag ``k``
is ``t = w - 1 - k`` (the oldest position is ``t = 0``). A *step* is a pair of chronologically adjacent positions
that are both non-null; a null between two values breaks the step. Every aggregate returns null when ``n`` is
below its ``min_points``. Every engine follows these definitions; the numpy reference is their oracle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bricksmith.features.errors import FeatureError

BASIC = "basic"
DISTRIBUTION = "distribution"
SEQUENCE = "sequence"
TERCILES = "terciles"
MOMENTS = "moments"
TREND = "trend"
#: Aggregate families, the unit the benchmark measures and the engine dispatch routes.
FAMILIES = (BASIC, DISTRIBUTION, SEQUENCE, TERCILES, MOMENTS, TREND)

#: Output types: counts are 64-bit integers, every other value is double precision.
INTEGER = "bigint"
DOUBLE = "double"

_PERCENTILE = re.compile(r"p([1-9][0-9]?)")


@dataclass(frozen=True)
class Aggregate:
    """One catalog entry.

    ``min_points`` is the smallest number of non-null values that yields a value (``0``: never null when the
    window is computed). ``min_window`` is the smallest window the aggregate is defined on; smaller windows give
    null. ``description`` states the definition and any further null rule.
    """

    name: str
    family: str
    min_points: int
    description: str
    kind: str = DOUBLE
    min_window: int = 1


def _entries() -> dict[str, Aggregate]:
    entries = [
        Aggregate("count", BASIC, 0, "number of non-null values", INTEGER),
        Aggregate("count_positive", BASIC, 0, "number of values greater than zero", INTEGER),
        Aggregate("sum", BASIC, 1, "sum of the non-null values"),
        Aggregate("mean", BASIC, 1, "sum / n"),
        Aggregate("mean_zero", BASIC, 1, "sum / w: the mean with nulls counted as zero"),
        Aggregate("min", BASIC, 1, "smallest value"),
        Aggregate("max", BASIC, 1, "largest value"),
        Aggregate("range", BASIC, 1, "max - min"),
        Aggregate("last", BASIC, 1, "the value at the current closing (null when it is null)"),
        Aggregate("mode", DISTRIBUTION, 1, "most frequent value; ties go to the smallest value"),
        Aggregate("mode_count", DISTRIBUTION, 1, "how many times the mode occurs", INTEGER),
        Aggregate("distinct", DISTRIBUTION, 0, "number of distinct non-null values", INTEGER),
        Aggregate("null_run_trailing", SEQUENCE, 0, "consecutive nulls from the current closing backwards", INTEGER),
        Aggregate("null_run_max", SEQUENCE, 0, "longest run of consecutive nulls", INTEGER),
        Aggregate("increments", SEQUENCE, 0, "number of rising steps", INTEGER),
        Aggregate("decrements", SEQUENCE, 0, "number of falling steps", INTEGER),
        Aggregate("increment_run_current", SEQUENCE, 0, "rising steps in a row up to the current closing", INTEGER),
        Aggregate("decrement_run_current", SEQUENCE, 0, "falling steps in a row up to the current closing", INTEGER),
        Aggregate("increment_run_max", SEQUENCE, 0, "longest run of rising steps", INTEGER),
        Aggregate("decrement_run_max", SEQUENCE, 0, "longest run of falling steps", INTEGER),
        Aggregate("delta", SEQUENCE, 2, "newest non-null value - oldest non-null value"),
        Aggregate("delta_mean", SEQUENCE, 2, "delta / periods between the newest and the oldest non-null values"),
        Aggregate("abs_variation", SEQUENCE, 2, "sum of the absolute step differences; null without a step"),
        Aggregate("since_positive", SEQUENCE, 1, "periods since the latest positive value; null when none", INTEGER),
        Aggregate("since_max", SEQUENCE, 1, "periods since the latest occurrence of the maximum", INTEGER),
        Aggregate("tercile_old_mean", TERCILES, 1, "mean of the oldest third of the positions", min_window=3),
        Aggregate("tercile_mid_mean", TERCILES, 1, "mean of the middle third of the positions", min_window=3),
        Aggregate("tercile_new_mean", TERCILES, 1, "mean of the newest third of the positions", min_window=3),
        Aggregate("tercile_diff", TERCILES, 1, "tercile_new_mean - tercile_old_mean", min_window=3),
        Aggregate("var_pop", MOMENTS, 1, "population variance (0 for a constant series)"),
        Aggregate("var_samp", MOMENTS, 2, "sample variance (0 for a constant series)"),
        Aggregate("std_pop", MOMENTS, 1, "population standard deviation"),
        Aggregate("std_samp", MOMENTS, 2, "sample standard deviation"),
        Aggregate("cv", MOMENTS, 2, "std_samp / |mean|; null when the mean is 0"),
        Aggregate("rms", MOMENTS, 1, "square root of the mean of the squares"),
        Aggregate("skew", MOMENTS, 3, "m3 / m2^1.5 about the window's own mean; null for a constant series"),
        Aggregate("kurtosis", MOMENTS, 4, "excess kurtosis m4 / m2^2 - 3; null for a constant series"),
        Aggregate("slope", TREND, 2, "OLS slope of the value on the chronological position"),
        Aggregate("intercept", TREND, 2, "OLS intercept at the oldest position (t = 0)"),
        Aggregate("r2", TREND, 2, "1 - SSE / SST; null for a constant series"),
        Aggregate("resid_std", TREND, 3, "sqrt(SSE / (n - 2))"),
        Aggregate("trend_distance", TREND, 2, "current value - the trend at the current position; null if it is null"),
    ]
    return {a.name: a for a in entries}


CATALOG: dict[str, Aggregate] = _entries()


def percentile_of(name: str) -> int | None:
    """Return ``q`` for a percentile aggregate named ``p<q>`` (1 to 99), else ``None``."""
    match = _PERCENTILE.fullmatch(name)
    return int(match.group(1)) if match else None


def aggregate(name: str) -> Aggregate:
    """Return the catalog entry of ``name``; percentiles ``p1`` to ``p99`` use linear interpolation."""
    if name in CATALOG:
        return CATALOG[name]
    q = percentile_of(name)
    if q is not None:
        return Aggregate(name, DISTRIBUTION, 1, f"{q}th percentile, linear interpolation over the non-null values")
    known = ", ".join(sorted(CATALOG))
    msg = f"unknown aggregate {name!r}; known: {known}, and percentiles p1..p99"
    raise FeatureError(msg)
