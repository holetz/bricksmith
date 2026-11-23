"""Declarative windowed feature engineering over periodic history.

Declare source columns x windows (in periods, counted from the current closing) x aggregates from the documented
catalog, plus ratios, differences and category counts; every output gets a typed name
(``<column>_<aggregate>_<window>p``). Engines compute the same values: a numpy reference over rows, native Spark
expressions and an Arrow engine. numpy, pyarrow and pyspark load only where an engine uses them.
"""

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
    "feature_name",
    "parse_spec",
]
