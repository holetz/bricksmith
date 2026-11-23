"""The ``feature_table`` declaration of a task, the closing calendar and feature lookups (standard library only).

A feature table holds one row per entity and closing. Its primary key is the entity keys plus a DATE
closing column; rows are stamped at the closing at which their source became available
(``availability_lag`` periods after the closing they describe), so a training set joins features by
exact closing equality and never needs lag arithmetic at read time.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import FeatureStoreError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

METADATA_KEY = "feature_table"
DAILY = "daily"
WEEKLY = "weekly"
MONTHLY = "monthly"
QUARTERLY = "quarterly"
SEMIANNUAL = "semiannual"
ANNUAL = "annual"
#: Month-based frequencies and the number of months in one period; closings are the last day of the period.
MONTHS = {MONTHLY: 1, QUARTERLY: 3, SEMIANNUAL: 6, ANNUAL: 12}
#: Day-based frequencies and the number of days in one period.
DAYS = {DAILY: 1, WEEKLY: 7}
FREQUENCIES = (DAILY, WEEKLY, MONTHLY, QUARTERLY, SEMIANNUAL, ANNUAL)
DOUBLE = "double"
STRING = "string"
#: Accepted feature column types: numeric as DOUBLE, categorical as STRING, plus documented arrays and maps.
FEATURE_TYPES = (DOUBLE, STRING, "array<double>", "array<string>", "map<string,double>", "map<string,string>")
WRITE_STRATEGY = "replace_partitions"
KEYS = frozenset({"entity_keys", "closing", "frequency", "availability_lag", "features", "closing_key", "output"})
FEATURE_KEYS = frozenset({"description", "type", "owner", "default", "order"})
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _fail(message: str) -> FeatureStoreError:
    return FeatureStoreError(f"feature_table: {message}")


def _ident(value: object, what: str) -> str:
    if isinstance(value, str) and _IDENT.fullmatch(value):
        return value
    msg = f"{what} must be an identifier, got {value!r}"
    raise _fail(msg)


def _text(value: object, what: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    msg = f"{what} must be non-empty text, got {value!r}"
    raise _fail(msg)


def normalize_type(value: str) -> str:
    """Return the canonical spelling of a feature type (lower case, no spaces)."""
    return value.strip().lower().replace(" ", "")


def is_ordered(feature_type: str) -> bool:
    """Whether a type stores several values whose order or keys must be documented (arrays and maps)."""
    return feature_type.startswith(("array<", "map<"))


# --- the closing calendar --------------------------------------------------------------------------------


def to_day(value: object, what: str = "closing") -> date:
    """Return ``value`` as a date; accepts dates, datetimes and ISO ``YYYY-MM-DD`` strings."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    msg = f"{what} {value!r} is not a date (use YYYY-MM-DD)"
    raise FeatureStoreError(msg)


def _check_frequency(frequency: str) -> None:
    if frequency not in FREQUENCIES:
        msg = f"frequency must be one of {', '.join(FREQUENCIES)}, got {frequency!r}"
        raise _fail(msg)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def is_closing(day: date, frequency: str) -> bool:
    """Whether ``day`` is a closing of ``frequency``: the last day of its period (any day for day-based ones)."""
    _check_frequency(frequency)
    step = MONTHS.get(frequency)
    if step is None:
        return True
    return day == _month_end(day.year, day.month) and day.month % step == 0


def require_closing(day: date, frequency: str, what: str = "closing") -> date:
    """Return ``day`` when it is a closing of ``frequency``; refuse it otherwise (never snapped)."""
    if not is_closing(day, frequency):
        msg = f"{what} {day.isoformat()} is not a {frequency} closing (the last day of its period)"
        raise FeatureStoreError(msg)
    return day


def shift(day: date, periods: int, frequency: str) -> date:
    """Return the closing ``periods`` periods after ``day`` (before it when negative)."""
    require_closing(day, frequency)
    step = MONTHS.get(frequency)
    if step is None:
        return day + timedelta(days=DAYS[frequency] * periods)
    index = day.year * 12 + day.month - 1 + periods * step
    year, month = divmod(index, 12)
    return _month_end(year, month + 1)


# --- the declaration -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Feature:
    """One feature column: its type, meaning and owner, the value used for a missing entity and the order."""

    name: str
    type: str
    description: str
    owner: str
    default: Any = None
    order: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "default": self.default,
            "description": self.description,
            "name": self.name,
            "order": self.order,
            "owner": self.owner,
            "type": self.type,
        }


@dataclass(frozen=True)
class FeatureTable:
    """A validated feature table declaration."""

    entity_keys: tuple[str, ...]
    features: tuple[Feature, ...]
    closing: str = "closing_date"
    frequency: str = MONTHLY
    availability_lag: int = 0
    closing_key: str = ""
    output: str = ""

    @property
    def primary_key(self) -> tuple[str, ...]:
        """The entity keys followed by the closing column."""
        return (*self.entity_keys, self.closing)

    @property
    def feature_names(self) -> tuple[str, ...]:
        """The declared feature names, in declaration order."""
        return tuple(f.name for f in self.features)

    def feature(self, name: str) -> Feature:
        """Return the declared feature ``name``."""
        for item in self.features:
            if item.name == name:
                return item
        msg = f"feature {name!r} is not declared (declared: {', '.join(self.feature_names)})"
        raise FeatureStoreError(msg)

    def write_options(self) -> dict[str, object]:
        """Return the write metadata this declaration implies: replace by closing, keyed by the primary key."""
        return {"write": WRITE_STRATEGY, "partition_by": [self.closing], "keys": list(self.primary_key)}

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "availability_lag": self.availability_lag,
            "closing": self.closing,
            "closing_key": self.closing_key,
            "entity_keys": list(self.entity_keys),
            "features": [f.to_dict() for f in self.features],
            "frequency": self.frequency,
            "output": self.output,
        }


def _default(value: object, feature_type: str, what: str) -> object:
    if value is None:
        return None
    if feature_type == DOUBLE and isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if feature_type == STRING and isinstance(value, str):
        return value
    if is_ordered(feature_type):
        msg = f"{what}: a default is supported only for double and string features"
        raise _fail(msg)
    msg = f"{what}: default {value!r} does not match type {feature_type}"
    raise _fail(msg)


def _feature(name: object, raw: object) -> Feature:
    what = f"feature {name!r}"
    checked = _ident(name, "feature name")
    if not isinstance(raw, dict):
        msg = f"{what} must be a mapping with description, type and owner"
        raise _fail(msg)
    unknown = sorted(set(raw) - FEATURE_KEYS)
    if unknown:
        msg = f"{what} has unknown keys: {', '.join(unknown)}"
        raise _fail(msg)
    if not isinstance(raw.get("type"), str):
        msg = f"{what} needs a type ({', '.join(FEATURE_TYPES)})"
        raise _fail(msg)
    feature_type = normalize_type(raw["type"])
    if feature_type not in FEATURE_TYPES:
        msg = f"{what} has type {raw['type']!r}; use one of {', '.join(FEATURE_TYPES)} (vectors are never stored)"
        raise _fail(msg)
    order = raw.get("order", "")
    if is_ordered(feature_type):
        order = _text(order, f"{what} order (what each position or key means)")
    elif order:
        msg = f"{what}: order applies only to arrays and maps"
        raise _fail(msg)
    return Feature(
        name=checked,
        type=feature_type,
        description=_text(raw.get("description"), f"{what} description"),
        owner=_text(raw.get("owner"), f"{what} owner"),
        default=_default(raw.get("default"), feature_type, what),
        order=str(order),
    )


def _entity_keys(value: object) -> tuple[str, ...]:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items:
        msg = "entity_keys must be a non-empty list of column names"
        raise _fail(msg)
    keys = tuple(_ident(item, "entity key") for item in items)
    if len(set(keys)) != len(keys):
        msg = f"entity_keys repeat a column: {list(keys)!r}"
        raise _fail(msg)
    return keys


def parse_feature_table(value: object) -> FeatureTable:
    """Validate the ``feature_table`` metadata value into a :class:`FeatureTable`."""
    if not isinstance(value, dict):
        msg = "the declaration must be a mapping"
        raise _fail(msg)
    unknown = sorted(set(value) - KEYS)
    if unknown:
        msg = f"unknown keys: {', '.join(unknown)}"
        raise _fail(msg)
    entity_keys = _entity_keys(value.get("entity_keys"))
    closing = _ident(value.get("closing", "closing_date"), "closing")
    frequency = value.get("frequency", MONTHLY)
    if not isinstance(frequency, str):
        msg = f"frequency must be text, got {frequency!r}"
        raise _fail(msg)
    _check_frequency(frequency)
    lag = value.get("availability_lag", 0)
    if not isinstance(lag, int) or isinstance(lag, bool) or lag < 0:
        msg = f"availability_lag must be a whole number of periods >= 0, got {lag!r}"
        raise _fail(msg)
    raw_features = value.get("features")
    if not isinstance(raw_features, dict) or not raw_features:
        msg = "features must be a non-empty mapping of name to {description, type, owner}"
        raise _fail(msg)
    features = tuple(_feature(name, raw) for name, raw in raw_features.items())
    closing_key = _ident(value["closing_key"], "closing_key") if value.get("closing_key") else ""
    output = _text(value["output"], "output") if value.get("output") else ""
    taken = [*entity_keys, closing, *(f.name for f in features), *([closing_key] if closing_key else [])]
    if len(set(taken)) != len(taken):
        repeated = sorted({name for name in taken if taken.count(name) > 1})
        msg = f"a column is declared twice across keys, closing and features: {', '.join(repeated)}"
        raise _fail(msg)
    return FeatureTable(entity_keys, features, closing, frequency, lag, closing_key, output)


def feature_table_of(metadata: Mapping[str, object]) -> FeatureTable | None:
    """Return the declaration in a task's ``metadata``, or ``None`` when it declares none."""
    if METADATA_KEY not in metadata:
        return None
    return parse_feature_table(metadata[METADATA_KEY])


def declared_output(definition: FeatureTable, outputs: Sequence[str]) -> str:
    """Return the task output the declaration describes: ``output`` when declared, else the only output."""
    if definition.output:
        found = [o for o in outputs if definition.output in (o, o.rsplit(".", 1)[-1])]
        if len(found) != 1:
            msg = f"output {definition.output!r} must name exactly one task output ({', '.join(outputs) or 'none'})"
            raise _fail(msg)
        return found[0]
    if len(outputs) != 1:
        msg = f"the task has several outputs ({', '.join(outputs)}); declare output"
        raise _fail(msg)
    return outputs[0]


def write_options(metadata: Mapping[str, object]) -> dict[str, object]:
    """Return ``metadata`` with the write options a feature table implies; a conflicting declaration is refused.

    Absent ``write``, ``partition_by`` and ``keys`` are derived (replace by closing, keyed by the primary
    key); a declared value must equal the derived one.
    """
    definition = parse_feature_table(metadata[METADATA_KEY])
    merged = dict(metadata)
    for key, expected in definition.write_options().items():
        if key not in merged:
            merged[key] = expected
        elif merged[key] != expected:
            msg = f"metadata {key}={merged[key]!r} conflicts with the feature table ({key}={expected!r})"
            raise _fail(msg)
    return merged


# --- lookups ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Lookup:
    """Features read from one feature table for each spine row.

    ``keys`` are the spine columns matching the table's entity keys, in order (default: the same names).
    The feature closing is the observation closing moved back ``offset`` periods; a negative offset
    would read a closing after the observation and is refused. ``prefix`` is prepended to output names.
    """

    table: str
    features: tuple[str, ...] = ()
    keys: tuple[str, ...] = ()
    offset: int = 0
    prefix: str = ""

    def __post_init__(self) -> None:
        """Refuse a lookup that could read the future or names nothing."""
        _text(self.table, "lookup table")
        if not isinstance(self.offset, int) or isinstance(self.offset, bool):
            msg = f"lookup {self.table}: offset must be a whole number of periods, got {self.offset!r}"
            raise FeatureStoreError(msg)
        if self.offset < 0:
            msg = (
                f"lookup {self.table}: offset {self.offset} would read a feature closing after the observation "
                "closing; offsets are periods back and must be >= 0"
            )
            raise FeatureStoreError(msg)
        if self.prefix and not _IDENT.fullmatch(self.prefix + "x"):
            msg = f"lookup {self.table}: prefix {self.prefix!r} must start an identifier"
            raise FeatureStoreError(msg)

    def spine_keys(self, definition: FeatureTable) -> tuple[str, ...]:
        """Return the spine columns matched with the table's entity keys."""
        keys = self.keys or definition.entity_keys
        if len(keys) != len(definition.entity_keys):
            msg = f"lookup {self.table}: keys {list(keys)!r} must match entity keys {list(definition.entity_keys)!r}"
            raise FeatureStoreError(msg)
        return keys

    def selected(self, definition: FeatureTable) -> tuple[Feature, ...]:
        """Return the declared features this lookup reads (all of them when none are named)."""
        names = self.features or definition.feature_names
        return tuple(definition.feature(name) for name in names)

    def output_names(self, definition: FeatureTable) -> dict[str, str]:
        """Return ``feature -> output column`` for the selected features."""
        return {f.name: f"{self.prefix}{f.name}" for f in self.selected(definition)}


def plan_columns(
    spine_columns: Sequence[str], lookups: Sequence[Lookup], definitions: Mapping[str, FeatureTable]
) -> list[str]:
    """Return the output columns of a training set; refuse a column produced twice or clashing with the spine."""
    taken = list(spine_columns)
    for lookup in lookups:
        if lookup.table not in definitions:
            msg = f"lookup {lookup.table}: no feature table definition was given for it"
            raise FeatureStoreError(msg)
        definition = definitions[lookup.table]
        missing = [k for k in lookup.spine_keys(definition) if k not in spine_columns]
        if missing:
            msg = f"lookup {lookup.table}: the spine has no column {', '.join(missing)}"
            raise FeatureStoreError(msg)
        for output in lookup.output_names(definition).values():
            if output in taken:
                msg = f"lookup {lookup.table}: column {output!r} is produced twice; set a prefix or select features"
                raise FeatureStoreError(msg)
            taken.append(output)
    return taken
