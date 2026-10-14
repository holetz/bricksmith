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
OUTLIERS = ("separate", "clip")
NUMERIC_METHODS = ("quantile", "fixed")


def is_null(value: object) -> bool:
    """Return whether ``value`` is null or NaN (both land in the explicit ``null`` bin)."""
    return value is None or (isinstance(value, float) and math.isnan(value))


def category_text(value: object) -> str:
    """Return the category spelling: booleans are ``true``/``false``, everything else ``str``."""
    if isinstance(value, bool):
        return "true" if value else "false"
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

    def bin_ids(self) -> tuple[str, ...]:
        """Return the bin ids in a stable order."""
        if self.kind == "categorical":
            return (*(f"cat:{c}" for c in self.categories), UNSEEN, NULL)
        edge = (BELOW,) if self.outliers == "separate" else ()
        tail = (ABOVE,) if self.outliers == "separate" else ()
        return (*edge, *(f"bin_{i}" for i in range(len(self.edges) - 1)), *tail, NULL)

    def assign(self, value: object) -> str:
        """Return the bin id of ``value``; never refits."""
        if is_null(value):
            return NULL
        if self.kind == "categorical":
            text = category_text(value)
            return f"cat:{text}" if text in self.categories else UNSEEN
        x = float(value)  # type: ignore[arg-type]
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
        """Return the serializable form."""
        return {
            "categories": list(self.categories),
            "column": self.column,
            "counts": {k: int(self.counts[k]) for k in sorted(self.counts)},
            "edges": list(self.edges),
            "kind": self.kind,
            "method": self.method,
            "outliers": self.outliers,
        }

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
        )


def count_bins(spec: BinSpec, values: Mapping[Hashable, int]) -> dict[str, int]:
    """Return counts per bin id for a ``value -> count`` mapping (every bin present, zero included)."""
    result = dict.fromkeys(spec.bin_ids(), 0)
    for value, count in values.items():
        result[spec.assign(value)] += int(count)
    return result


def _quantile_edges(counts: Mapping[Hashable, int], bins: int) -> tuple[float, ...]:
    ordered = sorted((float(v), int(c)) for v, c in counts.items() if not is_null(v) and c > 0)  # type: ignore[arg-type]
    total = sum(c for _, c in ordered)
    if total == 0:
        msg = "cannot fit numeric bins: no non-null values"
        raise MonitorError(msg)
    low, high = ordered[0][0], ordered[-1][0]
    cuts: set[float] = set()
    for j in range(1, bins):
        rank = -(-j * total // bins)  # ceil(j * n / B), nearest rank
        running = 0
        for value, count in ordered:
            running += count
            if running >= rank:
                cuts.add(value)
                break
    return (low, *sorted(c for c in cuts if c < high), high)


def fit_bins(column: str, counts: Mapping[Hashable, int], config: Mapping[str, Any]) -> BinSpec:
    """Fit one column from a ``value -> count`` mapping (null and NaN keys allowed) and a bin configuration.

    ``config``: ``{"type": "numeric", "method": "quantile", "bins": 4}``, ``{"type": "numeric", "edges": [...]}`` or
    ``{"type": "categorical", "max_categories": 20, "min_count": 1}``; numeric also takes ``outliers``.
    """
    kind = str(config.get("type", "numeric"))
    if kind == "categorical":
        tally: dict[str, int] = {}
        for value, count in counts.items():
            if not is_null(value) and count > 0:
                text = category_text(value)
                tally[text] = tally.get(text, 0) + int(count)
        min_count = int(config.get("min_count", 1))
        limit = int(config.get("max_categories", 20))
        ranked = sorted(tally.items(), key=lambda item: (-item[1], item[0]))
        kept = tuple(v for v, c in ranked if c >= min_count)[:limit]
        spec = BinSpec(column, "categorical", categories=kept, method="categories")
    elif kind == "numeric":
        outliers = str(config.get("outliers", "separate"))
        if outliers not in OUTLIERS:
            msg = f"outliers must be one of {', '.join(OUTLIERS)}, got {outliers!r}"
            raise MonitorError(msg)
        if "edges" in config:
            edges = tuple(float(e) for e in config["edges"])
            if len(edges) < MIN_EDGES or any(b <= a for a, b in itertools.pairwise(edges)):
                msg = f"{column}: fixed edges must be at least two strictly increasing numbers"
                raise MonitorError(msg)
            method = "fixed"
        else:
            method = str(config.get("method", "quantile"))
            bins = int(config.get("bins", 10))
            if method != "quantile" or bins < 1:
                msg = f"{column}: numeric bins need method 'quantile' with bins >= 1, or explicit edges"
                raise MonitorError(msg)
            edges = _quantile_edges(counts, bins)
        spec = BinSpec(column, "numeric", edges=edges, outliers=outliers, method=method)
    else:
        msg = f"{column}: bin type must be 'numeric' or 'categorical', got {kind!r}"
        raise MonitorError(msg)
    return BinSpec(
        spec.column, spec.kind, spec.edges, spec.outliers, spec.categories, count_bins(spec, counts), spec.method
    )
