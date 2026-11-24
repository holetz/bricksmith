"""The Spark engine of the feature store: the same semantics as the row engine, over DataFrames.

``pyspark`` is imported only inside the functions. Feature tables read from the catalog are pinned: the
builder reads the Delta version it reports, so the training set and its ``reads`` always agree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import FeatureStoreError
from bricksmith.mlops.featurestore.config import DAYS, MONTHS, FeatureTable, Lookup, plan_columns
from bricksmith.mlops.featurestore.rows import TrainingSet

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyspark.sql import Column, DataFrame, SparkSession  # pyright: ignore[reportMissingImports]

_SAMPLE = 5
_OBSERVED = "__fs_observed"


def _f() -> Any:  # noqa: ANN401 - the pyspark functions module
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration, loaded where used  # pyright: ignore[reportMissingImports]

    return f


def shift_column(column: Column, periods: int, frequency: str) -> Column:
    """Return the closing ``periods`` periods after the closing in ``column`` (the row engine's ``shift``)."""
    f = _f()
    step = MONTHS.get(frequency)
    if step is None:
        return f.date_add(column, DAYS[frequency] * periods)
    return f.last_day(f.add_months(column, step * periods))


def is_closing_column(column: Column, frequency: str) -> Column:
    """Return whether the date in ``column`` is a closing of ``frequency`` (the row engine's ``is_closing``)."""
    f = _f()
    step = MONTHS.get(frequency)
    if step is None:
        return column.isNotNull()
    return (column == f.last_day(column)) & (f.month(column) % f.lit(step) == f.lit(0))


def _unaligned(frame: DataFrame, column: str, frequency: str) -> list[str]:
    f = _f()
    day = f.col(column).cast("date")
    rows = frame.where(day.isNotNull() & ~is_closing_column(day, frequency)).select(day.alias("d")).limit(_SAMPLE)
    return [str(r["d"]) for r in rows.collect()]


def stamp_availability(frame: DataFrame, definition: FeatureTable, *, described: str = "") -> DataFrame:
    """Stamp each row at the closing at which it became available: ``described`` moved by the lag."""
    f = _f()
    source = described or definition.closing
    if source not in frame.columns:
        msg = f"stamp_availability: the frame has no column {source!r}"
        raise FeatureStoreError(msg)
    bad = _unaligned(frame, source, definition.frequency)
    if bad:
        msg = f"{source} {bad[0]} is not a {definition.frequency} closing (the last day of its period)"
        raise FeatureStoreError(msg)
    stamped = shift_column(f.col(source).cast("date"), definition.availability_lag, definition.frequency)
    frame = frame.withColumn(definition.closing, stamped)
    return frame if source == definition.closing else frame.drop(source)


def _spark_type(type_text: str) -> str:
    return type_text.replace(" ", "").lower()


def check_frame(frame: DataFrame, definition: FeatureTable) -> DataFrame:
    """Return ``frame`` with a DATE closing; refuse what would break the primary key or the declaration."""
    f = _f()
    missing = [c for c in (*definition.primary_key, *definition.feature_names) if c not in frame.columns]
    if missing:
        msg = f"feature table rows miss declared columns: {', '.join(missing)}"
        raise FeatureStoreError(msg)
    types = {name: _spark_type(kind) for name, kind in frame.dtypes}
    for feature in definition.features:
        actual = types[feature.name]
        if actual != feature.type and not (feature.type == "double" and actual == "void"):
            msg = f"feature {feature.name} is stored as {actual}, which is not {feature.type}"
            raise FeatureStoreError(msg)
    frame = frame.withColumn(definition.closing, f.col(definition.closing).cast("date"))
    null_any = f.lit(False)  # noqa: FBT003 - a Spark literal, not a flag
    for key in definition.primary_key:
        null_any = null_any | f.col(key).isNull()
    if frame.where(null_any).limit(1).count():
        msg = f"a primary key column {definition.primary_key} is null"
        raise FeatureStoreError(msg)
    bad = _unaligned(frame, definition.closing, definition.frequency)
    if bad:
        msg = f"{definition.closing} {bad[0]} is not a {definition.frequency} closing (the last day of its period)"
        raise FeatureStoreError(msg)
    keys = list(definition.primary_key)
    duplicated = frame.groupBy(*keys).count().where(f.col("count") > 1).limit(_SAMPLE).collect()
    if duplicated:
        sample = ", ".join(repr(tuple(r[k] for k in keys)) for r in duplicated)
        msg = f"primary key(s) {definition.primary_key} appear more than once, e.g. {sample}"
        raise FeatureStoreError(msg)
    return frame


def read_pinned(spark: SparkSession, table: str) -> tuple[DataFrame, int | None]:
    """Read ``table`` at its current Delta version and return the frame with that version (``None``: unversioned)."""
    from bricksmith.storage import current_version  # noqa: PLC0415 - keep the import light
    from bricksmith.storage.sql import quote  # noqa: PLC0415

    version = current_version(spark, table)
    if version is None:
        return spark.table(table), None
    statement = f"SELECT * FROM {quote(table)} VERSION AS OF {int(version)}"  # noqa: S608 - quoted identifier, integer
    return spark.sql(statement), version


def _read_tables(
    lookups: Sequence[Lookup],
    definitions: Mapping[str, FeatureTable],
    spark: SparkSession | None,
    frames: Mapping[str, DataFrame],
) -> tuple[dict[str, DataFrame], dict[str, str | int | None]]:
    tables: dict[str, DataFrame] = {}
    reads: dict[str, str | int | None] = {}
    for lookup in lookups:
        if lookup.table in tables:
            continue
        if lookup.table in frames:
            frame, version = frames[lookup.table], None
        elif spark is not None:
            frame, version = read_pinned(spark, lookup.table)
        else:
            msg = f"lookup {lookup.table}: no frame was given and no Spark session can read it"
            raise FeatureStoreError(msg)
        tables[lookup.table] = check_frame(frame, definitions[lookup.table])
        reads[lookup.table] = version
    return tables, reads


def _join(out: DataFrame, table: DataFrame, lookup: Lookup, definition: FeatureTable, *, tag: str) -> DataFrame:
    """Left-join one lookup's features on entity keys and the wanted closing; apply the declared defaults."""
    f = _f()
    wanted = f"{tag}_closing"
    out = out.withColumn(wanted, shift_column(f.col(_OBSERVED), -lookup.offset, definition.frequency))
    spine_keys = lookup.spine_keys(definition)
    names = lookup.output_names(definition)
    right = table.select(
        *(f.col(key).alias(f"{tag}_k{i}") for i, key in enumerate(definition.entity_keys)),
        f.col(definition.closing).alias(f"{tag}_c"),
        *(f.col(feature).alias(output) for feature, output in names.items()),
    )
    condition = f.col(wanted) == f.col(f"{tag}_c")
    for i, key in enumerate(spine_keys):
        condition = condition & (f.col(key) == f.col(f"{tag}_k{i}"))
    out = out.join(right, condition, "left")
    for feature in lookup.selected(definition):
        if feature.default is not None:
            output = names[feature.name]
            out = out.withColumn(
                output, f.when(f.col(f"{tag}_c").isNull(), f.lit(feature.default)).otherwise(f.col(output))
            )
    return out.drop(wanted, f"{tag}_c", *(f"{tag}_k{i}" for i in range(len(spine_keys))))


def build_training_set(  # noqa: PLR0913 - the row engine's arguments plus where frames come from
    spine: DataFrame,
    lookups: Sequence[Lookup],
    definitions: Mapping[str, FeatureTable],
    *,
    spark: SparkSession | None = None,
    frames: Mapping[str, DataFrame] | None = None,
    closing: str = "closing_date",
) -> TrainingSet:
    """Join features to a spine DataFrame by entity keys and exact closing (see ``rows.build_training_set``).

    A feature table comes from ``frames`` when given there (its ``reads`` entry is then ``None``), else
    it is read from the catalog through ``spark`` at its current Delta version, which ``reads`` reports.
    """
    f = _f()
    if closing not in spine.columns:
        msg = f"the spine has no observation closing column {closing!r}"
        raise FeatureStoreError(msg)
    plan_columns(spine.columns, lookups, definitions)
    tables, reads = _read_tables(lookups, definitions, spark, dict(frames or {}))
    out = spine.withColumn(closing, f.col(closing).cast("date")).withColumn(_OBSERVED, f.col(closing))
    for frequency in sorted({definitions[lookup.table].frequency for lookup in lookups}):
        bad = _unaligned(out, closing, frequency)
        if bad:
            msg = f"observation closing {bad[0]} is not a {frequency} closing (the last day of its period)"
            raise FeatureStoreError(msg)
    for index, lookup in enumerate(lookups):
        out = _join(out, tables[lookup.table], lookup, definitions[lookup.table], tag=f"__fs{index}")
    return TrainingSet(out.drop(_OBSERVED), reads)
