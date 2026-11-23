"""The reduction an engine owes the funnel: counts, class counts and moments over the split samples.

The funnel asks an engine only for aggregates of the variables still in play, and folds them with the pure
statistics in ``metrics``; that shared fold is what makes the row engine and the Spark engine agree. Rows are
split by closing first: development and out-of-time, inclusive ranges compared as ISO text.
"""

from __future__ import annotations

import math
import random
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from bricksmith.mlops.selection.config import BINARY, fail
from bricksmith.mlops.selection.metrics import Moments, rank_moments
from bricksmith.monitor.bins import category_text, is_null

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from bricksmith.mlops.selection.config import SelectionConfig

NUMERIC = "numeric"
CATEGORICAL = "categorical"


def normal(value: object) -> object:
    """Return ``value`` with NaN as ``None`` and decimals as floats, so both engines key counts alike."""
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def is_number(value: object) -> bool:
    """Return whether ``value`` is a real number (booleans are categories)."""
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def target_value(value: object, kind: str) -> float | None:
    """Return the usable target: 1.0/0.0 for a binary target, the number for a continuous one, else ``None``."""
    if is_null(value):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    if kind == BINARY:
        return number if number in (0.0, 1.0) else None
    return number


class Engine(Protocol):
    """What the funnel asks of an engine; every method aggregates, nothing returns per-row data but ``sample``."""

    columns: tuple[str, ...]

    def sizes(self) -> tuple[int, int]:
        """Return the development and out-of-time row counts."""
        ...

    def kinds(self, variables: Sequence[str]) -> dict[str, str]:
        """Return ``numeric`` or ``categorical`` per variable."""
        ...

    def closing_counts(self, variable: str) -> dict[str, dict[Any, int]]:
        """Return ``closing -> value -> rows`` over both samples."""
        ...

    def class_counts(self, variable: str) -> dict[Any, tuple[int, int]]:
        """Return ``value -> (events, non-events)`` over development rows with a binary target."""
        ...

    def target_moments(self, variable: str) -> dict[Any, tuple[float, float, float]]:
        """Return ``category -> (rows, sum y, sum y^2)`` over development rows with a continuous target."""
        ...

    def rank_moments(self, variable: str) -> Moments:
        """Return the moments of ``(rank x, rank y)`` over development rows where both are present."""
        ...

    def pair_moments(self, pairs: Sequence[tuple[str, str]]) -> dict[tuple[str, str], Moments]:
        """Return the Pearson moments of each numeric pair over development rows where both are present."""
        ...

    def sample(self, variables: Sequence[str], *, out_of_time: bool = False) -> list[dict[str, Any]]:
        """Return at most ``sample_rows`` rows (the variables and the target) of one sample."""
        ...


class RowsEngine:
    """The pure engine over a sequence of mappings."""

    def __init__(self, rows: Iterable[Mapping[str, Any]], config: SelectionConfig) -> None:
        """Split ``rows`` by closing into development and out-of-time; rows outside both are ignored."""
        self.config = config
        columns: dict[str, None] = {}
        self.dev: list[tuple[str, Mapping[str, Any]]] = []
        self.oot: list[tuple[str, Mapping[str, Any]]] = []
        for row in rows:
            columns.update(dict.fromkeys(row))
            raw = row.get(config.closing)
            if is_null(raw):
                continue
            closing = str(raw)
            if config.development.contains(closing):
                self.dev.append((closing, row))
            elif config.out_of_time and config.out_of_time.contains(closing):
                self.oot.append((closing, row))
        self.columns = tuple(columns)
        if config.closing not in columns or config.target not in columns:
            fail(f"the input needs the closing column {config.closing!r} and the target {config.target!r}")

    def sizes(self) -> tuple[int, int]:
        """Return the development and out-of-time row counts."""
        return len(self.dev), len(self.oot)

    def kinds(self, variables: Sequence[str]) -> dict[str, str]:
        """Numeric when every present value is a number; declared ``categorical`` variables always categorical."""
        kinds = {}
        for variable in variables:
            values = (normal(r.get(variable)) for _, r in (*self.dev, *self.oot))
            numeric = all(is_number(v) for v in values if v is not None)
            kinds[variable] = NUMERIC if numeric and variable not in self.config.categorical else CATEGORICAL
        return kinds

    def closing_counts(self, variable: str) -> dict[str, dict[Any, int]]:
        """Return ``closing -> value -> rows`` over both samples."""
        counts: dict[str, dict[Any, int]] = {}
        for closing, row in (*self.dev, *self.oot):
            slot = counts.setdefault(closing, {})
            value = normal(row.get(variable))
            slot[value] = slot.get(value, 0) + 1
        return counts

    def _labelled(self) -> Iterable[tuple[Mapping[str, Any], float]]:
        for _, row in self.dev:
            y = target_value(row.get(self.config.target), self.config.target_kind)
            if y is not None:
                yield row, y

    def class_counts(self, variable: str) -> dict[Any, tuple[int, int]]:
        """Return ``value -> (events, non-events)`` over development rows with a binary target."""
        counts: dict[Any, tuple[int, int]] = {}
        for row, y in self._labelled():
            value = normal(row.get(variable))
            events, non_events = counts.get(value, (0, 0))
            counts[value] = (events + (y == 1.0), non_events + (y == 0.0))
        return counts

    def target_moments(self, variable: str) -> dict[Any, tuple[float, float, float]]:
        """Return ``category -> (rows, sum y, sum y^2)`` over development rows with a continuous target."""
        moments: dict[Any, tuple[float, float, float]] = {}
        for row, y in self._labelled():
            value = normal(row.get(variable))
            key = None if value is None else category_text(value)
            n, s, q = moments.get(key, (0.0, 0.0, 0.0))
            moments[key] = (n + 1, s + y, q + y * y)
        return moments

    def rank_moments(self, variable: str) -> Moments:
        """Return the moments of ``(rank x, rank y)`` over development rows where both are present."""
        pairs = []
        for row, y in self._labelled():
            x = normal(row.get(variable))
            if x is not None:
                pairs.append((float(x), y))  # type: ignore[arg-type]
        return rank_moments(pairs)

    def pair_moments(self, pairs: Sequence[tuple[str, str]]) -> dict[tuple[str, str], Moments]:
        """Return the Pearson moments of each numeric pair over development rows where both are present."""
        result = {}
        for a, b in pairs:
            values = ((normal(r.get(a)), normal(r.get(b))) for _, r in self.dev)
            result[a, b] = Moments.of((float(x), float(y)) for x, y in values if x is not None and y is not None)  # type: ignore[arg-type]
        return result

    def sample(self, variables: Sequence[str], *, out_of_time: bool = False) -> list[dict[str, Any]]:
        """Return at most ``sample_rows`` rows of one sample, chosen with the configured seed when there are more."""
        source = self.oot if out_of_time else self.dev
        names = (*variables, self.config.target)
        rows = [{c: normal(r.get(c)) for c in names} for _, r in source]
        if len(rows) > self.config.sample_rows:
            rows = random.Random(self.config.seed).sample(rows, self.config.sample_rows)  # noqa: S311 - sampling, not security
        return rows


__all__ = ["CATEGORICAL", "NUMERIC", "Engine", "RowsEngine", "is_number", "normal", "target_value"]
