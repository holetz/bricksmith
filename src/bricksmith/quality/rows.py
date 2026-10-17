"""Tiny pure-Python reference comparison over lists of dict rows (unit tests and small local data)."""

from __future__ import annotations

import decimal
import math
from collections import Counter
from typing import TYPE_CHECKING

from bricksmith.quality.coltypes import is_numeric
from bricksmith.quality.errors import QualityError
from bricksmith.quality.report import (
    ColumnResult,
    ComparisonReport,
    SideStats,
    blocked_reasons,
    check_sample_limit,
    format_deviation,
    invalid_reasons,
    sample_order,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.quality.plan import ColumnPlan, ComparisonPlan, Tolerance


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


_EXACT = decimal.Context(prec=2000, traps=[decimal.Inexact, decimal.InvalidOperation, decimal.Overflow])


def absolute_deviation(left: object, right: object) -> decimal.Decimal | None:
    """Return the finite ``|left - right|`` of two numbers, or ``None`` when the pair is not eligible.

    Null, non-numeric, bool, NaN and infinite operands are not eligible. With a floating operand the deviation is
    the IEEE double difference (a non-finite result is not eligible); otherwise it is the exact decimal difference
    and an inexact one raises ``QualityError`` instead of rounding silently.
    """
    if not (_eligible(left) and _eligible(right)):
        return None
    if isinstance(left, float) or isinstance(right, float):
        try:
            diff = abs(float(left) - float(right))  # type: ignore[arg-type]
        except OverflowError:
            return None
        return decimal.Decimal(repr(diff)) if math.isfinite(diff) else None
    try:
        return _EXACT.abs(_EXACT.subtract(decimal.Decimal(left), decimal.Decimal(right)))  # type: ignore[arg-type]
    except decimal.DecimalException as exc:
        msg = f"deviation of {left!r} and {right!r} is outside the exact supported range"
        raise QualityError(msg) from exc


def _eligible(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float | decimal.Decimal):
        return False
    return (
        math.isfinite(value)
        if isinstance(value, float)
        else not isinstance(value, decimal.Decimal) or value.is_finite()
    )


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
    numeric_severity: bool = False,
) -> ComparisonReport:
    """Compare two row lists under ``plan``. Samples are returned only for an explicit ``sample_limit``.

    With ``numeric_severity`` each numeric column reports its largest finite absolute deviation among mismatches, and
    mismatch samples are ordered by largest deviation (ties and ineligible pairs by key) instead of by key.
    """
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
        "numeric_severity": numeric_severity,
    }
    if reasons:
        return ComparisonReport(**base, samples=samples)  # type: ignore[arg-type]
    common = sorted((k for k in lrows if k in rrows), key=repr)
    tally = _Tally(limit, numeric_severity=numeric_severity)
    mismatches = {c.name: 0 for c in plan.columns}
    differing = 0
    for key in common:
        bad = False
        for column in plan.columns:
            a, b = lrows[key].get(column.name), rrows[key].get(column.name)
            if not values_equal(a, b, column.tolerance):
                mismatches[column.name] += 1
                bad = True
                sample = {"key": dict(zip(plan.keys, key, strict=True)), "left": a, "right": b}
                tally.add(column, key, sample)
        differing += bad
    samples.update(tally.samples())
    return ComparisonReport(
        **base,  # type: ignore[arg-type]
        matched_keys=len(common),
        rows_with_differences=differing,
        columns=tuple(ColumnResult(c.name, mismatches[c.name], tally.worst(c.name)) for c in plan.columns),
        samples=samples,
    )


_Entry = tuple[tuple[object, ...], dict[str, object]]


class _Tally:
    """Bounded mismatch samples per column, plus the largest deviation when severity is requested."""

    def __init__(self, limit: int | None, *, numeric_severity: bool) -> None:
        self.limit = limit
        self.severity = numeric_severity
        self._worst: dict[str, decimal.Decimal] = {}
        self._buckets: dict[str, list[_Entry]] = {}

    def add(self, column: ColumnPlan, key: tuple[object, ...], sample: dict[str, object]) -> None:
        ranked = self.severity and is_numeric(column.left_type) and is_numeric(column.right_type)
        if ranked:
            dev = absolute_deviation(sample["left"], sample["right"])
            if dev is not None and (column.name not in self._worst or dev > self._worst[column.name]):
                self._worst[column.name] = dev
            sample["abs_deviation"] = format_deviation(dev)
        if self.limit is None:
            return
        bucket = self._buckets.setdefault(column.name, [])
        if not ranked:
            if len(bucket) < self.limit:
                bucket.append((key, sample))
            return
        bucket.append((key, sample))
        if len(bucket) > 2 * self.limit:
            bucket[:] = _top(bucket, self.limit)

    def worst(self, name: str) -> str | None:
        return format_deviation(self._worst.get(name))

    def samples(self) -> dict[str, list[object]]:
        out: dict[str, list[object]] = {}
        for name, bucket in self._buckets.items():
            entries = _top(bucket, self.limit or 0) if "abs_deviation" in bucket[0][1] else bucket
            out[f"mismatch:{name}"] = [sample for _, sample in entries]
        return out


def _top(bucket: list[_Entry], limit: int) -> list[_Entry]:
    def order(item: _Entry, *, text: bool) -> tuple[bool, decimal.Decimal, tuple[object, ...]]:
        key = tuple(repr(v) for v in item[0]) if text else item[0]
        return sample_order(item[1]["abs_deviation"], key)  # type: ignore[arg-type]

    try:
        return sorted(bucket, key=lambda item: order(item, text=False))[:limit]
    except TypeError:
        return sorted(bucket, key=lambda item: order(item, text=True))[:limit]
