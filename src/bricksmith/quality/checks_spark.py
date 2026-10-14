"""Check evaluation on Spark DataFrames: aggregates run remotely, only counts and bounded samples return."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

from bricksmith.quality.checks import Check, CheckResult, _unavailable, _verdict
from bricksmith.quality.coltypes import is_temporal

if TYPE_CHECKING:
    from collections.abc import Sequence


def _f() -> Any:  # noqa: ANN401
    from pyspark.sql import functions  # noqa: PLC0415 - heavy import only where Spark is used

    return functions


def _null_any(cols: Sequence[str]) -> Any:  # noqa: ANN401
    f = _f()
    expr = _const(value=False)
    for c in cols:
        expr = expr | f.col(c).isNull()
    return expr


def _groups(frame: Any, cols: Sequence[str]) -> tuple[int, int, int, Any]:  # noqa: ANN401
    """Return duplicate groups, rows in them, null-key rows, and the duplicate-group frame."""
    f = _f()
    nulls = int(frame.where(_null_any(cols)).count())
    dup = frame.where(~_null_any(cols)).groupBy(*cols).count().where(f.col("count") > 1)
    agg = dup.agg(f.count(f.lit(1)), f.sum("count")).collect()[0]
    return int(agg[0] or 0), int(agg[1] or 0), nulls, dup


def _keys(frame: Any, cols: Sequence[str], limit: int | None) -> list[object]:  # noqa: ANN401
    if not limit:
        return []
    return [r.asDict() for r in frame.select(*cols).orderBy(*cols).limit(limit).collect()]


def _grain(check: Check, frame: Any, limit: int | None) -> CheckResult:  # noqa: ANN401
    groups, dup_rows, nulls, dup = _groups(frame, check.columns)
    problems = []
    if groups:
        problems.append(f"{groups} duplicate key group(s) over {dup_rows} row(s)")
    if nulls:
        problems.append(f"{nulls} row(s) with a null key")
    observed = {
        "duplicate_groups": groups,
        "duplicate_rows": dup_rows,
        "null_key_rows": nulls,
        "rows": int(frame.count()),
    }
    return _verdict(check, problems, observed, _keys(dup, check.columns, limit))


def _cardinality(check: Check, frame: Any, other: Any, limit: int | None) -> CheckResult:  # noqa: ANN401
    cols = list(check.columns)
    lg, _, ln, _ = _groups(frame, cols)
    rg, _, rn, _ = _groups(other, cols)
    ldup, rdup = lg + ln, rg + rn
    lonly = frame.where(~_null_any(cols)).join(other.select(*cols).distinct(), cols, "left_anti")
    unmatched = int(lonly.count())
    problems = []
    if check.expect in ("one_to_one", "one_to_many") and ldup:
        problems.append(f"{ldup} duplicate or null key group(s) on the data side")
    if check.expect in ("one_to_one", "many_to_one") and rdup:
        problems.append(f"{rdup} duplicate or null key group(s) on the other side")
    if check.require_match and unmatched:
        problems.append(f"{unmatched} data row(s) have no match in the other relation")
    observed = {"data_invalid_groups": ldup, "other_invalid_groups": rdup, "unmatched_rows": unmatched}
    return _verdict(check, problems, observed, _keys(lonly.distinct(), cols, limit if check.require_match else None))


def _nullability(check: Check, frame: Any) -> CheckResult:  # noqa: ANN401
    f = _f()
    row = frame.agg(
        f.count(f.lit(1)).alias("rows"),
        *[f.sum(f.when(f.col(c).isNull(), 1).otherwise(0)).alias(f"n{i}") for i, c in enumerate(check.columns)],
    ).collect()[0]
    nulls = {c: int(row[f"n{i}"] or 0) for i, c in enumerate(check.columns)}
    return _verdict(
        check, [f"{c}: {n} null(s)" for c, n in nulls.items() if n], {"nulls": nulls, "rows": int(row["rows"])}
    )


def _accepted(check: Check, frame: Any, limit: int | None) -> CheckResult:  # noqa: ANN401
    f = _f()
    column = f.col(check.columns[0])
    values = list(check.values)
    if check.allow_null:
        condition = column.isNotNull() & ~column.isin(values)
    else:
        condition = ~f.coalesce(column.isin(values), _const(value=False))
    bad = frame.where(condition)
    count = int(bad.count())
    problems = [f"{count} row(s) outside the accepted values"] if count else []
    samples = (
        [r[0] for r in bad.select(column).distinct().orderBy(column).limit(limit).collect()] if limit and count else []
    )
    return _verdict(check, problems, {"invalid_rows": count, "rows": int(frame.count())}, samples)


def _completeness(check: Check, frame: Any) -> CheckResult:  # noqa: ANN401
    f = _f()
    row = frame.agg(
        f.count(f.lit(1)).alias("rows"), *[f.count(f.col(c)).alias(f"n{i}") for i, c in enumerate(check.columns)]
    ).collect()[0]
    rows = int(row["rows"])
    if rows == 0:
        return _unavailable(check, "the dataset is empty, so completeness is undefined")
    filled = {c: int(row[f"n{i}"]) for i, c in enumerate(check.columns)}
    problems = [
        f"{c}: {n}/{rows} non-null is below {check.min_ratio}" for c, n in filled.items() if n < check.min_ratio * rows
    ]
    return _verdict(check, problems, {"non_null": filled, "rows": rows})


def _freshness(check: Check, frame: Any, now: dt.datetime | None) -> CheckResult:  # noqa: ANN401
    f = _f()
    column = check.columns[0]
    type_name = frame.schema[column].dataType.simpleString()
    if not is_temporal(type_name):
        return _unavailable(check, f"freshness needs a date or timestamp column, {column!r} is {type_name}")
    newest = frame.agg(f.max(f.col(column)).cast("timestamp").cast("long").alias("t")).collect()[0]["t"]
    if newest is None:
        return _unavailable(check, "no non-null value to measure freshness")
    age = (now or dt.datetime.now(dt.UTC)).timestamp() - float(newest)
    problems = (
        [f"newest value is {age:.0f}s old, above {check.max_age_seconds:.0f}s"] if age > check.max_age_seconds else []
    )
    return _verdict(check, problems, {"age_seconds": age, "newest_epoch": int(newest)})


def run(check: Check, frame: Any, other: Any, limit: int | None, now: dt.datetime | None) -> CheckResult:  # noqa: ANN401
    """Dispatch ``check`` to its Spark implementation."""
    if check.kind == "grain":
        return _grain(check, frame, limit)
    if check.kind == "cardinality":
        return _cardinality(check, frame, other, limit)
    if check.kind == "nullability":
        return _nullability(check, frame)
    if check.kind == "accepted_values":
        return _accepted(check, frame, limit)
    if check.kind == "completeness":
        return _completeness(check, frame)
    return _freshness(check, frame, now)


def _const(*, value: object) -> Any:  # noqa: ANN401
    return _f().lit(value)
