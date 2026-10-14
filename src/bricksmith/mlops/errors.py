"""Errors raised by the model dataset helpers."""

from __future__ import annotations

from bricksmith.errors import ValidationError


class DatasetError(ValidationError):
    """A model dataset specification or declaration is malformed."""

    code = "dataset_invalid"


class DatasetValidationError(DatasetError):
    """A built dataset violates its contract (leakage, immature labels, overlap, wrong features)."""

    code = "dataset_validation_failed"
