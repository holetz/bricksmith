"""Spark engine: aggregations run on the cluster, the same ``Summary`` fold produces identical measurements."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.monitor.bins import ABOVE, BELOW, NULL, UNSEEN, ZERO, BinSpec, is_null
from bricksmith.monitor.fit import build_reference
from bricksmith.monitor.measure import Measurement, Summary, measure_summary, segment_name

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyspark.sql import Column, DataFrame  # pyright: ignore[reportMissingImports]

    from bricksmith.monitor.reference import Reference


FLOATING = ("float", "double")


def _missing(column: str, dtype: str) -> Column:
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration, loaded where used  # pyright: ignore[reportMissingImports]

    value = f.col(column)
    return value.isNull() | f.isnan(value) if dtype in FLOATING else value.isNull()


def null_condition(df: DataFrame, column: str) -> Column:
    """Return whether ``column`` is null, or NaN for a float/double column (the ``is_null`` rule)."""
    return _missing(column, dict(df.dtypes).get(column, ""))


def _float_spelling(text: str) -> float | None:
    """Return the float whose ``str`` is ``text`` (a category a float value can carry), else ``None``."""
    try:
        number = float(text)
    except ValueError:
        return None
    return number if str(number) == text else None


def segment_values(df: DataFrame, column: str) -> dict[str, list[Any]]:
    """Return the distinct raw values of ``column`` grouped by their ``segment_name`` (one small row per value)."""
    names: dict[str, list[Any]] = {}
    for row in df.select(column).distinct().collect():
        names.setdefault(segment_name(row[column]), []).append(row[column])
    return dict(sorted(names.items()))


def segment_frame(df: DataFrame, column: str, values: Sequence[Any]) -> DataFrame:
    """Keep the rows of one segment, given its raw values from ``segment_values``."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    kept = [v for v in values if not is_null(v)]
    condition = f.col(column).isin(kept) if kept else f.lit(False)  # noqa: FBT003 - a Spark literal
    if len(kept) < len(values):
        condition = condition | null_condition(df, column)
    return df.where(condition)


def bin_expression(spec: BinSpec, dtype: str = "") -> Column:
    """Return the Spark expression assigning the bin id of ``spec`` (the same rules as ``BinSpec.assign``).

    ``dtype`` is the column's Spark type name: a float/double column treats NaN as null and matches its categories
    by value, so the bin carries the stored ``category_text`` spelling (``cast("string")`` would spell ``1.0E7``).
    """
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration, loaded where used  # pyright: ignore[reportMissingImports]

    if spec.kind == "categorical":
        column = f.col(spec.column)
        expr = f.when(_missing(spec.column, dtype), f.lit(NULL))
        if dtype in FLOATING:
            for category in spec.categories:
                number = _float_spelling(category)
                if number is not None:
                    expr = expr.when(column == f.lit(number), f.lit(f"cat:{category}"))
            return expr.otherwise(f.lit(UNSEEN))
        text = column.cast("string")
        kept = list(spec.categories)
        known = f.when(text.isin(kept), f.concat(f.lit("cat:"), text)) if kept else f.lit(None)
        return expr.when(known.isNotNull(), known).otherwise(f.lit(UNSEEN))
    value = f.col(spec.column).cast("double")
    last = len(spec.edges) - 1
    separate = spec.outliers == "separate"
    expr = f.when(value.isNull() | f.isnan(value), f.lit(NULL))
    if spec.zero:
        expr = expr.when(value == f.lit(0.0), f.lit(ZERO))
    expr = expr.when(value < f.lit(spec.edges[0]), f.lit(BELOW if separate else "bin_0"))
    expr = expr.when(value > f.lit(spec.edges[-1]), f.lit(ABOVE if separate else f"bin_{last - 1}"))
    for i in range(last):
        expr = expr.when(value <= f.lit(spec.edges[i + 1]), f.lit(f"bin_{i}"))
    return expr.otherwise(f.lit(f"bin_{last - 1}"))


def window_frame(df: DataFrame, window: Mapping[str, str] | None) -> DataFrame:
    """Keep rows whose window column text lies in ``[start, end)``."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    if not window:
        return df
    text = f.col(window["column"]).cast("string")
    return df.filter((text >= window["start"]) & (text < window["end"]))


def fit_spark(df: DataFrame, columns: Mapping[str, Mapping[str, Any]], **meta: Any) -> Reference:  # noqa: ANN401 - reference metadata
    """Fit a reference from a DataFrame. Value counts are grouped remotely; one small row per distinct value returns."""
    scoped = window_frame(df, meta.get("fit_window"))
    counts = {c: {r[c]: r["count"] for r in scoped.groupBy(c).count().collect()} for c in columns}
    return build_reference(counts, columns, scoped.count(), **meta)


def summarize_spark(reference: Reference, df: DataFrame) -> Summary:
    """Reduce a windowed DataFrame to a ``Summary`` with remote aggregations."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    summary = Summary(n=df.count())
    period_kind = reference.metric_config.get("stability_period", "") if reference.time else ""
    width = 10 if period_kind == "day" else 7
    dtypes = dict(df.dtypes)
    for spec in reference.columns:
        bin_col = bin_expression(spec, dtypes.get(spec.column, "")).alias("b")
        summary.bins[spec.column] = dict.fromkeys(spec.bin_ids(), 0)
        for row in df.groupBy(bin_col).count().collect():
            summary.bins[spec.column][row["b"]] = row["count"]
        if period_kind:
            period = f.substring(f.col(reference.time).cast("string"), 1, width).alias("p")
            slots: dict[str, dict[str, int]] = {}
            for row in df.filter(f.col(reference.time).isNotNull()).groupBy(period, bin_col).count().collect():
                slots.setdefault(row["p"], dict.fromkeys(spec.bin_ids(), 0))[row["b"]] = row["count"]
            summary.periods[spec.column] = slots
    if not (reference.score and reference.label):
        return summary
    label = f.col(reference.label).cast("double")
    score = f.col(reference.score).cast("double")
    bins = reference.metric_config["calibration_bins"]
    valid = df.filter(label.isin(0.0, 1.0))
    scored = valid.filter(score.isNotNull() & ~f.isnan(score))
    in_range = scored.filter((score >= 0.0) & (score <= 1.0))
    labeled = ~null_condition(df, reference.label) & label.isNotNull()
    head = df.agg(
        f.sum(f.when(labeled, 1).otherwise(0)).alias("labeled"),
        f.sum(f.when(labeled & ~label.isin(0.0, 1.0), 1).otherwise(0)).alias("invalid"),
    ).first()
    assert head is not None  # noqa: S101 - an aggregate always returns one row
    summary.labeled, summary.invalid_labels = int(head["labeled"] or 0), int(head["invalid"] or 0)
    summary.missing_scores = valid.count() - scored.count()
    summary.invalid_scores = scored.count() - in_range.count()
    one = f.when(label == 1.0, 1).otherwise(0)
    for row in scored.groupBy(score.alias("s")).agg(f.sum(one).alias("p"), f.sum(1 - one).alias("n")).collect():
        summary.groups[float(row["s"])] = (int(row["p"]), int(row["n"]))
    idx = f.least(f.floor(score * f.lit(float(bins))).cast("int"), f.lit(bins - 1)).alias("i")
    for row in (
        in_range.groupBy(idx)
        .agg(f.count(f.lit(1)).alias("n"), f.sum(score).alias("s"), f.sum(label).alias("y"))
        .collect()
    ):
        summary.calibration[int(row["i"])] = (int(row["n"]), float(row["s"]), float(row["y"]))
    squared = in_range.agg(f.sum((score - label) * (score - label)).alias("e")).first()
    assert squared is not None  # noqa: S101
    summary.squared_error = float(squared["e"] or 0.0)
    return summary


def measure_spark(
    reference: Reference, df: DataFrame, window: Mapping[str, str] | None = None, *, segments: bool = True
) -> list[Measurement]:
    """Compare a DataFrame with the reference on the cluster; equals ``measure`` on the same rows."""
    scoped = window_frame(df, window)
    out = measure_summary(reference, summarize_spark(reference, scoped))
    if segments and reference.segment:
        for name, values in segment_values(scoped, reference.segment).items():
            subset = segment_frame(scoped, reference.segment, values)
            out += measure_summary(reference, summarize_spark(reference, subset), name)
    return out


__all__ = [
    "bin_expression",
    "fit_spark",
    "measure_spark",
    "null_condition",
    "segment_frame",
    "segment_values",
    "summarize_spark",
    "window_frame",
]
