"""Model evaluation: discrimination, stability and calibration of every sample against the reference sample.

``evaluate`` measures each sample (train, test, out-of-time, ...) and each closing with the shared monitor core
(KS, AUC, Gini, PSI of the score, CSI of the selected features, Brier, ECE) and adds the per-grade calibration
table with its inversions. Importing this package loads only the standard library; Spark and MLflow load where
they are used.
"""

from bricksmith.mlops.evaluation.config import (
    DECREASING,
    INCREASING,
    METADATA_KEY,
    EvaluationConfig,
    EvaluationError,
    parse_config,
)
from bricksmith.mlops.evaluation.engine import GRADE_PSI, INVERSIONS, WHOLE, EvaluationResult, evaluate

__all__ = [
    "DECREASING",
    "GRADE_PSI",
    "INCREASING",
    "INVERSIONS",
    "METADATA_KEY",
    "WHOLE",
    "EvaluationConfig",
    "EvaluationError",
    "EvaluationResult",
    "evaluate",
    "parse_config",
]
