"""Comparison on Spark DataFrames: every count is computed remotely and only bounded results are collected."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.quality.report import (
    ColumnResult,
    ComparisonReport,
    SideStats,
    blocked_reasons,
    check_sample_limit,
    invalid_reasons,
)

if TYPE_CHECKING:
    from bricksmith.quality.plan import ColumnPlan, ComparisonPlan, Tolerance


def _functions() -> Any:  # noqa: ANN401
    from pyspark.sql import functions  # noqa: PLC0415 - heavy import only where Spark is used  # pyright: ignore[reportMissingImports]

    return functions


def match_expression(left: Any, right: Any, tolerance: Tolerance) -> Any:  # noqa: ANN401
    """Return the null-safe match condition of two Spark columns under ``tolerance`` (inclusive boundary).

    Tolerant comparison is evaluated in double precision, so tolerances at an exact boundary are only
    reliable when the operands are exactly representable; the pure-Python reference uses exact decimals.
    """
    f = _functions()
    exact = left.eqNullSafe(right)
    if tolerance.exact:
        return exact
    a, b = left.cast("double"), right.cast("double")
    allowed = f.greatest(
        f.lit(float(tolerance.absolute)), f.lit(float(tolerance.relative)) * f.greatest(f.abs(a), f.abs(b))
    )
    return exact | (a.isNotNull() & b.isNotNull() & (f.abs(a - b) <= allowed))


def _side_stats(frame: Any, keys: tuple[str, ...]) -> SideStats:  # noqa: ANN401
    f = _functions()
    any_null = _any_null(keys)
    total = frame.agg(
        f.count(f.lit(1)).alias("rows"), f.sum(f.when(any_null, 1).otherwise(0)).alias("nulls")
    ).collect()[0]
    valid = frame.where(~any_null)
    dups = valid.groupBy(*keys).count().where(f.col("count") > 1).agg(f.count(f.lit(1)), f.sum("count")).collect()[0]
    return SideStats(int(total["rows"]), int(total["nulls"] or 0), int(dups[0] or 0), int(dups[1] or 0))


def _prefixed(frame: Any, plan: ComparisonPlan, prefix: str) -> Any:  # noqa: ANN401
    f = _functions()
    selected = [f.col(k) for k in plan.keys]
    selected += [f.col(c.name).alias(f"{prefix}{i}") for i, c in enumerate(plan.columns)]
    return frame.select(*selected)


def _mismatch(index: int, column: ColumnPlan, joined: Any) -> Any:  # noqa: ANN401
    return ~match_expression(joined[f"l_{index}"], joined[f"r_{index}"], column.tolerance)


def _key_rows(frame: Any, plan: ComparisonPlan, limit: int) -> list[object]:  # noqa: ANN401
    return [row.asDict() for row in frame.select(*plan.keys).limit(limit).collect()]


def compare_frames(left: Any, right: Any, plan: ComparisonPlan, *, sample_limit: int | None = None) -> ComparisonReport:  # noqa: ANN401
    """Compare two Spark DataFrames under ``plan`` without collecting them.

    Duplicate or null keys on either side make the report ``invalid`` and skip value comparison. Without an
    explicit ``sample_limit`` nothing but counts is returned; with one, at most that many rows per category.
    """
    f = _functions()
    limit = check_sample_limit(sample_limit)
    if not plan.executable:
        return ComparisonReport(plan, None, None, blocked_reasons(plan), sample_limit=limit)
    lstats, rstats = _side_stats(left, plan.keys), _side_stats(right, plan.keys)
    reasons = invalid_reasons(lstats, rstats, plan)
    lkeys = left.select(*plan.keys).where(~_any_null(plan.keys)).distinct()
    rkeys = right.select(*plan.keys).where(~_any_null(plan.keys)).distinct()
    only_left = lkeys.join(rkeys, list(plan.keys), "left_anti")
    only_right = rkeys.join(lkeys, list(plan.keys), "left_anti")
    samples: dict[str, list[object]] = {}
    counts = [only_left.count(), only_right.count()]
    if limit is not None:
        samples["missing_in_right"] = _key_rows(only_left.orderBy(*plan.keys), plan, limit)
        samples["extra_in_right"] = _key_rows(only_right.orderBy(*plan.keys), plan, limit)
    base: dict[str, Any] = {
        "plan": plan,
        "left": lstats,
        "right": rstats,
        "reasons": reasons,
        "missing_in_right": counts[0],
        "extra_in_right": counts[1],
        "sample_limit": limit,
    }
    if reasons:
        return ComparisonReport(**base, samples=samples)
    l_named = _prefixed(left, plan, "l_")
    r_named = _prefixed(right, plan, "r_")
    joined = l_named.join(r_named, list(plan.keys), "inner")
    flags = [_mismatch(i, c, joined) for i, c in enumerate(plan.columns)]
    any_diff = _const(value=False)
    for flag in flags:
        any_diff = any_diff | flag
    aggregates = [f.count(f.lit(1)).alias("matched"), f.sum(f.when(any_diff, 1).otherwise(0)).alias("differing")]
    aggregates += [f.sum(f.when(flag, 1).otherwise(0)).alias(f"c{i}") for i, flag in enumerate(flags)]
    row = joined.agg(*aggregates).collect()[0]
    results = tuple(ColumnResult(c.name, int(row[f"c{i}"] or 0)) for i, c in enumerate(plan.columns))
    if limit is not None:
        for i, (column, flag) in enumerate(zip(plan.columns, flags, strict=True)):
            bad = joined.where(flag).select(*plan.keys, f.col(f"l_{i}").alias("left"), f.col(f"r_{i}").alias("right"))
            found = bad.orderBy(*plan.keys).limit(limit).collect()
            if found:
                samples[f"mismatch:{column.name}"] = [
                    {"key": {k: r[k] for k in plan.keys}, "left": r["left"], "right": r["right"]} for r in found
                ]
    return ComparisonReport(
        **base,
        matched_keys=int(row["matched"]),
        rows_with_differences=int(row["differing"] or 0),
        columns=results,
        samples=samples,
    )


def _any_null(keys: tuple[str, ...]) -> Any:  # noqa: ANN401
    f = _functions()
    expr = _const(value=False)
    for key in keys:
        expr = expr | f.col(key).isNull()
    return expr


def _const(*, value: object) -> Any:  # noqa: ANN401
    return _functions().lit(value)
