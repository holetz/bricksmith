"""Errors of the external I/O capability; messages never carry secret values."""

from __future__ import annotations

from bricksmith.errors import BricksmithError, ValidationError


class IoError(BricksmithError):
    """An external read or write failed; ``retryable`` tells whether a retry may succeed."""

    code = "io_error"
    retryable = False


class TransientIoError(IoError):
    """A failure that a retry can fix (connection loss, lock timeout, throttling)."""

    code = "io_transient"
    retryable = True


class PermanentIoError(IoError):
    """A failure no retry fixes (constraint violation, bad statement, missing object, denied access)."""

    code = "io_permanent"


class LimitExceededError(ValidationError):
    """A configured row, byte or file limit would be exceeded."""

    code = "io_limit_exceeded"


class SecretError(IoError):
    """A secret reference is malformed or cannot be resolved."""

    code = "io_secret"


class PathError(ValidationError):
    """An artifact resource name or relative path is not acceptable."""

    code = "io_path"
