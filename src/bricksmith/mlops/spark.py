# ruff: noqa: FBT003 - Spark literals take a positional bool
"""Spark implementations of the dataset construction helpers (mirrors :mod:`bricksmith.mlops.reference`).

PySpark is imported inside the functions, so importing this module stays light. Inputs are Spark
DataFrames; tasks read them through ``ctx.read`` so every dependency is visible in the graph.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.spec import to_date
from bricksmith.mlops.validate import fingerprint, rows_from, validate_dataset, validate_prediction

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.mlops.spec import ModelSpec
    from bricksmith.mlops.validate import DatasetFingerprint, ValidationReport


def predicate_column(predicate: Mapping[str, Any]) -> Any:  # noqa: ANN401, PLR0911 - Spark Column, one branch per operator
    """Translate a declarative predicate to a Spark boolean Column that is never null."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    if "all" in predicate:
        out = f.lit(True)
        for item in predicate["all"]:
            out = out & predicate_column(item)
        return out
    if "any" in predicate:
        out = f.lit(False)
        for item in predicate["any"]:
            out = out | predicate_column(item)
        return out
    if "not" in predicate:
        return ~predicate_column(predicate["not"])
    column, op = f.col(predicate["column"]), predicate["op"]
    if op == "is_null":
        return column.isNull()
    if op == "not_null":
        return column.isNotNull()
    if op == "in":
        return f.coalesce(column.isin(predicate["value"]), f.lit(False))
    value = f.lit(predicate["value"])
    test = {
        "eq": column == value,
        "ne": column != value,
        "lt": column < value,
        "le": column <= value,
        "gt": column > value,
        "ge": column >= value,
    }[op]
    return f.coalesce(test, f.lit(False))


def sample_column(spec: ModelSpec) -> Any:  # noqa: ANN401
    """Boolean Column: the key's seeded hash fraction is below the sample fraction (same hash as the reference)."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    if spec.sample is None:
        return f.lit(True)
    text = f.concat_ws("|", *[f.coalesce(f.col(k).cast("string"), f.lit("")) for k in spec.key])
    digest = f.sha2(f.concat(f.lit(f"{spec.sample.seed}:"), text), 256)
    position = f.conv(f.substring(digest, 1, 8), 16, 10).cast("bigint") / f.lit(4294967296.0)
    return position < f.lit(spec.sample.fraction)


def _first_match(rules: Any, spec_rules: Any) -> Any:  # noqa: ANN401
    expr = None
    for rule in spec_rules:
        cond = predicate_column(rule.where)
        expr = rules.when(cond, rules.lit(rule.name)) if expr is None else expr.when(cond, rules.lit(rule.name))
    return expr if expr is not None else rules.lit(None).cast("string")


def build_population(source: Any, spec: ModelSpec) -> tuple[Any, Any]:  # noqa: ANN401
    """Return ``(population, exclusions)`` DataFrames: sample by key hash, then first matching exclusion reason."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    base = [f.col(k) for k in spec.key] + [f.to_date(f.col(spec.snapshot)).alias(spec.snapshot)]
    sampled = (
        source.filter(sample_column(spec))
        .withColumn("_reason", _first_match(f, spec.exclusions))
        .withColumn("_segment", _first_match(f, spec.segments))
    )
    population = sampled.filter(f.col("_reason").isNull()).select(*base, f.col("_segment").alias("segment"))
    exclusions = sampled.filter(f.col("_reason").isNotNull()).select(
        f.lit(spec.model).alias("model"), *base, f.col("_reason").alias("exclusion_reason")
    )
    return population, exclusions


def build_features(source: Any, spec: ModelSpec) -> Any:  # noqa: ANN401
    """Project key, snapshot, feature time and features in contract order, cast to the declared types."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    return source.select(
        *spec.key,
        f.to_date(f.col(spec.snapshot)).alias(spec.snapshot),
        f.to_date(f.col(spec.feature_time)).alias(spec.feature_time),
        *[f.col(x.name).cast(x.type).alias(x.name) for x in spec.features],
    )


def build_labels(source: Any, spec: ModelSpec) -> Any:  # noqa: ANN401
    """Project key, snapshot, the label and the date it becomes observable."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    snapshot = f.to_date(f.col(spec.snapshot))
    return source.select(
        *spec.key,
        snapshot.alias(spec.snapshot),
        f.col(spec.label.name).cast(spec.label.type).alias(spec.label.name),
        f.date_add(snapshot, spec.label.maturity_days).alias("label_time"),
    )


def build_training(population: Any, features: Any, labels: Any, spec: ModelSpec, *, drop_immature: bool = False) -> Any:  # noqa: ANN401
    """Join on key and snapshot, add the split (gap rows dropped); immature labels stay unless ``drop_immature``."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    on = [*spec.key, spec.snapshot]
    snap = f.col(spec.snapshot)
    if spec.split is None:
        split = f.lit("train")
    else:
        split = f.when(snap <= f.lit(spec.split.train_until), f.lit("train")).when(
            snap >= f.lit(spec.split.evaluation_from), f.lit("evaluation")
        )
    joined = (
        population.join(features, on)
        .join(labels.select(*on, spec.label.name, "label_time"), on)
        .withColumn("split", split)
        .filter(f.col("split").isNotNull())
    )
    if drop_immature and spec.reference_date:
        joined = joined.filter(f.col("label_time") <= f.lit(spec.reference_date))
    return joined.select(
        *on, "segment", spec.feature_time, *[x.name for x in spec.features], spec.label.name, "label_time", "split"
    )


def build_prediction(population: Any, features: Any, spec: ModelSpec, as_of: object) -> Any:  # noqa: ANN401
    """Return the population rows of the snapshot ``as_of`` joined with the features observed by ``as_of``."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    day = f.lit(to_date(as_of))
    on = [*spec.key, spec.snapshot]
    usable = features.filter(f.col(spec.feature_time) <= day)
    joined = population.filter(f.col(spec.snapshot) == day).join(usable, on)
    return joined.select(*on, "segment", spec.feature_time, *[x.name for x in spec.features])


def frame_columns(frame: Any) -> list[tuple[str, str]]:  # noqa: ANN401
    """Return the frame's ordered ``(name, type)`` pairs."""
    return [(str(name), str(kind)) for name, kind in frame.dtypes]


def validate_frame(frame: Any, spec: ModelSpec, *, reference_date: object = None) -> ValidationReport:  # noqa: ANN401
    """Validate a training DataFrame: schema against the contract; rows from the projected key/time/split columns."""
    needed = [
        c
        for c in (*spec.key, spec.snapshot, spec.feature_time, spec.label.name, "label_time", "split")
        if c in frame.columns
    ]
    return validate_dataset(
        spec,
        rows_from(frame.select(*needed).collect()),
        columns=frame_columns(frame),
        reference_date=reference_date,  # type: ignore[arg-type]
    )


def validate_prediction_frame(frame: Any, spec: ModelSpec, *, as_of: object) -> ValidationReport:  # noqa: ANN401
    """Validate a prediction DataFrame against the contract and the ``as_of`` boundary."""
    needed = [c for c in (*spec.key, spec.snapshot, spec.feature_time) if c in frame.columns]
    return validate_prediction(
        spec, rows_from(frame.select(*needed).collect()), as_of=to_date(as_of), columns=frame_columns(frame)
    )


def table_versions(spark: Any, names: list[str]) -> dict[str, int | None]:  # noqa: ANN401
    """Return the current Delta version of each table (None when it is not a Delta table)."""
    from bricksmith.storage import current_version  # noqa: PLC0415

    return {name: current_version(spark, name) for name in names}


def task_inputs(ctx: Any) -> dict[str, int | None]:  # noqa: ANN401
    """Pinned versions of the tables a running task reads (``ctx`` is the runner's task context)."""
    names = sorted({binding.physical for binding in ctx.entry.reads})
    return table_versions(ctx.io.spark, names)


def provenance_frame(
    spark: Any,  # noqa: ANN401
    spec: ModelSpec,
    role: str,
    stamp: DatasetFingerprint,
    counts: Mapping[str, int],
) -> Any:  # noqa: ANN401
    """One-row DataFrame recording what a dataset was built from: model, role, fingerprint, config, inputs, counts."""
    row = (
        spec.model,
        role,
        stamp.value,
        stamp.config,
        json.dumps(dict(stamp.inputs), sort_keys=True),
        json.dumps(list(stamp.unpinned)),
        json.dumps(dict(counts), sort_keys=True),
    )
    ddl = (
        "model STRING, dataset STRING, fingerprint STRING, config STRING, inputs STRING, unpinned STRING, counts STRING"
    )
    return spark.createDataFrame([row], ddl)


def resolve_fingerprint(ctx: Any, spec: ModelSpec, role: str) -> DatasetFingerprint:  # noqa: ANN401
    """Fingerprint ``spec`` with the table versions the running task reads."""
    return fingerprint(spec, task_inputs(ctx), role=role)


def latest_snapshot(population: Any, spec: ModelSpec) -> object:  # noqa: ANN401
    """Return the latest snapshot date in a population DataFrame (the natural ``as_of`` of a prediction run)."""
    from pyspark.sql import functions as f  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    return population.agg(f.max(spec.snapshot)).collect()[0][0]
