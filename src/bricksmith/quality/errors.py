"""Errors raised by the quality capability."""

from __future__ import annotations

from bricksmith.errors import ValidationError


class QualityError(ValidationError):
    """A comparison, check or gate was configured incorrectly."""

    code = "quality_invalid"


class CheckFailedError(QualityError):
    """A check used as a task did not pass; the task fails with the reportable result attached."""

    code = "quality_check_failed"
