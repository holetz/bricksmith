"""Model development capabilities within bricksmith's shared task model.

BS-11 (this slice): explicit model dataset metadata (``spec``), dataset construction
(``reference`` over row dicts, ``spark`` over DataFrames), validation and fingerprints
(``validate``), and the ``dataset`` CLI group (``cli``). Training and registration are BS-12.
MLOps tasks may share their catalog, schema and task scope with data and monitoring tasks.
Importing this package loads only the standard library.
"""

from bricksmith.mlops.errors import DatasetError, DatasetValidationError
from bricksmith.mlops.spec import (
    METADATA_KEY,
    ROLES,
    DatasetDeclaration,
    ModelSpec,
    dataset_spec,
    load_spec_file,
    parse_declaration,
    parse_spec,
)
from bricksmith.mlops.validate import (
    DatasetFingerprint,
    Finding,
    ValidationReport,
    fingerprint,
    validate_dataset,
    validate_prediction,
)

__all__ = [
    "METADATA_KEY",
    "ROLES",
    "DatasetDeclaration",
    "DatasetError",
    "DatasetFingerprint",
    "DatasetValidationError",
    "Finding",
    "ModelSpec",
    "ValidationReport",
    "dataset_spec",
    "fingerprint",
    "load_spec_file",
    "parse_declaration",
    "parse_spec",
    "validate_dataset",
    "validate_prediction",
]
