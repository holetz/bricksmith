"""Errors raised by the registry capability."""

from __future__ import annotations

from typing import NoReturn

from bricksmith.errors import ValidationError


class RegistryFormatError(ValidationError):
    """A registry, preset or baseline file is malformed."""

    code = "registry_format_error"


def fail(message: str) -> NoReturn:
    """Raise a ``RegistryFormatError``; keeps raise sites short."""
    raise RegistryFormatError(message)
