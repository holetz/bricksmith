"""Infer a bin configuration per column from its type and approximate distinct count (no data is read here).

The caller passes Spark-like type names (``df.dtypes``) or the names :func:`python_type` gives to plain values,
plus approximate distinct counts. Columns that cannot be binned meaningfully are returned with a reason
instead of being dropped silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

NUMERIC = re.compile(r"^(tinyint|smallint|int|integer|bigint|long|short|byte|float|double|real|decimal.*)$")
TEXT = re.compile(r"^(string|varchar.*|char.*|boolean)$")
SKIPPED_TYPES = re.compile(r"^(date|timestamp.*|binary|array.*|map.*|struct.*|variant|interval.*|void|null)$")
EXCLUDED = "excluded"
UNSUPPORTED_TYPE = "unsupported_type"
HIGH_CARDINALITY = "high_cardinality"
CONSTANT = "constant"


@dataclass(frozen=True)
class InferenceOptions:
    """Thresholds of the inference: numeric bins, categories and the cardinality limits."""

    numeric_bins: int = 10
    numeric_min_distinct: int = 20
    max_categories: int = 20
    max_text_distinct: int = 1000
    zero: bool = True


@dataclass(frozen=True)
class Inference:
    """The bin configuration of each monitored column and the reason each other column is skipped."""

    columns: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def python_type(values: Iterable[object]) -> str:  # noqa: PLR0911 - one return per type family
    """Return the Spark-like type name of the first non-null value (``void`` when all are null)."""
    for value in values:
        if value is None or (isinstance(value, float) and value != value):  # noqa: PLR0124 - NaN check
            continue
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "bigint"
        if isinstance(value, (float, Decimal)):
            return "double"
        if isinstance(value, datetime):
            return "timestamp"
        if isinstance(value, date):
            return "date"
        if isinstance(value, str):
            return "string"
        return "struct"
    return "void"


def infer_columns(
    types: Mapping[str, str],
    distinct: Mapping[str, int],
    *,
    exclude: Iterable[str] = (),
    options: InferenceOptions | None = None,
) -> Inference:
    """Return the bin configuration of every column of ``types`` that can be monitored.

    Numeric columns with more than ``numeric_min_distinct`` distinct values get quantile bins (with an
    exact-zero bin unless disabled); other numeric, text and boolean columns are categorical. Dates,
    timestamps and complex types are skipped (``unsupported_type``), as are text columns above
    ``max_text_distinct`` (``high_cardinality``), columns with one value at most (``constant``) and the
    ``exclude`` list (``excluded``).
    """
    rule = options or InferenceOptions()
    banned = set(exclude)
    columns: dict[str, dict[str, Any]] = {}
    skipped: dict[str, str] = {}
    for name in sorted(types):
        kind = types[name].strip().lower()
        count = int(distinct.get(name, 0))
        if name in banned:
            skipped[name] = EXCLUDED
        elif SKIPPED_TYPES.match(kind) or not (NUMERIC.match(kind) or TEXT.match(kind)):
            skipped[name] = UNSUPPORTED_TYPE
        elif count <= 1:
            skipped[name] = CONSTANT
        elif NUMERIC.match(kind) and count > rule.numeric_min_distinct:
            config: dict[str, Any] = {"type": "numeric", "bins": rule.numeric_bins}
            if rule.zero:
                config["zero"] = "separate"
            columns[name] = config
        elif TEXT.match(kind) and count > rule.max_text_distinct:
            skipped[name] = HIGH_CARDINALITY
        else:
            columns[name] = {"type": "categorical", "max_categories": rule.max_categories}
    return Inference(columns, skipped)


__all__ = [
    "CONSTANT",
    "EXCLUDED",
    "HIGH_CARDINALITY",
    "UNSUPPORTED_TYPE",
    "Inference",
    "InferenceOptions",
    "infer_columns",
    "python_type",
]
