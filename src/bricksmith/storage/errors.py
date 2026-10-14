"""Errors raised by the storage strategies; every one is raised before the target is mutated unless noted."""

from __future__ import annotations

from bricksmith.errors import BricksmithError, ValidationError


class WriteSpecError(ValidationError):
    """Write metadata is missing, inconsistent or names an unknown strategy."""

    code = "write_spec_error"


class EmptyInputError(ValidationError):
    """The input is empty and the declared empty-input policy is ``fail``."""

    code = "empty_input"


class WriteCheckError(ValidationError):
    """A pre-write check failed (nothing was written) or a post-write check failed (see the recovery path)."""

    code = "write_check_failed"


class UnsupportedError(BricksmithError):
    """The workspace or compute refuses the object kind; reported as ``unsupported``, never as a pass."""

    code = "unsupported"
