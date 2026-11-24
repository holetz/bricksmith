"""The row engine of the feature store: availability stamping, load checks and the exact-closing training set.

Rows are plain mappings; closings may be dates or ISO strings and come back as dates. This engine is the
reference the Spark engine is proved against.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import FeatureStoreError
from bricksmith.mlops.featurestore.config import (
    DOUBLE,
    STRING,
    Feature,
    FeatureTable,
    Lookup,
    plan_columns,
    require_closing,
    shift,
    to_day,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

type Row = dict[str, Any]
_SAMPLE = 5


@dataclass(frozen=True)
class TrainingSet:
    """A built training (or scoring) set and what it read.

    ``reads`` maps each feature table read to its Delta version (Spark engine, when read from the
    catalog), a fingerprint of its rows (row engine) or ``None`` when no version is known.
    """

    data: Any
    reads: Mapping[str, str | int | None] = field(default_factory=dict)


def stamp_availability(
    rows: Iterable[Mapping[str, Any]], definition: FeatureTable, *, described: str = ""
) -> list[Row]:
    """Stamp each row at the closing at which it became available: ``described`` moved by the lag.

    ``described`` is the column holding the closing the row describes (default: the closing column).
    It is replaced by the closing column, so a stored row never carries the closing it describes.
    """
    source = described or definition.closing
    stamped: list[Row] = []
    for row in rows:
        if source not in row:
            msg = f"stamp_availability: a row has no column {source!r}"
            raise FeatureStoreError(msg)
        out = {k: v for k, v in row.items() if k != source}
        day = require_closing(to_day(row[source], source), definition.frequency, source)
        out[definition.closing] = shift(day, definition.availability_lag, definition.frequency)
        stamped.append(out)
    return stamped


def _type_ok(value: object, feature: Feature) -> bool:
    if value is None:
        return True
    if feature.type == DOUBLE:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if feature.type == STRING:
        return isinstance(value, str)
    if feature.type.startswith("array<"):
        inner = feature.type[len("array<") : -1]
        return isinstance(value, (list, tuple)) and all(_type_ok(v, Feature("", inner, "", "")) for v in value)
    if isinstance(value, dict):
        inner = feature.type.split(",", 1)[1][:-1]
        return all(isinstance(k, str) and _type_ok(v, Feature("", inner, "", "")) for k, v in value.items())
    return False


def check_rows(rows: Iterable[Mapping[str, Any]], definition: FeatureTable) -> list[Row]:
    """Return the rows with dates for closings; refuse what would break the primary key or the declaration.

    Refused: a missing declared column, a null key, a closing not aligned to the frequency, a value whose
    type disagrees with the declaration, and a duplicated primary key.
    """
    checked: list[Row] = []
    seen: dict[tuple[object, ...], int] = {}
    for row in rows:
        missing = [c for c in (*definition.primary_key, *definition.feature_names) if c not in row]
        if missing:
            msg = f"feature table rows miss declared columns: {', '.join(missing)}"
            raise FeatureStoreError(msg)
        if any(row[k] is None for k in definition.primary_key):
            msg = f"a primary key column is null in {dict(row)!r}"
            raise FeatureStoreError(msg)
        out = dict(row)
        out[definition.closing] = require_closing(
            to_day(row[definition.closing]), definition.frequency, definition.closing
        )
        for feature in definition.features:
            if not _type_ok(out[feature.name], feature):
                msg = f"feature {feature.name} holds {out[feature.name]!r}, which is not {feature.type}"
                raise FeatureStoreError(msg)
        key = tuple(out[k] for k in definition.primary_key)
        seen[key] = seen.get(key, 0) + 1
        checked.append(out)
    duplicated = [k for k, n in seen.items() if n > 1]
    if duplicated:
        sample = ", ".join(repr(k) for k in duplicated[:_SAMPLE])
        msg = f"{len(duplicated)} primary key(s) {definition.primary_key} appear more than once, e.g. {sample}"
        raise FeatureStoreError(msg)
    return checked


def _canonical(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items())}
    return value


def fingerprint_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    """Return a SHA-256 of the rows independent of row and key order; any value change changes it."""
    lines = sorted(json.dumps(_canonical(dict(r)), sort_keys=True, default=str) for r in rows)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def build_training_set(
    spine: Iterable[Mapping[str, Any]],
    lookups: Sequence[Lookup],
    definitions: Mapping[str, FeatureTable],
    data: Mapping[str, Iterable[Mapping[str, Any]]],
    *,
    closing: str = "closing_date",
) -> TrainingSet:
    """Join features to a spine by entity keys and exact closing; the same call serves training and scoring.

    ``spine`` rows carry entity keys, the observation closing (column ``closing``) and anything else
    (a label, weights), which is kept. For each lookup the feature closing is the observation closing moved
    back ``offset`` periods; a missing entity row yields each feature's declared default (else ``None``).
    Feature tables are checked as at load, so a duplicated primary key is refused here too.
    """
    spine_rows = [dict(r) for r in spine]
    columns = list(dict.fromkeys(c for r in spine_rows for c in r))
    if spine_rows and closing not in columns:
        msg = f"the spine has no observation closing column {closing!r}"
        raise FeatureStoreError(msg)
    if spine_rows:
        plan_columns(columns, lookups, definitions)
    indexes: dict[str, dict[tuple[object, ...], Row]] = {}
    reads: dict[str, str | int | None] = {}
    for lookup in lookups:
        if lookup.table in indexes:
            continue
        if lookup.table not in data or lookup.table not in definitions:
            msg = f"lookup {lookup.table}: no rows or no feature table definition were given for it"
            raise FeatureStoreError(msg)
        definition = definitions[lookup.table]
        rows = check_rows(data[lookup.table], definition)
        indexes[lookup.table] = {tuple(r[k] for k in definition.primary_key): r for r in rows}
        reads[lookup.table] = fingerprint_rows(rows)
    result: list[Row] = []
    for row in spine_rows:
        out = dict(row)
        observed = to_day(row.get(closing), closing)
        out[closing] = observed
        for lookup in lookups:
            definition = definitions[lookup.table]
            wanted = shift(require_closing(observed, definition.frequency, "observation closing"),
                           -lookup.offset, definition.frequency)  # fmt: skip
            key = (*(row[k] for k in lookup.spine_keys(definition)), wanted)
            match = indexes[lookup.table].get(key)
            for feature in lookup.selected(definition):
                name = f"{lookup.prefix}{feature.name}"
                out[name] = feature.default if match is None else match[feature.name]
        result.append(out)
    return TrainingSet(result, reads)
