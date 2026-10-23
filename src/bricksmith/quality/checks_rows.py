"""Check evaluation over lists of dict rows (reference implementation for tests and small local data)."""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from decimal import Decimal
from typing import TYPE_CHECKING

from bricksmith.quality.checks import (
    MICROS,
    Check,
    CheckResult,
    _epoch_micros,
    _max_age_micros,
    _unavailable,
    _verdict,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _key_stats(rows: Sequence[Mapping[str, object]], cols: Sequence[str]) -> tuple[Counter[tuple[object, ...]], int]:
    counts: Counter[tuple[object, ...]] = Counter()
    nulls = 0
    for row in rows:
        key = tuple(row.get(c) for c in cols)
        if any(v is None for v in key):
            nulls += 1
        else:
            counts[key] += 1
    return counts, nulls


def _utc_naive(value: dt.datetime) -> dt.datetime:
    return value.astimezone(dt.UTC).replace(tzinfo=None) if value.tzinfo is not None else value


def _is_nan(value: float | Decimal) -> bool:
    return value.is_nan() if isinstance(value, Decimal) else math.isnan(value)


def _value_key(value: object) -> tuple[int, object, str]:
    """Order values like Spark ascending: nulls first, NaN last among numbers, dates and timestamps chronologically."""
    if value is None:
        return (0, 0, "")
    if isinstance(value, bool | int | float | Decimal):
        return (1, (1, 0) if _is_nan(value) else (0, value), "")
    if isinstance(value, str):
        return (2, value, "")
    if isinstance(value, dt.datetime):
        return (4, _utc_naive(value), "")
    if isinstance(value, dt.date):
        return (3, value.toordinal(), "")
    return (5, 0, f"{type(value).__name__}:{value!r}")


def _tuple_key(key: tuple[object, ...]) -> tuple[tuple[int, object, str], ...]:
    return tuple(_value_key(v) for v in key)


def _dups(counts: Counter[tuple[object, ...]]) -> tuple[int, int]:
    over = [n for n in counts.values() if n > 1]
    return len(over), sum(over)


def _grain(check: Check, rows: Sequence[Mapping[str, object]], limit: int | None) -> CheckResult:
    counts, nulls = _key_stats(rows, check.columns)
    groups, dup_rows = _dups(counts)
    problems = []
    if groups:
        problems.append(f"{groups} duplicate key group(s) over {dup_rows} row(s)")
    if nulls:
        problems.append(f"{nulls} row(s) with a null key")
    offending = {k for k, n in counts.items() if n > 1}
    offending.update(key for key in (tuple(r.get(c) for c in check.columns) for r in rows) if None in key)
    samples = [dict(zip(check.columns, k, strict=True)) for k in sorted(offending, key=_tuple_key)]
    observed = {"duplicate_groups": groups, "duplicate_rows": dup_rows, "null_key_rows": nulls, "rows": len(rows)}
    return _verdict(check, problems, observed, samples[:limit] if limit else ())


def _cardinality(
    check: Check, rows: Sequence[Mapping[str, object]], other: Sequence[Mapping[str, object]], limit: int | None
) -> CheckResult:
    lcounts, lnulls = _key_stats(rows, check.columns)
    rcounts, rnulls = _key_stats(other, check.columns)
    ldup, rdup = _dups(lcounts)[0] + lnulls, _dups(rcounts)[0] + rnulls
    unmatched = sum(n for k, n in lcounts.items() if k not in rcounts) + lnulls
    problems = []
    if check.expect in ("one_to_one", "one_to_many") and ldup:
        problems.append(f"{ldup} duplicate or null key group(s) on the data side")
    if check.expect in ("one_to_one", "many_to_one") and rdup:
        problems.append(f"{rdup} duplicate or null key group(s) on the other side")
    if check.require_match and unmatched:
        problems.append(f"{unmatched} data row(s) have no match in the other relation")
    observed = {"data_invalid_groups": ldup, "other_invalid_groups": rdup, "unmatched_rows": unmatched}
    unmatched_keys = {k for k in lcounts if k not in rcounts}
    null_keys = (tuple(row.get(c) for c in check.columns) for row in rows)
    unmatched_keys.update(key for key in null_keys if any(v is None for v in key))
    ordered = sorted(unmatched_keys, key=_tuple_key)
    samples = [dict(zip(check.columns, k, strict=True)) for k in ordered]
    return _verdict(check, problems, observed, samples[:limit] if limit and check.require_match else ())


def _nullability(check: Check, rows: Sequence[Mapping[str, object]]) -> CheckResult:
    nulls = {c: sum(1 for r in rows if r.get(c) is None) for c in check.columns}
    problems = [f"{c}: {n} null(s)" for c, n in nulls.items() if n]
    return _verdict(check, problems, {"nulls": nulls, "rows": len(rows)})


def _accepted(check: Check, rows: Sequence[Mapping[str, object]], limit: int | None) -> CheckResult:
    column = check.columns[0]
    bad = [
        r.get(column)
        for r in rows
        if (r.get(column) is None and not check.allow_null)
        or (r.get(column) is not None and r.get(column) not in check.values)
    ]
    distinct = sorted({repr(v): v for v in bad}.values(), key=_value_key)
    problems = [f"{len(bad)} row(s) outside the accepted values"] if bad else []
    return _verdict(check, problems, {"invalid_rows": len(bad), "rows": len(rows)}, distinct[: limit or 0])


def _completeness(check: Check, rows: Sequence[Mapping[str, object]]) -> CheckResult:
    if not rows:
        return _unavailable(check, "the dataset is empty, so completeness is undefined")
    filled = {c: sum(1 for r in rows if r.get(c) is not None) for c in check.columns}
    problems = [
        f"{c}: {n}/{len(rows)} non-null is below {check.min_ratio}"
        for c, n in filled.items()
        if n < check.min_ratio * len(rows)
    ]
    observed = {"non_null": filled, "rows": len(rows)}
    return _verdict(check, problems, observed)


def _freshness(check: Check, rows: Sequence[Mapping[str, object]], now: dt.datetime | None) -> CheckResult:
    values = [r.get(check.columns[0]) for r in rows if r.get(check.columns[0]) is not None]
    if not values:
        return _unavailable(check, "no non-null value to measure freshness")
    if not all(isinstance(v, dt.date) for v in values):
        return _unavailable(check, "freshness needs date or timestamp values")
    stamps = [
        v.replace(tzinfo=v.tzinfo or dt.UTC)
        if isinstance(v, dt.datetime)
        else dt.datetime(v.year, v.month, v.day, tzinfo=dt.UTC)  # type: ignore[union-attr]
        for v in values
    ]
    newest = max(stamps)
    age_micros = _epoch_micros(now or dt.datetime.now(dt.UTC)) - _epoch_micros(newest)
    age = age_micros / MICROS
    problems = (
        [f"newest value is {age:.0f}s old, above {check.max_age_seconds:.0f}s"]
        if age_micros > _max_age_micros(check)
        else []
    )
    return _verdict(check, problems, {"age_seconds": age, "newest": newest.isoformat()})


def run(
    check: Check,
    rows: list[Mapping[str, object]],
    other: list[Mapping[str, object]] | None,
    limit: int | None,
    now: dt.datetime | None,
) -> CheckResult:
    """Dispatch ``check`` to its row implementation."""
    if check.kind == "grain":
        return _grain(check, rows, limit)
    if check.kind == "cardinality":
        return _cardinality(check, rows, other or [], limit)
    if check.kind == "nullability":
        return _nullability(check, rows)
    if check.kind == "accepted_values":
        return _accepted(check, rows, limit)
    if check.kind == "completeness":
        return _completeness(check, rows)
    return _freshness(check, rows, now)
