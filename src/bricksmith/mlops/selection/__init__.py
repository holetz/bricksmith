"""Feature selection: a reproducible funnel over a training set that leaves a per-variable selection ledger.

The funnel splits the input into development and out-of-time samples by closing, then runs quality, stability,
univariate power, redundancy, an optional multivariate step and declared judgment. Importing this package loads
only the standard library; Spark, scikit-learn and MLflow load where they are used.
"""

from bricksmith.mlops.selection.config import STEPS, SelectionConfig, SelectionError, parse_config
from bricksmith.mlops.selection.funnel import (
    LedgerEntry,
    SelectionResult,
    VariableOutcome,
    apply_woe,
    select_features,
)
from bricksmith.mlops.selection.metrics import WoeTable
from bricksmith.mlops.selection.tables import table_rows, to_frame, write_options
from bricksmith.mlops.selection.tracking import log_selection

__all__ = [
    "STEPS",
    "LedgerEntry",
    "SelectionConfig",
    "SelectionError",
    "SelectionResult",
    "VariableOutcome",
    "WoeTable",
    "apply_woe",
    "log_selection",
    "parse_config",
    "select_features",
    "table_rows",
    "to_frame",
    "write_options",
]
