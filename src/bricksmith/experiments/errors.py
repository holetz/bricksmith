"""Errors raised by experiments; all are validation failures with stable codes."""

from __future__ import annotations

from bricksmith.errors import ValidationError


class ExperimentError(ValidationError):
    """An experiment revision, plan or operation is invalid."""

    code = "experiment_error"


class EvidenceError(ExperimentError):
    """Evidence is malformed, tampered with or cannot be read."""

    code = "evidence_error"


class EvidenceExistsError(EvidenceError):
    """Evidence is immutable: a record already exists at the destination."""

    code = "evidence_exists"


class StaleEvidenceError(ExperimentError):
    """The sources, declarations or configuration changed since the evidence was frozen."""

    code = "stale_evidence"


class IsolationError(ExperimentError):
    """An operation would touch an object outside the isolated destination."""

    code = "isolation_violation"
