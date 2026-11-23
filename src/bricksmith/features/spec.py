"""The declarative feature spec: source columns x windows x aggregates, plus ratios, differences and category counts.

A window is a number of periods counted from the current closing inclusive (window 3 holds lags 0, 1 and 2).
Window outputs are named ``<column>_<aggregate>_<window>p``; category counts ``<column>_count_<category>_<window>p``;
ratios and differences carry their declared names. Build a spec in Python or from a plain dict (:func:`parse_spec`)
so a task can declare it as a literal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.features.catalog import DOUBLE, INTEGER, aggregate
from bricksmith.features.errors import FeatureError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

MONTH = "month"
QUARTER = "quarter"
WEEK = "week"
DAY = "day"
PERIODS = (MONTH, QUARTER, WEEK, DAY)

WINDOW = "window"
RATIO = "ratio"
DIFFERENCE = "difference"
CATEGORY = "category"

_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")


def _identifier(value: str, what: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):  # pyright: ignore[reportUnnecessaryIsInstance]
        msg = f"{what} must be a lower snake case identifier, got {value!r}"
        raise FeatureError(msg)
    return value


def _windows(values: Iterable[int], what: str) -> tuple[int, ...]:
    windows = tuple(values)
    if not windows or any(isinstance(w, bool) or not isinstance(w, int) or w < 1 for w in windows):
        msg = f"{what} needs at least one window, each a whole number of periods >= 1, got {windows!r}"
        raise FeatureError(msg)
    if len(set(windows)) != len(windows):
        msg = f"{what} repeats a window: {windows!r}"
        raise FeatureError(msg)
    return windows


def category_slug(category: str) -> str:
    """Spell a category value as an identifier fragment (``Credit Card`` -> ``credit_card``)."""
    slug = re.sub(r"[^a-z0-9]+", "_", str(category).lower()).strip("_")
    if not slug:
        msg = f"category {category!r} has no letters or digits to name an output"
        raise FeatureError(msg)
    return slug


def feature_name(column: str, aggregate_name: str, window: int) -> str:
    """Return the output name of a windowed aggregate (``balance_mean_6p``)."""
    return f"{column}_{aggregate_name}_{window}p"


@dataclass(frozen=True)
class WindowFeature:
    """Every ``aggregates`` x ``windows`` combination over every column of ``columns``."""

    columns: tuple[str, ...]
    windows: tuple[int, ...]
    aggregates: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate names, windows and aggregates."""
        object.__setattr__(self, "columns", tuple(_identifier(c, "a feature column") for c in self.columns))
        object.__setattr__(self, "windows", _windows(self.windows, f"columns {list(self.columns)}"))
        object.__setattr__(self, "aggregates", tuple(self.aggregates))
        if not self.columns or not self.aggregates:
            msg = "a window feature needs at least one column and one aggregate"
            raise FeatureError(msg)
        for name in self.aggregates:
            aggregate(name)


@dataclass(frozen=True)
class Ratio:
    """``numerator / denominator`` of two outputs or source columns at the current closing; null on a zero."""

    name: str
    numerator: str
    denominator: str


@dataclass(frozen=True)
class Difference:
    """``left - right`` of two outputs or source columns at the current closing."""

    name: str
    left: str
    right: str


@dataclass(frozen=True)
class CategoricalCount:
    """How many closings in each window had each declared category of a categorical column."""

    column: str
    categories: tuple[str, ...]
    windows: tuple[int, ...]

    def __post_init__(self) -> None:
        """Validate the column, the categories and the windows."""
        _identifier(self.column, "a categorical column")
        object.__setattr__(self, "categories", tuple(str(c) for c in self.categories))
        object.__setattr__(self, "windows", _windows(self.windows, f"categorical column {self.column!r}"))
        if not self.categories or len(set(self.categories)) != len(self.categories):
            msg = f"categorical column {self.column!r} needs distinct categories"
            raise FeatureError(msg)


@dataclass(frozen=True)
class Output:
    """One output column: its name, type and what computes it."""

    name: str
    kind: str
    dtype: str
    column: str = ""
    aggregate: str = ""
    window: int = 0
    category: str = ""
    left: str = ""
    right: str = ""


@dataclass(frozen=True)
class FeatureSpec:
    """Entity ``keys``, the ``closing`` date column, the period unit and every declared feature."""

    keys: tuple[str, ...]
    closing: str
    features: tuple[WindowFeature, ...] = ()
    ratios: tuple[Ratio, ...] = ()
    differences: tuple[Difference, ...] = ()
    categorical: tuple[CategoricalCount, ...] = ()
    period: str = MONTH
    outputs: tuple[Output, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Validate the spec and derive its outputs, refusing duplicate or clashing names."""
        for name in ("keys", "features", "ratios", "differences", "categorical"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not self.keys:
            msg = "a feature spec needs at least one entity key"
            raise FeatureError(msg)
        for key in self.keys:
            _identifier(key, "an entity key")
        _identifier(self.closing, "the closing column")
        if self.period not in PERIODS:
            msg = f"period must be one of {', '.join(PERIODS)}, got {self.period!r}"
            raise FeatureError(msg)
        if not (self.features or self.categorical):
            msg = "a feature spec needs at least one window feature or categorical count"
            raise FeatureError(msg)
        reserved = {*self.keys, self.closing}
        clash = reserved & {*self.value_columns, *self.categorical_columns}
        if clash:
            msg = f"key or closing columns cannot be feature columns: {sorted(clash)}"
            raise FeatureError(msg)
        both = set(self.value_columns) & set(self.categorical_columns)
        if both:
            msg = f"a column cannot be both numeric and categorical: {sorted(both)}"
            raise FeatureError(msg)
        object.__setattr__(self, "outputs", tuple(self._derive(reserved)))

    def _derive(self, reserved: set[str]) -> list[Output]:
        outputs = self._aggregated()
        operands = {o.name for o in outputs} | set(self.value_columns)
        for ratio in self.ratios:
            outputs.append(self._combined(ratio.name, RATIO, ratio.numerator, ratio.denominator, operands))
            operands.add(ratio.name)
        for difference in self.differences:
            outputs.append(self._combined(difference.name, DIFFERENCE, difference.left, difference.right, operands))
            operands.add(difference.name)
        seen: set[str] = set()
        for output in outputs:
            if output.name in seen or output.name in reserved:
                msg = f"output name {output.name!r} is declared twice or clashes with a key"
                raise FeatureError(msg)
            seen.add(output.name)
        return outputs

    def _aggregated(self) -> list[Output]:
        outputs: list[Output] = []
        for feature in self.features:
            for column in feature.columns:
                for window in feature.windows:
                    for name in feature.aggregates:
                        entry = aggregate(name)
                        outputs.append(
                            Output(feature_name(column, name, window), WINDOW, entry.kind, column, name, window)
                        )
        for count in self.categorical:
            for window in count.windows:
                for category in count.categories:
                    name = f"{count.column}_count_{category_slug(category)}_{window}p"
                    outputs.append(Output(name, CATEGORY, INTEGER, count.column, window=window, category=category))
        return outputs

    @staticmethod
    def _combined(name: str, kind: str, left: str, right: str, operands: set[str]) -> Output:
        _identifier(name, f"a {kind} name")
        missing = [o for o in (left, right) if o not in operands]
        if missing:
            msg = f"{kind} {name!r} refers to unknown outputs or columns {missing} (declare them first)"
            raise FeatureError(msg)
        return Output(name, kind, DOUBLE, left=left, right=right)

    @property
    def value_columns(self) -> tuple[str, ...]:
        """Return the numeric source columns, in declaration order."""
        return tuple(dict.fromkeys(c for f in self.features for c in f.columns))

    @property
    def categorical_columns(self) -> tuple[str, ...]:
        """Return the categorical source columns, in declaration order."""
        return tuple(dict.fromkeys(c.column for c in self.categorical))

    @property
    def max_window(self) -> int:
        """Return the largest window of the spec."""
        windows = [w for f in self.features for w in f.windows] + [w for c in self.categorical for w in c.windows]
        return max(windows)

    def window_outputs(self) -> tuple[Output, ...]:
        """Return the windowed aggregate outputs."""
        return tuple(o for o in self.outputs if o.kind == WINDOW)


_SPEC_KEYS = frozenset({"keys", "closing", "period", "features", "ratios", "differences", "categorical"})


def _sequence(value: object, what: str) -> list[Any]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        msg = f"{what} must be a list"
        raise FeatureError(msg)
    return list(value)  # pyright: ignore[reportUnknownArgumentType]


def _mapping(value: object, what: str, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        msg = f"{what} must be a mapping"
        raise FeatureError(msg)
    unknown = set(value) - keys  # pyright: ignore[reportUnknownArgumentType]
    missing = keys - set(value)  # pyright: ignore[reportUnknownArgumentType]
    if unknown or missing:
        msg = f"{what} takes exactly {sorted(keys)}; unknown {sorted(unknown)}, missing {sorted(missing)}"
        raise FeatureError(msg)
    return value  # pyright: ignore[reportUnknownVariableType]


def parse_spec(declaration: Mapping[str, Any]) -> FeatureSpec:
    """Build a :class:`FeatureSpec` from a plain dict, refusing unknown keys."""
    unknown = set(declaration) - _SPEC_KEYS
    if unknown:
        msg = f"unknown feature spec keys {sorted(unknown)}; known: {sorted(_SPEC_KEYS)}"
        raise FeatureError(msg)
    features = [
        WindowFeature(
            tuple(_sequence(m["columns"], "columns")),
            tuple(_sequence(m["windows"], "windows")),
            tuple(_sequence(m["aggregates"], "aggregates")),
        )
        for m in (
            _mapping(item, "a feature", {"columns", "windows", "aggregates"})
            for item in _sequence(declaration.get("features", []), "features")
        )
    ]
    ratios = [
        Ratio(m["name"], m["numerator"], m["denominator"])
        for m in (
            _mapping(item, "a ratio", {"name", "numerator", "denominator"})
            for item in _sequence(declaration.get("ratios", []), "ratios")
        )
    ]
    differences = [
        Difference(m["name"], m["left"], m["right"])
        for m in (
            _mapping(item, "a difference", {"name", "left", "right"})
            for item in _sequence(declaration.get("differences", []), "differences")
        )
    ]
    categorical = [
        CategoricalCount(
            m["column"], tuple(_sequence(m["categories"], "categories")), tuple(_sequence(m["windows"], "windows"))
        )
        for m in (
            _mapping(item, "a categorical count", {"column", "categories", "windows"})
            for item in _sequence(declaration.get("categorical", []), "categorical")
        )
    ]
    if "keys" not in declaration or "closing" not in declaration:
        msg = "a feature spec needs keys and closing"
        raise FeatureError(msg)
    return FeatureSpec(
        keys=tuple(_sequence(declaration["keys"], "keys")),
        closing=declaration["closing"],
        features=tuple(features),
        ratios=tuple(ratios),
        differences=tuple(differences),
        categorical=tuple(categorical),
        period=declaration.get("period", MONTH),
    )
