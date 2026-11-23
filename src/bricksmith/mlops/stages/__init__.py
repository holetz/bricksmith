"""Model development stages: named sample windows, the exclusion cascade and one spine for training and scoring.

A ``model_development`` declaration names samples (inclusive windows of closings with a role: train, test,
out-of-time, through-the-door, prediction, monitoring), the label and its forward window, and an ordered
exclusion cascade. The population is the spine of the feature store's training-set builder; the training and
scoring ABTs come from the same code path and differ only by the sample. Importing this package loads only the
standard library: the row engine is ``rows`` and the Spark engine ``spark``.
"""

from bricksmith.mlops.stages.config import (
    CASCADE_COLUMNS,
    EXCLUDED,
    EXCLUSION_FIRST,
    EXCLUSION_RULES,
    LABELLED,
    METADATA_KEY,
    PREDICTION_COLUMNS,
    PURPOSES,
    ROLES,
    SAMPLE,
    SAMPLE_ROLE,
    SCORING,
    TRAINING,
    Band,
    Development,
    ExclusionRule,
    Label,
    SampleWindow,
    Split,
    Stratify,
    load_development_file,
    parse_development,
)

__all__ = [
    "CASCADE_COLUMNS",
    "EXCLUDED",
    "EXCLUSION_FIRST",
    "EXCLUSION_RULES",
    "LABELLED",
    "METADATA_KEY",
    "PREDICTION_COLUMNS",
    "PURPOSES",
    "ROLES",
    "SAMPLE",
    "SAMPLE_ROLE",
    "SCORING",
    "TRAINING",
    "Band",
    "Development",
    "ExclusionRule",
    "Label",
    "SampleWindow",
    "Split",
    "Stratify",
    "load_development_file",
    "parse_development",
]
