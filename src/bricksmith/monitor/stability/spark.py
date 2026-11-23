"""Spark engine of the stability profiles: the aggregations run remotely, the same rows as the pure engine return.

Fitting groups value counts per monitored column (one small row per distinct value); columns with more than
``EXACT_DISTINCT_LIMIT`` distinct values use exact percentiles computed remotely instead. Profiling runs one
grouped aggregation per segment set over an exploded ``(column, bin, value)`` array.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.monitor.bins import NULL, BinSpec, bin_options, edges_from_cuts, fit_bins
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.infer import NUMERIC, TEXT
from bricksmith.monitor.spark import bin_expression
from bricksmith.monitor.stability.profile import (
    NULL_TEXT,
    ProfileInfo,
    _reference,  # pyright: ignore[reportPrivateUsage]
    complete_rows,
    resolve_columns,
    segment_sets,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pyspark.sql import Column, DataFrame  # pyright: ignore[reportMissingImports]

    from bricksmith.monitor.reference import Reference
    from bricksmith.monitor.stability.config import StabilityConfig

EXACT_DISTINCT_LIMIT = 100_000


def _f() -> Any:  # noqa: ANN401 - the pyspark functions module
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration, loaded where used  # pyright: ignore[reportMissingImports]

    return f


def _text(df: DataFrame, name: str) -> Column:
    """Spell a column like ``category_text``: booleans ``true``/``false``, everything else cast to string."""
    f = _f()
    kind = dict(df.dtypes).get(name, "")
    text = f.col(name).cast("string")
    return f.lower(text) if kind == "boolean" else text


def period_column(df: DataFrame, period: str) -> Column:
    """Return the period text expression (the same spelling as ``period_text``)."""
    return _text(df, period)


def periods_spark(df: DataFrame, period: str) -> list[str]:
    """Return the sorted distinct period texts of ``df`` (null periods are left out)."""
    rows = df.select(period_column(df, period).alias("p")).where("p IS NOT NULL").distinct().collect()
    return sorted(str(r["p"]) for r in rows)


def _large_numeric(df: DataFrame, column: str, config: dict[str, Any]) -> BinSpec:
    """Fit quantile bins from remote approximate percentiles for a column with very many distinct values."""
    f = _f()
    options = bin_options(column, config)
    value = f.col(column).cast("double")
    usable = df.where(value.isNotNull() & ~f.isnan(value))
    if options.zero:
        usable = usable.where(value != 0)
    probabilities = [j / options.bins for j in range(1, options.bins)]
    row = usable.agg(
        f.min(value).alias("lo"),
        f.max(value).alias("hi"),
        f.percentile_approx(value, probabilities or [0.5], 10_000).alias("cuts"),
    ).first()
    if row is None or row["lo"] is None:
        return fit_bins(column, {0: 0} if options.zero else {}, config)
    cuts = {float(c) for c in (row["cuts"] or [])} if probabilities else set()
    edges = edges_from_cuts(float(row["lo"]), cuts, float(row["hi"]))
    if len(edges) < 2 or edges[0] == edges[-1]:  # noqa: PLR2004 - two edges make one bin
        return fit_bins(column, {float(row["lo"]): 1}, config)
    return fit_bins(column, {}, {**config, "edges": list(edges), "method": "quantile"})


def fit_spark_binning(  # noqa: PLR0913 - one keyword per reference field
    df: DataFrame,
    config: StabilityConfig,
    fit_periods: Sequence[str],
    *,
    dataset: str,
    version: int,
    source: str = "",
) -> tuple[Reference, dict[str, str]]:
    """Fit a frozen binning on the rows of ``fit_periods``; equals ``fit_rows_binning`` on the same rows."""
    f = _f()
    scoped = df.where(period_column(df, config.period).isin(list(fit_periods)))
    types = dict(df.dtypes)
    countable = [c for c, t in types.items() if NUMERIC.match(t) or TEXT.match(t)]
    head = scoped.agg(
        f.count(f.lit(1)).alias("__rows"), *(f.approx_count_distinct(f.col(c)).alias(c) for c in countable)
    ).first()
    if head is None or not head["__rows"]:
        msg = f"stability: no rows in the fit periods {sorted(fit_periods)}"
        raise MonitorError(msg)
    distinct = {c: int(head[c] or 0) for c in countable}
    columns, skipped = resolve_columns(config, types, distinct)
    specs: list[BinSpec] = []
    for name in sorted(columns):
        options = columns[name]
        if (
            bin_options(name, options).kind == "numeric"
            and "edges" not in options
            and (distinct.get(name, 0) > EXACT_DISTINCT_LIMIT)
        ):
            fitted = _large_numeric(scoped, name, options)
            specs.append(_with_counts(fitted, scoped))
            continue
        counts = {r[name]: int(r["count"]) for r in scoped.groupBy(name).count().collect()}
        specs.append(fit_bins(name, counts, options))
    reference = _reference(
        config, tuple(specs), int(head["__rows"]), fit_periods, dataset=dataset, version=version, source=source
    )
    return reference, skipped


def _with_counts(spec: BinSpec, df: DataFrame) -> BinSpec:
    counts = dict.fromkeys(spec.bin_ids(), 0)
    for row in df.groupBy(bin_expression(spec).alias("b")).count().collect():
        counts[row["b"]] = int(row["count"])
    return BinSpec(spec.column, spec.kind, spec.edges, spec.outliers, spec.categories, counts, spec.method, spec.zero)


def segment_column(df: DataFrame, columns: Sequence[str]) -> Column:
    """Return the canonical segment key expression ``col=value,...`` (``""`` for the whole population)."""
    f = _f()
    if not columns:
        return f.lit("")
    parts = [f.concat(f.lit(f"{c}="), f.coalesce(_text(df, c), f.lit(NULL_TEXT))) for c in columns]
    return f.concat_ws(",", *parts)


def profile_spark(  # noqa: PLR0913 - one keyword per profile field
    binning: Reference,
    df: DataFrame,
    config: StabilityConfig,
    periods: Sequence[str],
    *,
    dataset: str,
    run_id: str = "",
) -> tuple[list[dict[str, Any]], ProfileInfo]:
    """Profile ``periods`` of ``df`` with the frozen ``binning``; equals ``profile_rows`` on the same rows."""
    f = _f()
    info = ProfileInfo()
    if not periods:
        return [], info
    period = period_column(df, config.period)
    info.null_period_rows = int(df.where(period.isNull()).count())
    scoped = df.where(period.isin(list(periods))).withColumn("__p", period)
    weight = f.coalesce(f.col(config.weight).cast("double"), f.lit(0.0)) if config.weight else f.lit(0.0)
    cells = []
    for spec in binning.columns:
        bin_id = bin_expression(spec)
        value = f.col(spec.column).cast("double") if spec.kind == "numeric" else f.lit(None).cast("double")
        x = f.when(bin_id == f.lit(NULL), f.lit(None).cast("double")).otherwise(value)
        cells.append(f.struct(f.lit(spec.column).alias("c"), bin_id.alias("b"), x.alias("x")))
    exploded = scoped.select(
        "__p", *{c for s in config.segments for c in s}, weight.alias("__w"), f.explode(f.array(*cells)).alias("__cell")
    )
    totals: dict[tuple[str, str, str, str], list[float]] = {}
    info.rows = int(scoped.count())
    for columns in segment_sets(config):
        keyed = exploded.withColumn("__s", segment_column(df, columns))
        if columns:
            ranked = scoped.withColumn("__s", segment_column(df, columns)).groupBy("__s").count().collect()
            ordered = sorted(((str(r["__s"]), int(r["count"])) for r in ranked), key=lambda i: (-i[1], i[0]))
            if len(ordered) > config.max_segments:
                info.truncated[",".join(columns)] = len(ordered) - config.max_segments
                keyed = keyed.where(f.col("__s").isin([k for k, _ in ordered[: config.max_segments]]))
        grouped = keyed.groupBy("__p", "__s", f.col("__cell.c").alias("c"), f.col("__cell.b").alias("b")).agg(
            f.count(f.lit(1)).alias("n"),
            f.sum("__w").alias("w"),
            f.sum(f.col("__cell.x")).alias("s"),
            f.sum(f.col("__cell.x") * f.col("__cell.x")).alias("q"),
        )
        for row in grouped.collect():
            totals[(str(row["__p"]), str(row["__s"]), str(row["c"]), str(row["b"]))] = [
                int(row["n"]), float(row["w"] or 0.0), float(row["s"] or 0.0), float(row["q"] or 0.0)
            ]  # fmt: skip
    return complete_rows(binning, totals, config, dataset=dataset, run_id=run_id), info


__all__ = [
    "EXACT_DISTINCT_LIMIT",
    "fit_spark_binning",
    "period_column",
    "periods_spark",
    "profile_spark",
    "segment_column",
]
