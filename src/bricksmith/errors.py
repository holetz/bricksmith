"""Stable error types and exit codes shared by the library and the command line."""

from __future__ import annotations

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_CONFIG = 3
EXIT_VALIDATION = 4

EXIT_CODES: dict[int, str] = {
    EXIT_OK: "success",
    EXIT_UNEXPECTED: "unexpected failure (a bug; report it)",
    EXIT_USAGE: "command-line usage error",
    EXIT_CONFIG: "configuration, authentication or missing-extra error",
    EXIT_VALIDATION: "invalid input rejected before any remote call",
}


class BricksmithError(Exception):
    """Base class for expected failures; ``code`` is stable and machine readable."""

    code = "bricksmith_error"
    exit_code = EXIT_UNEXPECTED

    def __init__(self, message: str) -> None:
        """Store the human message; secrets must never be part of it."""
        super().__init__(message)
        self.message = message

    def to_dict(self) -> dict[str, str]:
        """Return the structured form used by ``--json`` output."""
        return {"code": self.code, "message": self.message}


class UsageError(BricksmithError):
    """The command line or API call was malformed."""

    code = "usage_error"
    exit_code = EXIT_USAGE


class ConfigError(BricksmithError):
    """Environment, profile, dotenv or authentication configuration is unusable."""

    code = "config_error"
    exit_code = EXIT_CONFIG


class AuthConflictError(ConfigError):
    """Exported credentials conflict with the selected profile or expected host."""

    code = "auth_conflict"


class MissingExtraError(ConfigError):
    """An optional integration is not installed."""

    code = "missing_extra"


class ValidationError(BricksmithError):
    """User-supplied values are invalid or ambiguous."""

    code = "validation_error"
    exit_code = EXIT_VALIDATION
