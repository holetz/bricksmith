"""Tiny pure-Python reference comparison over lists of dict rows (unit tests and small local data)."""

from __future__ import annotations

import decimal
import math
from collections import Counter
from typing import TYPE_CHECKING

from bricksmith.quality.report import (
    ColumnResult,
    ComparisonReport,
    SideStats,
    blocked_reasons,
    check_sample_limit,
    invalid_reasons,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.quality.plan import ComparisonPlan, Tolerance


def _dec(value: object) -> decimal.Decimal:
    return decimal.Decimal(repr(value)) if isinstance(value, float) else decimal.Decimal(value)  # type: ignore[arg-type]


def values_equal(left: object, right: object, tolerance: Tolerance | None = None) -> bool:  # noqa: PLR0911 - one exit per rule
    """Null-safe equality: two nulls match, a null never matches a value, NaN equals NaN.

    With a tolerance, numbers match when ``|l - r| <= max(absolute, relative * max(|l|, |r|))``, evaluated in exact
    decimal arithmetic so the inclusive boundary does not depend on binary floating point.
    """
    if left is None or right is None:
        return left is None and right is None
    lnan = isinstance(left, float) and math.isnan(left)
    rnan = isinstance(right, float) and math.isnan(right)
    if lnan or rnan:
        return lnan and rnan
    if left == right:
        return True
    numeric = (int, float, decimal.Decimal)
    if tolerance is None or tolerance.exact:
        return False
    if (
        isinstance(left, bool)
        or isinstance(right, bool)
        or not isinstance(left, numeric)
        or not isinstance(right, numeric)
    ):
        return False
    if any(isinstance(v, float) and math.isinf(v) for v in (left, right)):
        return False
    a, b = _dec(left), _dec(right)
    allowed = max(_dec(tolerance.absolute), _dec(tolerance.relative) * max(abs(a), abs(b)))
    return abs(a - b) <= allowed


def _key(row: Mapping[str, object], keys: Sequence[str]) -> tuple[object, ...]:
    return tuple(row.get(k) for k in keys)


def _stats(
    rows: Sequence[Mapping[str, object]], keys: Sequence[str]
) -> tuple[SideStats, dict[tuple[object, ...], Mapping[str, object]]]:
    counts: Counter[tuple[object, ...]] = Counter()
    first: dict[tuple[object, ...], Mapping[str, object]] = {}
    nulls = 0
    for row in rows:
        key = _key(row, keys)
        if any(v is None for v in key):
            nulls += 1
            continue
        counts[key] += 1
        first.setdefault(key, row)
    dups = [n for n in counts.values() if n > 1]
    return SideStats(len(rows), nulls, len(dups), sum(dups)), first


def compare_rows(
    left: Sequence[Mapping[str, object]],
    right: Sequence[Mapping[str, object]],
    plan: ComparisonPlan,
    *,
    sample_limit: int | None = None,
) -> ComparisonReport:
    """Compare two row lists under ``plan``. Samples are returned only for an explicit ``sample_limit``."""
    limit = check_sample_limit(sample_limit)
    if not plan.executable:
        return ComparisonReport(plan, None, None, blocked_reasons(plan), sample_limit=limit)
    lstats, lrows = _stats(left, plan.keys)
    rstats, rrows = _stats(right, plan.keys)
    reasons = invalid_reasons(lstats, rstats, plan)
    missing = sorted((k for k in lrows if k not in rrows), key=repr)
    extra = sorted((k for k in rrows if k not in lrows), key=repr)
    samples: dict[str, list[object]] = {}
    if limit is not None:
        samples["missing_in_right"] = [dict(zip(plan.keys, k, strict=True)) for k in missing[:limit]]
        samples["extra_in_right"] = [dict(zip(plan.keys, k, strict=True)) for k in extra[:limit]]
    base = {
        "plan": plan,
        "left": lstats,
        "right": rstats,
        "reasons": reasons,
        "missing_in_right": len(missing),
        "extra_in_right": len(extra),
        "sample_limit": limit,
    }
    if reasons:
        return ComparisonReport(**base, samples=samples)  # type: ignore[arg-type]
    common = sorted((k for k in lrows if k in rrows), key=repr)
    mismatches = {c.name: 0 for c in plan.columns}
    differing = 0
    for key in common:
        bad = False
        for column in plan.columns:
            a, b = lrows[key].get(column.name), rrows[key].get(column.name)
            if not values_equal(a, b, column.tolerance):
                mismatches[column.name] += 1
                bad = True
                if limit is not None:
                    bucket = samples.setdefault(f"mismatch:{column.name}", [])
                    if len(bucket) < limit:
                        bucket.append({"key": dict(zip(plan.keys, key, strict=True)), "left": a, "right": b})
        differing += bad
    return ComparisonReport(
        **base,  # type: ignore[arg-type]
        matched_keys=len(common),
        rows_with_differences=differing,
        columns=tuple(ColumnResult(c.name, mismatches[c.name]) for c in plan.columns),
        samples=samples,
    )
