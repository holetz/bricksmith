# ruff: noqa: FBT003 - Spark literals take a positional bool
"""The Spark engine of model development stages: the row engine's semantics over DataFrames.

``pyspark`` is imported only inside the functions. Only built-in functions are used (no Python UDF), so the
engine runs where Python UDFs are refused. Seeded positions use the row engine's hash: the first 32 bits of
``sha256("<seed>:<entity keys and closing joined by |>")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import DatasetError, DatasetValidationError
from bricksmith.mlops.featurestore.spark import build_training_set, is_closing_column, shift_column
from bricksmith.mlops.spark import predicate_column
from bricksmith.mlops.stages.config import (
    CASCADE_COLUMNS,
    EXCLUDED,
    EXCLUSION_FIRST,
    EXCLUSION_RULES,
    GRADE,
    MODEL_ALIAS,
    MODEL_NAME,
    MODEL_VERSION,
    PREDICTION_COLUMNS,
    PURPOSES,
    SAMPLE,
    SAMPLE_ROLE,
    SCORE,
    TEST,
    TRAINING,
    Development,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyspark.sql import Column, DataFrame, SparkSession  # pyright: ignore[reportMissingImports]

    from bricksmith.mlops.featurestore.config import FeatureTable
    from bricksmith.mlops.featurestore.rows import TrainingSet

_EXAMPLES = 3
_INTEGRAL = ("tinyint", "smallint", "int", "bigint")


def _f() -> Any:  # noqa: ANN401 - the pyspark functions module
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration, loaded where used  # pyright: ignore[reportMissingImports]

    return f


def _day(development: Development) -> Column:
    return _f().col(development.closing).cast("date")


def _key_text(development: Development) -> Column:
    f = _f()
    parts = [f.col(k).cast("string") for k in development.entity_keys] + [_day(development).cast("string")]
    return f.concat_ws("|", *[f.coalesce(p, f.lit("")) for p in parts])


def position_column(development: Development, seed: int) -> Column:
    """Return the row's seeded position in [0, 1) over its entity keys and closing (the row engine's ``position``)."""
    f = _f()
    digest = f.sha2(f.concat(f.lit(f"{seed}:"), _key_text(development)), 256)
    return f.conv(f.substring(digest, 1, 8), 16, 10).cast("bigint") / f.lit(4294967296.0)


def _examples(frame: DataFrame, development: Development) -> str:
    rows = frame.select(_key_text(development).alias("k")).limit(_EXAMPLES).collect()
    return ", ".join(r["k"] for r in rows)


def _aligned(frame: DataFrame, development: Development) -> DataFrame:
    """Return ``frame`` with a DATE closing; refuse a missing column or a closing off the frequency."""
    if development.closing not in frame.columns:
        msg = f"a row has no closing column {development.closing!r}"
        raise DatasetError(msg)
    frame = frame.withColumn(development.closing, _day(development))
    day = _f().col(development.closing)
    bad = frame.where(day.isNotNull() & ~is_closing_column(day, development.frequency)).limit(1).collect()
    if bad:
        value = bad[0][development.closing]
        msg = f"{development.closing} {value} is not a {development.frequency} closing (the last day of its period)"
        raise DatasetError(msg)
    return frame


def apply_cascade(frame: DataFrame, development: Development) -> DataFrame:
    """Add ``exclusion_rules``, ``exclusion_first`` and ``excluded`` to every row (see the row engine)."""
    f = _f()
    hits = [f.when(predicate_column(rule.where), f.lit(rule.name)) for rule in development.exclusions]
    if hits:
        met = f.filter(f.array(*hits), lambda name: name.isNotNull())
        first = f.coalesce(*hits)
    else:
        met = f.array().cast("array<string>")
        first = f.lit(None).cast("string")
    return (
        frame.withColumn(EXCLUSION_RULES, met)
        .withColumn(EXCLUSION_FIRST, first)
        .withColumn(EXCLUDED, f.size(f.col(EXCLUSION_RULES)) > f.lit(0))
    )


def _with_cascade(frame: DataFrame, development: Development) -> DataFrame:
    if all(c in frame.columns for c in CASCADE_COLUMNS):
        return frame
    return apply_cascade(frame, development)


def composition(frame: DataFrame, development: Development, *, by: Sequence[str] = ()) -> DataFrame:
    """Return the exclusion composition: one row per group, closing and rule (see the row engine)."""
    f = _f()
    cascaded = _aligned(_with_cascade(frame, development), development)
    aggregates = [f.count(f.lit(1)).alias("total"), f.sum(f.col(EXCLUDED).cast("bigint")).alias("excluded")]
    for index, rule in enumerate(development.exclusions):
        name = f.lit(rule.name)
        met = f.when(f.array_contains(f.col(EXCLUSION_RULES), name), 1).otherwise(0)
        first = f.when(f.col(EXCLUSION_FIRST) == name, 1).otherwise(0)
        aggregates += [f.sum(met).cast("bigint").alias(f"__met{index}"),
                       f.sum(first).cast("bigint").alias(f"__first{index}")]  # fmt: skip
    grouped = cascaded.groupBy(*by, development.closing).agg(*aggregates)
    if not development.exclusions:
        empty = [f.lit(None).cast(t).alias(n) for n, t in (("rule", "string"), ("met", "bigint"), ("first", "bigint"))]
        return grouped.select(*by, development.closing, *empty[:1], "total", "excluded", *empty[1:]).where(f.lit(False))
    parts = [
        f.struct(f.lit(index).alias("pos"), f.lit(rule.name).alias("rule"), f.col(f"__met{index}").alias("met"),
                 f.col(f"__first{index}").alias("first"))
        for index, rule in enumerate(development.exclusions)
    ]  # fmt: skip
    stacked = grouped.select(*by, development.closing, "total", "excluded", f.explode(f.array(*parts)).alias("r"))
    return stacked.orderBy(*by, development.closing, "r.pos").select(
        *by,
        development.closing,
        f.col("r.rule").alias("rule"),
        "total",
        "excluded",
        f.col("r.met").alias("met"),
        f.col("r.first").alias("first"),
        (f.col("r.first") / f.col("total")).alias("first_share"),
    )


def select_sample(frame: DataFrame, development: Development, sample: str) -> DataFrame:
    """Return the rows of the sample's window, tagged with its name and role (see the row engine)."""
    f = _f()
    window = development.sample(sample)
    frame = _aligned(frame, development)
    day = f.col(development.closing)
    keep = day.between(f.lit(window.start), f.lit(window.end))
    shared = development.shared(window)
    if development.split is not None and shared:
        in_shared = f.lit(False)
        for other in shared:
            in_shared = in_shared | day.between(f.lit(other.start), f.lit(other.end))
        drawn = position_column(development, development.split.seed) < f.lit(development.split.test_fraction)
        keep = keep & (~in_shared | (drawn if window.role == TEST else ~drawn))
    return frame.where(keep).withColumn(SAMPLE, f.lit(window.name)).withColumn(SAMPLE_ROLE, f.lit(window.role))


def _label_text(frame: DataFrame, name: str) -> Column:
    """Return the label as the text that keys ``stratify.fractions`` (the row engine's ``label_text``)."""
    f = _f()
    kind = dict(frame.dtypes).get(name, "string")
    column = f.col(name)
    if kind == "boolean":
        return f.when(column, f.lit("1")).when(~column, f.lit("0"))
    if kind in _INTEGRAL:
        return column.cast("string")
    if kind in ("float", "double") or kind.startswith("decimal"):
        whole = column == f.floor(column)
        return f.when(whole, column.cast("bigint").cast("string")).otherwise(column.cast("string"))
    return column.cast("string")


def stratify(frame: DataFrame, development: Development) -> DataFrame:
    """Keep a row when its stratification position is below its label value's fraction (see the row engine)."""
    f = _f()
    plan, label = development.stratify, development.label
    if plan is None or label is None:
        return frame
    text = _label_text(frame, label.name)
    fraction = f.lit(1.0)
    for value, share in sorted(plan.fractions.items()):
        fraction = f.when(text == f.lit(value), f.lit(share)).otherwise(fraction)
    return frame.where(position_column(development, plan.seed) < fraction)


def attach_labels(frame: DataFrame, development: Development, labels: DataFrame | None = None) -> DataFrame:
    """Return the rows with their label; refuse an immature, missing or duplicated label (see the row engine)."""
    f = _f()
    label, as_of = development.label, development.as_of
    if label is None or as_of is None:
        msg = "attaching labels needs a declared label and as_of"
        raise DatasetError(msg)
    frame = _aligned(frame, development)
    if labels is not None:
        keys = list(development.spine_keys)
        label_frame = _aligned(labels, development).select(*keys, label.name)
        repeated = label_frame.groupBy(*keys).count().where(f.col("count") > 1).limit(1).collect()
        if repeated:
            msg = f"label rows repeat the key {tuple(repeated[0][k] for k in keys)!r}"
            raise DatasetValidationError(msg)
        kept = [c for c in frame.columns if c != label.name]
        frame = frame.select(*kept).join(label_frame, keys, "left").select(*kept, label.name)
    elif label.name not in frame.columns:
        frame = frame.withColumn(label.name, f.lit(None))
    late = frame.where(shift_column(f.col(development.closing), label.horizon, development.frequency) > f.lit(as_of))
    count = late.count()
    if count:
        msg = (
            f"{count} row(s) have a label that matures after as_of {as_of.isoformat()} "
            f"({label.horizon} periods), e.g. {_examples(late, development)}"
        )
        raise DatasetValidationError(msg)
    absent = frame.where(f.col(label.name).isNull())
    count = absent.count()
    if count:
        msg = f"{count} row(s) have no {label.name!r} label, e.g. {_examples(absent, development)}"
        raise DatasetValidationError(msg)
    return frame


def build_abt(  # noqa: PLR0913 - the sample, its purpose and the inputs of the feature store builder
    population: DataFrame,
    development: Development,
    sample: str,
    *,
    purpose: str = TRAINING,
    definitions: Mapping[str, FeatureTable] | None = None,
    spark: SparkSession | None = None,
    frames: Mapping[str, DataFrame] | None = None,
    labels: DataFrame | None = None,
) -> TrainingSet:
    """Build the ABT of one sample over DataFrames (see ``rows.build_abt``); feature tables as in the feature store."""
    f = _f()
    if purpose not in PURPOSES:
        msg = f"purpose must be one of {', '.join(PURPOSES)}, got {purpose!r}"
        raise DatasetError(msg)
    window = development.sample(sample)
    rows = select_sample(_with_cascade(population, development), development, sample)
    if purpose == TRAINING:
        if not window.labelled:
            msg = f"sample {sample!r} has role {window.role!r}, which carries no label; build it for scoring"
            raise DatasetError(msg)
        rows = stratify(attach_labels(rows.where(~f.col(EXCLUDED)), development, labels), development)
    return build_training_set(
        rows, development.lookups, definitions or {}, spark=spark, frames=frames, closing=development.closing
    )


def _grade(scored: DataFrame, development: Development, score: Column) -> Column:
    f = _f()
    if not development.bands:
        return f.col(GRADE).cast("string") if GRADE in scored.columns else f.lit(None).cast("string")
    tests = [
        (score.isNotNull() if b.upper is None else score <= f.lit(b.upper), f.lit(b.name)) for b in development.bands
    ]
    out = f.when(*tests[0])
    for test, name in tests[1:]:
        out = out.when(test, name)
    return out


def prediction_frame(
    scored: DataFrame,
    development: Development,
    *,
    model_version: str | int,
    model_alias: str | None = None,
    score: str = SCORE,
) -> DataFrame:
    """Return a scored ABT in the prediction contract that model monitoring reads (see ``rows.prediction_rows``)."""
    f = _f()
    missing = [c for c in (score, SAMPLE, SAMPLE_ROLE, *CASCADE_COLUMNS) if c not in scored.columns]
    if missing:
        msg = f"a scored row misses {', '.join(missing)}; score rows of an ABT built by build_abt"
        raise DatasetError(msg)
    value = f.col(score).cast("double")
    out = _aligned(scored, development).select(
        *development.entity_keys,
        development.closing,
        f.lit(development.model).alias(MODEL_NAME),
        f.lit(str(model_version)).alias(MODEL_VERSION),
        f.lit(model_alias).cast("string").alias(MODEL_ALIAS),
        value.alias(SCORE),
        _grade(scored, development, value).alias(GRADE),
        SAMPLE,
        SAMPLE_ROLE,
        EXCLUSION_RULES,
        EXCLUSION_FIRST,
        f.col(EXCLUDED).cast("boolean").alias(EXCLUDED),
    )
    return check_prediction_frame(out, development)


def check_prediction_frame(frame: DataFrame, development: Development) -> DataFrame:
    """Return ``frame`` when it holds the prediction contract (see ``rows.check_prediction``)."""
    f = _f()
    missing = [c for c in (*development.spine_keys, *PREDICTION_COLUMNS) if c not in frame.columns]
    if missing:
        msg = f"prediction rows miss contract columns: {', '.join(missing)}"
        raise DatasetValidationError(msg)
    frame = _aligned(frame, development)
    required = (*development.spine_keys, MODEL_NAME, MODEL_VERSION, SCORE)
    nulls = frame.select(*[f.sum(f.col(c).isNull().cast("int")).alias(c) for c in required]).collect()[0]
    found = [c for c in required if nulls[c]]
    if found:
        msg = f"prediction rows have null {', '.join(found)}"
        raise DatasetValidationError(msg)
    keys = [*development.spine_keys, MODEL_VERSION]
    repeated = frame.groupBy(*keys).count().where(f.col("count") > 1).limit(1).collect()
    if repeated:
        msg = f"prediction rows repeat entity, closing and model version {tuple(repeated[0][k] for k in keys)!r}"
        raise DatasetValidationError(msg)
    return frame
