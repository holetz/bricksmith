"""Spark engine of the stability profiles: the aggregations run remotely, the same rows as the pure engine return.

Fitting counts the values of every monitored column in one job per column type (one small row per distinct
value); columns with more than ``EXACT_DISTINCT_LIMIT`` distinct values use exact percentiles computed remotely
instead. Profiling runs three jobs whatever the columns and segment sets: the row totals, the segment ranking and one
grouped aggregation over an exploded ``(segment, column, bin, value)`` array; declared ``keys`` and ``distinct``
columns add one more grouped aggregation of approximate distinct counts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.monitor.bins import NULL, BinSpec, bin_options, edges_from_cuts, fit_bins
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.infer import NUMERIC, TEXT
from bricksmith.monitor.spark import bin_expression
from bricksmith.monitor.stability.config import DISTINCT_PREFIX, KEYS_COLUMN
from bricksmith.monitor.stability.profile import (
    NULL_TEXT,
    ProfileInfo,
    _reference,  # pyright: ignore[reportPrivateUsage]
    complete_rows,
    first_periods,
    kept_segments,
    resolve_columns,
    scan_row,
    segment_sets,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyspark.sql import Column, DataFrame  # pyright: ignore[reportMissingImports]

    from bricksmith.monitor.reference import Reference
    from bricksmith.monitor.stability.config import StabilityConfig
    from bricksmith.monitor.stability.schema import Schema

EXACT_DISTINCT_LIMIT = 100_000


def _f() -> Any:  # noqa: ANN401 - the pyspark functions module
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration, loaded where used  # pyright: ignore[reportMissingImports]

    return f


_PLAIN_LOW = 1e-3
_PLAIN_HIGH = 1e7


def _float_text(df: DataFrame, name: str) -> Column:
    """Spell a float/double column like ``segment_text``: ``str(float)``, NaN as null, either zero ``0.0``.

    Inside ``[1e-3, 1e7)`` the string cast spells like Python; the few finite values outside that range and the
    infinities are collected and spelled by ``str``.
    """
    f = _f()
    value = f.col(name).cast("double")
    magnitude = f.abs(value)
    tiny = (magnitude < _PLAIN_LOW) & (value != 0)
    outside = magnitude.isNotNull() & ~f.isnan(value) & ((magnitude >= _PLAIN_HIGH) | tiny)
    expr = f.when(value.isNull() | f.isnan(value), f.lit(None).cast("string")).when(value == 0, f.lit("0.0"))
    values = [r["v"] for r in df.where(outside).select(value.alias("v")).distinct().collect()]
    for number in values:
        expr = expr.when(value == f.lit(number), f.lit(str(number)))
    return expr.otherwise(value.cast("string"))


def _text(df: DataFrame, name: str) -> Column:
    """Spell a column like ``category_text``: booleans ``true``/``false``, floats like ``str``, else cast to string."""
    f = _f()
    kind = dict(df.dtypes).get(name, "")
    if kind in ("float", "double"):
        return _float_text(df, name)
    text = f.col(name).cast("string")
    return f.lower(text) if kind == "boolean" else text


def schema_spark(df: DataFrame) -> Schema:
    """Return the schema of ``df`` (column, type name, nullable), sorted by column."""
    return tuple(
        sorted((field.name, field.dataType.simpleString(), bool(field.nullable)) for field in df.schema.fields)
    )


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
    large = {
        name
        for name, options in columns.items()
        if bin_options(name, options).kind == "numeric"
        and "edges" not in options
        and distinct.get(name, 0) > EXACT_DISTINCT_LIMIT
    }
    counted = _value_counts(scoped, sorted(set(columns) - large), types)
    specs: list[BinSpec] = []
    for name in sorted(columns):
        if name in large:
            specs.append(_with_counts(_large_numeric(scoped, name, columns[name]), scoped))
        else:
            specs.append(fit_bins(name, counted[name], columns[name]))
    reference = _reference(
        config, tuple(specs), int(head["__rows"]), fit_periods, dataset=dataset, version=version, source=source
    )
    return reference, skipped


def _value_counts(df: DataFrame, names: Sequence[str], types: dict[str, str]) -> dict[str, dict[Any, int]]:
    """Count the distinct values of every column in ``names``: one job per distinct column type, not per column."""
    f = _f()
    counts: dict[str, dict[Any, int]] = {name: {} for name in names}
    by_type: dict[str, list[str]] = {}
    for name in names:
        by_type.setdefault(types[name], []).append(name)
    for group in by_type.values():
        cells = f.explode(f.array(*(f.struct(f.lit(n).alias("c"), f.col(n).alias("v")) for n in group))).alias("cell")
        for row in df.select(cells).groupBy("cell.c", "cell.v").count().collect():
            counts[row["c"]][row["v"]] = int(row["count"])
    return counts


def _with_counts(spec: BinSpec, df: DataFrame) -> BinSpec:
    counts = dict.fromkeys(spec.bin_ids(), 0)
    dtype = dict(df.dtypes).get(spec.column, "")
    for row in df.groupBy(bin_expression(spec, dtype).alias("b")).count().collect():
        counts[row["b"]] = int(row["count"])
    return BinSpec(spec.column, spec.kind, spec.edges, spec.outliers, spec.categories, counts, spec.method, spec.zero)


def segment_column(df: DataFrame, columns: Sequence[str]) -> Column:
    """Return the canonical segment key expression ``col=value,...`` (``""`` for the whole population)."""
    f = _f()
    if not columns:
        return f.lit("")
    parts = [f.concat(f.lit(f"{c}="), f.coalesce(_text(df, c), f.lit(NULL_TEXT))) for c in columns]
    return f.concat_ws(",", *parts)


def _kept_segments(  # noqa: PLR0913, PLR0917 - the ranking inputs
    ranking: DataFrame,
    config: StabilityConfig,
    sets: Sequence[Sequence[str]],
    keys: Sequence[Column],
    carried: Mapping[str, str],
    info: ProfileInfo,
) -> tuple[dict[int, list[str]], dict[str, str]]:
    """Rank the segments of every non-empty set in one job over the ranking periods.

    Return the kept keys of each truncated set and the earliest period of every kept segment, by the same rule as
    the pure engine (:func:`kept_segments`, :func:`first_periods`).
    """
    f = _f()
    ranked = {i: k for i, k in enumerate(keys) if sets[i]}
    if not ranked:
        return {}, {}
    pairs = f.explode(f.array(*(f.struct(f.lit(i).alias("i"), k.alias("k")) for i, k in ranked.items())))
    counts: dict[int, dict[str, int]] = {i: {} for i in ranked}
    found: dict[str, str] = {}
    grouped = ranking.select("__p", pairs.alias("seg")).groupBy("seg.i", "seg.k")
    for r in grouped.agg(f.count(f.lit(1)).alias("n"), f.min("__p").alias("first")).collect():
        counts[int(r["i"])][str(r["k"])] = int(r["n"])
        found[str(r["k"])] = min(str(r["first"]), found.get(str(r["k"]), str(r["first"])))
    keep: dict[int, list[str]] = {}
    first: dict[str, str] = {}
    for i, totals in counts.items():
        kept = kept_segments(totals, config.max_segments, carried, label=",".join(sets[i]), info=info)
        first |= first_periods({k: found[k] for k in kept}, {k: v for k, v in carried.items() if k in kept})
        if len(kept) < len(totals):
            keep[i] = sorted(kept)
    return keep, first


def profile_spark(  # noqa: PLR0913 - one keyword per profile field
    binning: Reference,
    df: DataFrame,
    config: StabilityConfig,
    periods: Sequence[str],
    *,
    dataset: str,
    run_id: str = "",
    rank_periods: Sequence[str] | None = None,
    carried: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], ProfileInfo]:
    """Profile ``periods`` of ``df`` with the frozen ``binning``; equals ``profile_rows`` on the same rows.

    Segments are ranked over ``rank_periods`` (``periods`` by default) and ``carried`` segments stay kept.
    """
    f = _f()
    info = ProfileInfo()
    if not periods:
        return [], info
    period = period_column(df, config.period)
    ranking = df.where(period.isin(sorted({*periods, *(rank_periods or ())}))).withColumn("__p", period)
    head = df.agg(
        f.sum(f.when(period.isNull(), 1).otherwise(0)).alias("nulls"),
        f.sum(f.when(period.isin(list(periods)), 1).otherwise(0)).alias("rows"),
    ).first()
    info.null_period_rows = int(head["nulls"] or 0) if head else 0
    info.rows = int(head["rows"] or 0) if head else 0
    scoped = df.where(period.isin(list(periods))).withColumn("__p", period)
    weight = f.coalesce(f.col(config.weight).cast("double"), f.lit(0.0)) if config.weight else f.lit(0.0)
    sets = tuple(dict.fromkeys(segment_sets(config)))
    keys = [segment_column(df, columns) for columns in sets]
    keep, first = _kept_segments(ranking, config, sets, keys, carried or {}, info)
    cells = []
    types = dict(df.dtypes)
    for spec in binning.columns:
        bin_id = bin_expression(spec, types.get(spec.column, ""))
        value = f.col(spec.column).cast("double") if spec.kind == "numeric" else f.lit(None).cast("double")
        x = f.when(bin_id == f.lit(NULL), f.lit(None).cast("double")).otherwise(value)
        cells.append(f.struct(f.lit(spec.column).alias("c"), bin_id.alias("b"), x.alias("x")))
    segmented = scoped.select(
        "*",
        f.explode(f.array(*(f.struct(f.lit(i).alias("i"), k.alias("k")) for i, k in enumerate(keys)))).alias("__seg"),
    )
    for i, kept in keep.items():
        segmented = segmented.where((f.col("__seg.i") != i) | f.col("__seg.k").isin(kept))
    exploded = segmented.select("__p", "__seg", weight.alias("__w"), f.explode(f.array(*cells)).alias("__cell"))
    totals: dict[tuple[str, str, str, str], list[float]] = {}
    grouped = exploded.groupBy(
        "__p", "__seg.i", f.col("__seg.k").alias("__s"), f.col("__cell.c").alias("c"), f.col("__cell.b").alias("b")
    ).agg(
        f.count(f.lit(1)).alias("n"),
        f.sum("__w").alias("w"),
        f.sum(f.col("__cell.x")).alias("s"),
        f.sum(f.col("__cell.x") * f.col("__cell.x")).alias("q"),
    )
    for row in grouped.collect():
        totals[(str(row["__p"]), str(row["__s"]), str(row["c"]), str(row["b"]))] = [
            int(row["n"]), float(row["w"] or 0.0), float(row["s"] or 0.0), float(row["q"] or 0.0)
        ]  # fmt: skip
    rows = complete_rows(binning, totals, config, dataset=dataset, run_id=run_id, first=first, periods=periods)
    return [*rows, *_scan_spark(binning, scoped, config, dataset=dataset, run_id=run_id)], info


def _scan_spark(
    binning: Reference, scoped: DataFrame, config: StabilityConfig, *, dataset: str, run_id: str
) -> list[dict[str, Any]]:
    """Return the ``~keys`` and ``~distinct:<column>`` rows of every period: one grouped aggregation, approximate.

    Distinct counts use ``approx_count_distinct`` (a relative standard deviation of about 5%), so the duplicate
    key rate of a Spark profile carries that error; the pure engine counts exactly.
    """
    if not config.keys and not config.distinct:
        return []
    f = _f()
    aggs = [f.count(f.lit(1)).alias("rows")]
    if config.keys:
        parts = [f.coalesce(f.col(c).cast("string"), f.lit("\x00null")) for c in config.keys]
        aggs.append(f.approx_count_distinct(f.concat_ws("\x1f", *parts)).alias("keys"))
    for i, column in enumerate(config.distinct):
        aggs += [f.count(f.col(column)).alias(f"n{i}"), f.approx_count_distinct(f.col(column)).alias(f"d{i}")]
    out: list[dict[str, Any]] = []
    for row in scoped.groupBy("__p").agg(*aggs).collect():
        period = str(row["__p"])
        if config.keys:
            keys = (int(row["rows"]), min(int(row["keys"]), int(row["rows"])))
            out.append(scan_row(binning, period, KEYS_COLUMN, keys, dataset=dataset, run_id=run_id))
        for i, column in enumerate(config.distinct):
            count = int(row[f"n{i}"])
            if count > 0:
                counts = (count, min(int(row[f"d{i}"]), count))
                out.append(
                    scan_row(binning, period, f"{DISTINCT_PREFIX}{column}", counts, dataset=dataset, run_id=run_id)
                )
    return sorted(out, key=lambda r: (r["period"], r["column"]))


__all__ = [
    "EXACT_DISTINCT_LIMIT",
    "fit_spark_binning",
    "period_column",
    "periods_spark",
    "profile_spark",
    "schema_spark",
    "segment_column",
]
