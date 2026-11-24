"""Optional Unity Catalog Feature Engineering adapter (extra ``features``), imported only where used.

Feature Engineering never becomes a requirement: the store works with plain Delta tables and the builders
in ``rows`` and ``spark``. The adapter publishes the primary key and builds the client's training set so
``log_model`` records feature lineage. Its join semantics are chosen explicitly with ``mode``:

- ``exact``: the lookup key is the entity keys plus the declared STRING ``closing_key`` (the closing as
  ``YYYY-MM-DD``); the client matches it exactly, like the builders. A DATE key cannot be used for this.
- ``timeseries``: the DATE closing is a TIMESERIES key and the client performs an as-of join (the latest
  closing at or before the wanted one), which is *not* exact when a closing is missing.

Declared feature defaults are not applied on this path: the client returns nulls for missing entities.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import FeatureStoreError
from bricksmith.mlops.featurestore.config import FeatureTable, Lookup, plan_columns

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyspark.sql import Column, DataFrame, SparkSession  # pyright: ignore[reportMissingImports]

EXACT = "exact"
TIMESERIES = "timeseries"
MODES = (EXACT, TIMESERIES)
CLOSING_KEY_FORMAT = "yyyy-MM-dd"
CLIENT_MODULE = "databricks.feature_engineering"


def _client_module() -> Any:  # noqa: ANN401 - the optional client module
    try:
        return importlib.import_module(CLIENT_MODULE)
    except ImportError as error:
        msg = "the Feature Engineering adapter needs the 'features' extra (databricks-feature-engineering)"
        raise FeatureStoreError(msg) from error


def _mode(mode: str) -> str:
    if mode not in MODES:
        msg = f"mode must be one of {', '.join(MODES)} (exact match or as-of join), got {mode!r}"
        raise FeatureStoreError(msg)
    return mode


def _key_columns(definition: FeatureTable, mode: str) -> tuple[str, ...]:
    if _mode(mode) == TIMESERIES:
        return definition.primary_key
    if not definition.closing_key:
        msg = "exact mode needs closing_key: a STRING copy of the closing (a DATE key would be an as-of join)"
        raise FeatureStoreError(msg)
    return (*definition.entity_keys, definition.closing_key)


def _quote(name: str) -> str:
    return ".".join(f"`{part.replace('`', '``')}`" for part in name.split("."))


def primary_key_statements(table: str, definition: FeatureTable, *, mode: str) -> list[str]:
    """Return the SQL that makes the key columns NOT NULL and declares the primary key the client needs."""
    columns = _key_columns(definition, mode)
    statements = [f"ALTER TABLE {_quote(table)} ALTER COLUMN {_quote(c)} SET NOT NULL" for c in columns]
    keys = [_quote(c) for c in columns]
    if mode == TIMESERIES:
        keys[-1] += " TIMESERIES"
    name = _quote(f"{table.rsplit('.', 1)[-1]}_pk")
    statements.append(f"ALTER TABLE {_quote(table)} ADD CONSTRAINT {name} PRIMARY KEY ({', '.join(keys)})")
    return statements


def publish_primary_key(spark: SparkSession, table: str, definition: FeatureTable, *, mode: str) -> list[str]:
    """Run :func:`primary_key_statements` on ``table`` and return what ran."""
    statements = primary_key_statements(table, definition, mode=mode)
    for statement in statements:
        spark.sql(statement)
    return statements


def closing_key(column: Column) -> Column:
    """Return the STRING closing key of a DATE closing column (``YYYY-MM-DD``)."""
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

    return f.date_format(column, CLOSING_KEY_FORMAT)


def with_closing_key(frame: DataFrame, definition: FeatureTable) -> DataFrame:
    """Add the declared STRING ``closing_key`` column, derived from the closing, to a feature table frame."""
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

    _key_columns(definition, EXACT)
    return frame.withColumn(definition.closing_key, closing_key(f.col(definition.closing).cast("date")))


def lookup_column(index: int) -> str:
    """Return the name of the spine helper column carrying the wanted closing of lookup ``index``."""
    return f"__fs{index}_lookup"


def _lookup_value(column: Column, lookup: Lookup, definition: FeatureTable, mode: str) -> Column:
    from bricksmith.mlops.featurestore.spark import shift_column  # noqa: PLC0415 - heavy integration

    wanted = shift_column(column, -lookup.offset, definition.frequency)
    return closing_key(wanted) if mode == EXACT else wanted.cast("timestamp")


def feature_lookups(lookups: Sequence[Lookup], definitions: Mapping[str, FeatureTable], *, mode: str) -> list[Any]:
    """Return one client ``FeatureLookup`` per lookup, keyed on the spine helper columns of the chosen mode."""
    module = _client_module()
    found = []
    for index, lookup in enumerate(lookups):
        definition = definitions[lookup.table]
        _key_columns(definition, mode)
        names = lookup.output_names(definition)
        options: dict[str, Any] = {"table_name": lookup.table, "feature_names": list(names)}
        keys = list(lookup.spine_keys(definition))
        if mode == EXACT:
            options["lookup_key"] = [*keys, lookup_column(index)]
        else:
            options["lookup_key"] = keys
            options["timestamp_lookup_key"] = lookup_column(index)
        renamed = {feature: output for feature, output in names.items() if feature != output}
        if renamed:
            options["rename_outputs"] = renamed
        found.append(module.FeatureLookup(**options))
    return found


def create_training_set(  # noqa: PLR0913 - the builder's arguments plus the client's
    client: Any,  # noqa: ANN401 - a FeatureEngineeringClient
    spine: DataFrame,
    lookups: Sequence[Lookup],
    definitions: Mapping[str, FeatureTable],
    *,
    mode: str,
    label: str | None = None,
    closing: str = "closing_date",
    exclude_columns: Sequence[str] = (),
) -> Any:  # noqa: ANN401 - the client's TrainingSet
    """Build the client's training set (for ``log_model`` lineage) from a spine and closing lookups.

    The spine gets one helper column per lookup with the wanted closing, excluded from the result.
    """
    from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

    _mode(mode)
    if closing not in spine.columns:
        msg = f"the spine has no observation closing column {closing!r}"
        raise FeatureStoreError(msg)
    plan_columns(spine.columns, lookups, definitions)
    observed = f.col(closing).cast("date")
    for index, lookup in enumerate(lookups):
        spine = spine.withColumn(lookup_column(index), _lookup_value(observed, lookup, definitions[lookup.table], mode))
    helpers = [lookup_column(i) for i in range(len(lookups))]
    return client.create_training_set(
        df=spine,
        feature_lookups=feature_lookups(lookups, definitions, mode=mode),
        label=label,
        exclude_columns=[*helpers, *exclude_columns],
    )
