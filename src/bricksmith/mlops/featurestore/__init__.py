"""Closing-based feature store: entity-by-closing feature tables, availability lag and exact-closing training sets.

A feature table is an ordinary task output declared with the ``feature_table`` metadata key. Its rows are
stamped at the closing at which their source became available, and a training set joins them to a spine
(entity keys plus an observation closing) by exact closing equality. The same builder serves training and
scoring; only the spine differs. Importing this package loads only the standard library: the row engine is
``rows``, the Spark engine ``spark`` and the optional Unity Catalog Feature Engineering adapter ``uc``.
"""

from bricksmith.mlops.errors import FeatureStoreError
from bricksmith.mlops.featurestore.config import (
    FEATURE_TYPES,
    FREQUENCIES,
    METADATA_KEY,
    Feature,
    FeatureTable,
    Lookup,
    feature_table_of,
    is_closing,
    parse_feature_table,
    shift,
)

__all__ = [
    "FEATURE_TYPES",
    "FREQUENCIES",
    "METADATA_KEY",
    "Feature",
    "FeatureStoreError",
    "FeatureTable",
    "Lookup",
    "feature_table_of",
    "is_closing",
    "parse_feature_table",
    "shift",
]
