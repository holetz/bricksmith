"""Errors raised by the model dataset helpers."""

from __future__ import annotations

from bricksmith.errors import ValidationError


class DatasetError(ValidationError):
    """A model dataset specification or declaration is malformed."""

    code = "dataset_invalid"


class DatasetValidationError(DatasetError):
    """A built dataset violates its contract (leakage, immature labels, overlap, wrong features)."""

    code = "dataset_validation_failed"


class ModelError(ValidationError):
    """A model operation (training, registration, loading, lifecycle) was rejected."""

    code = "model_invalid"


class ModelNotFoundError(ModelError):
    """The requested model, version or alias does not exist."""

    code = "model_missing"


class ModelAmbiguousError(ModelError):
    """A model reference names no selector, several selectors or an implicit one such as ``latest``."""

    code = "model_ambiguous"


class ModelIncompatibleError(ModelError):
    """The artifact does not fit the identity, the feature contract or the runtime asking for it."""

    code = "model_incompatible"


class FeatureContractError(ModelError):
    """Rows or an artifact disagree with the declared feature contract."""

    code = "feature_contract_mismatch"


class ModelInUseError(ModelError):
    """Retirement refused because active consumers still resolve to the model."""

    code = "model_in_use"
