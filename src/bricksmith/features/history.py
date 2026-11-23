"""The input contract: a long table of entity keys, a closing date and values, read point in time.

Each row is one entity at one closing. The lag between two closings is the number of whole periods between
them (calendar months by default), so a missing closing inside a window is a null at its lag, never compressed
away. Targets are the closings present in the input; with ``as_of``, only that closing, and any row after it is
refused rather than silently dropped.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from bricksmith.features.errors import FeatureError, PointInTimeError
from bricksmith.features.spec import DAY, MONTH, QUARTER, WEEK

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

#: Weeks start on Monday; this Monday is week 0.
WEEK_ORIGIN = date(1970, 1, 5)
DAY_ORIGIN = date(1970, 1, 1)


def to_date(value: object) -> date:
    """Read a closing as a date: a ``date``, a ``datetime`` (its date) or an ISO ``YYYY-MM-DD`` text."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    msg = f"a closing must be a date or an ISO date text, got {value!r}"
    raise FeatureError(msg)


def period_index(closing: date, period: str = MONTH) -> int:
    """Return the whole-period number of ``closing``; the difference of two is the lag between them."""
    month = (closing.year - 1970) * 12 + closing.month - 1
    if period == MONTH:
        return month
    if period == QUARTER:
        return month // 3
    if period == WEEK:
        return (closing - WEEK_ORIGIN).days // 7
    if period == DAY:
        return (closing - DAY_ORIGIN).days
    msg = f"unknown period {period!r}"
    raise FeatureError(msg)


def check_point_in_time(closings: Iterable[date], as_of: date | None) -> None:
    """Refuse any closing after ``as_of``: features at a closing may only see that closing and earlier ones."""
    if as_of is None:
        return
    late = sorted({c for c in closings if c > as_of})
    if late:
        shown = ", ".join(c.isoformat() for c in late[:3])
        msg = f"input holds rows after the closing {as_of.isoformat()} (for example {shown}); filter them first"
        raise PointInTimeError(msg)


def entity_key(row: Mapping[str, Any], keys: tuple[str, ...]) -> tuple[Any, ...]:
    """Return the entity key tuple of a row, refusing a null key."""
    key = tuple(row[k] for k in keys)
    if any(v is None for v in key):
        msg = f"entity keys {list(keys)} cannot be null"
        raise FeatureError(msg)
    return key


def duplicate_error(key: tuple[Any, ...], closing: date) -> FeatureError:
    """Return the error for two rows of one entity at one closing."""
    return FeatureError(f"entity {key!r} has more than one row at the closing {closing.isoformat()}")
