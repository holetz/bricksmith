"""Typed validation of dates, windows, dataset selectors, segments and write options."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING

from bricksmith.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DATASET_PART = re.compile(r"[A-Za-z0-9_*]+")
WRITE_MODES = ("append", "overwrite", "merge", "replace_window")
type Scalar = str | int | float | bool | None


def require_identifier(value: str, what: str) -> str:
    """Return ``value`` when it is a plain identifier, else raise."""
    if not IDENTIFIER.fullmatch(value):
        msg = f"{what} {value!r} is not a valid identifier"
        raise ValidationError(msg)
    return value


def parse_date(value: str | date, what: str = "date") -> date:
    """Parse an ISO ``YYYY-MM-DD`` date."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value)
    except ValueError:
        msg = f"{what} {value!r} is not an ISO date (YYYY-MM-DD)"
        raise ValidationError(msg) from None


@dataclass(frozen=True)
class DateWindow:
    """Inclusive date window."""

    start: date
    end: date

    def __post_init__(self) -> None:
        """Reject inverted windows."""
        if self.start > self.end:
            msg = f"window start {self.start} is after end {self.end}"
            raise ValidationError(msg)

    @property
    def days(self) -> int:
        """Number of days covered, both ends included."""
        return (self.end - self.start).days + 1

    def to_dict(self) -> dict[str, str]:
        """Return the serializable form."""
        return {"end": self.end.isoformat(), "start": self.start.isoformat()}


def parse_window(
    *,
    start: str | date | None = None,
    end: str | date | None = None,
    as_of: str | date | None = None,
    lookback_days: int | None = None,
) -> DateWindow | None:
    """Build a window from exactly one form, or return ``None`` when nothing is given.

    Forms: ``start`` + ``end``; or ``as_of`` alone (one day); or ``as_of`` + ``lookback_days``
    (the window ending at ``as_of`` and covering that many days).
    """
    range_given = start is not None or end is not None
    point_given = as_of is not None or lookback_days is not None
    if range_given and point_given:
        msg = "give either start/end or as_of/lookback_days, not both"
        raise ValidationError(msg)
    if range_given:
        if start is None or end is None:
            msg = "start and end must be given together"
            raise ValidationError(msg)
        return DateWindow(parse_date(start, "start"), parse_date(end, "end"))
    if not point_given:
        return None
    if as_of is None:
        msg = "lookback_days requires as_of"
        raise ValidationError(msg)
    anchor = parse_date(as_of, "as_of")
    days = 1 if lookback_days is None else lookback_days
    if isinstance(days, bool) or days < 1:
        msg = f"lookback_days must be a positive integer, got {lookback_days!r}"
        raise ValidationError(msg)
    return DateWindow(anchor - timedelta(days=days - 1), anchor)


def _check_dataset(ref: str) -> str:
    parts = ref.split(".")
    if len(parts) not in (2, 3) or not all(_DATASET_PART.fullmatch(p) for p in parts):
        msg = f"dataset reference {ref!r} must be schema.table or catalog.schema.table ('*' allowed)"
        raise ValidationError(msg)
    return ref


@dataclass(frozen=True)
class DatasetSelector:
    """Include and exclude dataset references, optionally with ``*`` wildcards per part."""

    include: tuple[str, ...]
    exclude: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, list[str]]:
        """Return the serializable form."""
        return {"exclude": list(self.exclude), "include": list(self.include)}


def parse_selector(include: Iterable[str], exclude: Iterable[str] = ()) -> DatasetSelector:
    """Validate a selector; empty include, duplicates and include/exclude overlap are rejected."""
    inc = tuple(_check_dataset(r) for r in include)
    exc = tuple(_check_dataset(r) for r in exclude)
    if not inc:
        msg = "dataset selector needs at least one included dataset"
        raise ValidationError(msg)
    for group, label in ((inc, "included"), (exc, "excluded")):
        if len(set(group)) != len(group):
            msg = f"duplicate {label} dataset reference"
            raise ValidationError(msg)
    overlap = sorted(set(inc) & set(exc))
    if overlap:
        msg = f"dataset {overlap[0]!r} is both included and excluded"
        raise ValidationError(msg)
    return DatasetSelector(inc, exc)


def parse_segments(items: Iterable[str]) -> Mapping[str, tuple[str, ...]]:
    """Parse ``dimension=a,b`` items; values within one item are alternatives (OR).

    Repeating a dimension is ambiguous (AND or OR) and is rejected.
    """
    result: dict[str, tuple[str, ...]] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        values = tuple(v.strip() for v in raw.split(","))
        if not sep or not all(values):
            msg = f"segment {item!r} must look like dimension=value[,value]"
            raise ValidationError(msg)
        require_identifier(key, "segment dimension")
        if key in result:
            msg = f"segment dimension {key!r} is given more than once"
            raise ValidationError(msg)
        if len(set(values)) != len(values):
            msg = f"segment dimension {key!r} repeats a value"
            raise ValidationError(msg)
        result[key] = values
    return MappingProxyType(dict(sorted(result.items())))


@dataclass(frozen=True)
class WriteOptions:
    """Validated write request; semantics per mode belong to the write contract."""

    mode: str
    merge_keys: tuple[str, ...] = ()
    window: DateWindow | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "merge_keys": list(self.merge_keys),
            "mode": self.mode,
            "window": self.window.to_dict() if self.window else None,
        }


def parse_write_options(
    mode: str,
    *,
    merge_keys: Sequence[str] = (),
    window: DateWindow | None = None,
) -> WriteOptions:
    """Reject option combinations that contradict the chosen mode."""
    if mode not in WRITE_MODES:
        msg = f"write mode {mode!r} must be one of {', '.join(WRITE_MODES)}"
        raise ValidationError(msg)
    keys = tuple(require_identifier(k, "merge key") for k in merge_keys)
    if len(set(keys)) != len(keys):
        msg = "merge keys contain duplicates"
        raise ValidationError(msg)
    if mode == "merge" and not keys:
        msg = "write mode 'merge' requires merge keys"
        raise ValidationError(msg)
    if mode != "merge" and keys:
        msg = f"merge keys are only valid with write mode 'merge', not {mode!r}"
        raise ValidationError(msg)
    if mode == "replace_window" and window is None:
        msg = "write mode 'replace_window' requires a window"
        raise ValidationError(msg)
    if mode != "replace_window" and window is not None:
        msg = f"a window is only valid with write mode 'replace_window', not {mode!r}"
        raise ValidationError(msg)
    return WriteOptions(mode, keys, window)


def validate_parameters(parameters: Mapping[str, Scalar]) -> dict[str, Scalar]:
    """Return a copy of ``parameters`` after checking identifier keys and scalar values."""
    checked: dict[str, Scalar] = {}
    for key, value in parameters.items():
        require_identifier(key, "parameter name")
        if not isinstance(value, str | int | float | bool | None):
            msg = f"parameter {key!r} must be a scalar, got {type(value).__name__}"
            raise ValidationError(msg)
        checked[key] = value
    return checked
