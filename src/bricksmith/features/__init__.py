"""Declarative windowed feature engineering over periodic history.

Declare source columns x windows (in periods, counted from the current closing) x aggregates from the documented
catalog, plus ratios, differences and category counts; every output gets a typed name
(``<column>_<aggregate>_<window>p``). Engines compute the same values: a numpy reference over rows, native Spark
expressions and an Arrow engine. numpy, pyarrow and pyspark load only where an engine uses them:
``compute_rows`` is resolved on first use.
"""

from typing import TYPE_CHECKING, Any

from bricksmith.features.catalog import CATALOG, FAMILIES, Aggregate, aggregate
from bricksmith.features.errors import FeatureError, PointInTimeError, UnsupportedAggregateError
from bricksmith.features.spec import (
    CategoricalCount,
    Difference,
    FeatureSpec,
    Output,
    Ratio,
    WindowFeature,
    feature_name,
    parse_spec,
)

if TYPE_CHECKING:
    from bricksmith.features.engine import compute_rows

__all__ = [
    "CATALOG",
    "FAMILIES",
    "Aggregate",
    "CategoricalCount",
    "Difference",
    "FeatureError",
    "FeatureSpec",
    "Output",
    "PointInTimeError",
    "Ratio",
    "UnsupportedAggregateError",
    "WindowFeature",
    "aggregate",
    "compute_rows",
    "feature_name",
    "parse_spec",
]


def __getattr__(name: str) -> Any:  # noqa: ANN401 - lazily resolved engine entry points
    if name == "compute_rows":
        from bricksmith.features.engine import compute_rows  # noqa: PLC0415 - loads numpy only when used

        return compute_rows
    msg = f"module 'bricksmith.features' has no attribute {name!r}"
    raise AttributeError(msg)
