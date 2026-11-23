"""Feature selection: a reproducible funnel over a training set that leaves a per-variable selection ledger.

The funnel splits the input into development and out-of-time samples by closing, then runs quality, stability,
univariate power, redundancy, an optional multivariate step and declared judgment. Importing this package loads
only the standard library; Spark, scikit-learn and MLflow load where they are used.
"""

from bricksmith.mlops.selection.config import STEPS, SelectionConfig, SelectionError, parse_config

__all__ = ["STEPS", "SelectionConfig", "SelectionError", "parse_config"]
