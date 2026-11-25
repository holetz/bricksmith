"""Fit and apply bins: numeric (quantile or fixed edges) and categorical, with explicit null/outlier/unseen bins."""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.errors import MonitorError

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping

MIN_EDGES = 2
NULL = "null"
BELOW = "below"
ABOVE = "above"
UNSEEN = "unseen"
ZERO = "zero"
ZERO_TEXT = "0.0"
NEGATIVE_ZERO_TEXT = "-0.0"
OUTLIERS = ("separate", "clip")
NUMERIC_METHODS = ("quantile", "fixed")


def is_null(value: object) -> bool:
    """Return whether ``value`` is null or NaN (both land in the explicit ``null`` bin)."""
    return value is None or (isinstance(value, float) and math.isnan(value))


def category_text(value: object) -> str:
    """Return the category spelling: booleans are ``true``/``false``, either zero is ``0.0``, else ``str``."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value == 0:
        return ZERO_TEXT
    return str(value)


@dataclass(frozen=True)
class BinSpec:
    """One fitted column: the bin definition and the fit counts per bin id (the reference distribution)."""

    column: str
    kind: str
    edges: tuple[float, ...] = ()
    outliers: str = "separate"
    categories: tuple[str, ...] = ()
    counts: Mapping[str, int] = field(default_factory=dict)
    method: str = ""
    zero: bool = False

    def bin_ids(self) -> tuple[str, ...]:
        """Return the bin ids in a stable order."""
        if self.kind == "categorical":
            return (*(f"cat:{c}" for c in self.categories), UNSEEN, NULL)
        edge = (BELOW,) if self.outliers == "separate" else ()
        tail = (ABOVE,) if self.outliers == "separate" else ()
        zero = (ZERO,) if self.zero else ()
        return (*edge, *zero, *(f"bin_{i}" for i in range(len(self.edges) - 1)), *tail, NULL)

    def assign(self, value: object) -> str:  # noqa: PLR0911 - one return per bin rule
        """Return the bin id of ``value``; never refits."""
        if is_null(value):
            return NULL
        if self.kind == "categorical":
            text = category_text(value)
            if text == ZERO_TEXT and text not in self.categories and NEGATIVE_ZERO_TEXT in self.categories:
                text = NEGATIVE_ZERO_TEXT
            return f"cat:{text}" if text in self.categories else UNSEEN
        x = float(value)  # type: ignore[arg-type]
        if self.zero and x == 0:
            return ZERO
        last = len(self.edges) - 1
        if x < self.edges[0]:
            return BELOW if self.outliers == "separate" else "bin_0"
        if x > self.edges[-1]:
            return ABOVE if self.outliers == "separate" else f"bin_{last - 1}"
        for i in range(last):
            if x <= self.edges[i + 1]:
                return f"bin_{i}"
        return f"bin_{last - 1}"  # pragma: no cover - unreachable for ordered edges

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form; ``zero`` appears only when set, so older forms are unchanged."""
        data: dict[str, Any] = {
            "categories": list(self.categories),
            "column": self.column,
            "counts": {k: int(self.counts[k]) for k in sorted(self.counts)},
            "edges": list(self.edges),
            "kind": self.kind,
            "method": self.method,
            "outliers": self.outliers,
        }
        if self.zero:
            data["zero"] = True
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BinSpec:
        """Rebuild a bin spec from ``to_dict`` output."""
        return cls(
            column=str(data["column"]),
            kind=str(data["kind"]),
            edges=tuple(float(e) for e in data.get("edges", ())),
            outliers=str(data.get("outliers", "separate")),
            categories=tuple(str(c) for c in data.get("categories", ())),
            counts={str(k): int(v) for k, v in data.get("counts", {}).items()},
            method=str(data.get("method", "")),
            zero=data.get("zero", False) is True,
        )


def count_bins(spec: BinSpec, values: Mapping[Hashable, int]) -> dict[str, int]:
    """Return counts per bin id for a ``value -> count`` mapping (every bin present, zero included)."""
    result = dict.fromkeys(spec.bin_ids(), 0)
    for value, count in values.items():
        result[spec.assign(value)] += int(count)
    return result


def _quantile_edges(counts: Mapping[Hashable, int], bins: int, *, allow_empty: bool = False) -> tuple[float, ...]:
    ordered = sorted((float(v), int(c)) for v, c in counts.items() if not is_null(v) and c > 0)  # type: ignore[arg-type]
    total = sum(c for _, c in ordered)
    if total == 0 and allow_empty:
        return (0.0, 0.0)
    if total == 0:
        msg = "cannot fit numeric bins: no non-null values"
        raise MonitorError(msg)
    low, high = ordered[0][0], ordered[-1][0]
    cuts: set[float] = set()
    for rank in cut_ranks(total, bins):
        running = 0
        for value, count in ordered:
            running += count
            if running >= rank:
                cuts.add(value)
                break
    return edges_from_cuts(low, cuts, high)


def fit_bins(column: str, counts: Mapping[Hashable, int], config: Mapping[str, Any]) -> BinSpec:
    """Fit one column from a ``value -> count`` mapping (null and NaN keys allowed) and a bin configuration.

    ``config``: ``{"type": "numeric", "method": "quantile", "bins": 4}``, ``{"type": "numeric", "edges": [...]}`` or
    ``{"type": "categorical", "max_categories": 20, "min_count": 1}``; numeric also takes ``outliers`` and
    ``zero: "separate"`` (exact zeros get their own bin and quantile edges are fitted on the non-zero values).
    """
    options = bin_options(column, config)
    if options.kind == "categorical":
        tally: dict[str, int] = {}
        for value, count in counts.items():
            if not is_null(value) and count > 0:
                text = category_text(value)
                tally[text] = tally.get(text, 0) + int(count)
        ranked = sorted(tally.items(), key=lambda item: (-item[1], item[0]))
        kept = tuple(v for v, c in ranked if c >= options.min_count)[: options.max_categories]
        spec = BinSpec(column, "categorical", categories=kept, method="categories")
    else:
        edges = options.edges
        if edges is None:
            source = _without_zeros(counts) if options.zero else counts
            edges = _quantile_edges(source, options.bins, allow_empty=options.zero)
        spec = BinSpec(
            column, "numeric", edges=edges, outliers=options.outliers, method=options.method, zero=options.zero
        )
    return BinSpec(
        spec.column,
        spec.kind,
        spec.edges,
        spec.outliers,
        spec.categories,
        count_bins(spec, counts),
        spec.method,
        spec.zero,
    )


@dataclass(frozen=True)
class BinOptions:
    """A validated bin configuration: the kind and its numeric or categorical options."""

    kind: str
    outliers: str = "separate"
    zero: bool = False
    edges: tuple[float, ...] | None = None
    bins: int = 10
    method: str = "categories"
    min_count: int = 1
    max_categories: int = 20


def bin_options(column: str, config: Mapping[str, Any]) -> BinOptions:
    """Validate one column's bin configuration (shared by the pure and the Spark fit)."""
    kind = str(config.get("type", "numeric"))
    if kind == "categorical":
        return BinOptions(
            "categorical",
            min_count=int(config.get("min_count", 1)),
            max_categories=int(config.get("max_categories", 20)),
        )
    if kind != "numeric":
        msg = f"{column}: bin type must be 'numeric' or 'categorical', got {kind!r}"
        raise MonitorError(msg)
    outliers = str(config.get("outliers", "separate"))
    if outliers not in OUTLIERS:
        msg = f"outliers must be one of {', '.join(OUTLIERS)}, got {outliers!r}"
        raise MonitorError(msg)
    zero = _zero_option(column, config.get("zero", False))
    if "edges" in config:
        edges = tuple(float(e) for e in config["edges"])
        if len(edges) < MIN_EDGES or any(b <= a for a, b in itertools.pairwise(edges)):
            msg = f"{column}: fixed edges must be at least two strictly increasing numbers"
            raise MonitorError(msg)
        return BinOptions("numeric", outliers, zero, edges, method="fixed")
    method = str(config.get("method", "quantile"))
    bins = int(config.get("bins", 10))
    if method != "quantile" or bins < 1:
        msg = f"{column}: numeric bins need method 'quantile' with bins >= 1, or explicit edges"
        raise MonitorError(msg)
    return BinOptions("numeric", outliers, zero, None, bins, "quantile")


def cut_ranks(total: int, bins: int) -> tuple[int, ...]:
    """Return the nearest ranks ``ceil(j * total / bins)`` of the quantile cuts ``j = 1..bins-1``."""
    return tuple(-(-j * total // bins) for j in range(1, bins))


def edges_from_cuts(low: float, cuts: set[float], high: float) -> tuple[float, ...]:
    """Return ``[low, distinct cuts below high, high]``: equal values never create empty bins."""
    return (low, *sorted(c for c in cuts if c < high), high)


def _zero_option(column: str, value: object) -> bool:
    if value in (False, None, "", "none"):
        return False
    if value in (True, "separate"):
        return True
    msg = f"{column}: zero must be 'separate' (or true/false), got {value!r}"
    raise MonitorError(msg)


def _without_zeros(counts: Mapping[Hashable, int]) -> dict[Hashable, int]:
    return {v: c for v, c in counts.items() if is_null(v) or float(v) != 0}  # type: ignore[arg-type]
