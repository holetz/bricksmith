"""Errors raised by the governance capability."""

from __future__ import annotations

from bricksmith.errors import ValidationError


class GovernanceConfigError(ValidationError):
    """The governance configuration is malformed or inconsistent."""

    code = "governance_config_error"


class PrincipalMappingError(GovernanceConfigError):
    """A logical principal is unknown, unmapped for the environment, or its mapping expired."""

    code = "governance_principal_error"
