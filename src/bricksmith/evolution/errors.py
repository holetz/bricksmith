"""Errors raised by schema evolution and maintenance."""

from __future__ import annotations

from bricksmith.errors import PlanError, StalePlanError, ValidationError


class SchemaSpecError(ValidationError):
    """A declared schema, policy or maintenance specification is invalid."""

    code = "schema_spec_error"


class MigrationBlockedError(PlanError):
    """The plan holds operations that the declared policy does not allow; nothing was changed."""

    code = "migration_blocked"


class StaleMigrationError(StalePlanError):
    """The live table changed since the plan was made; plan again."""

    code = "stale_migration"


class MigrationVerificationError(PlanError):
    """The table still differs from the declaration after a migration ran."""

    code = "migration_verification_failed"
