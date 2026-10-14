"""The thin Spark/Delta adapter: read live state, apply a plan in order, verify, and stay a no-op on re-run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.evolution.diff import diff_schemas
from bricksmith.evolution.errors import MigrationBlockedError, MigrationVerificationError, StaleMigrationError
from bricksmith.evolution.model import TableSchema, schema_from_spark
from bricksmith.evolution.planner import EvolutionPolicy, MigrationPlan, fingerprint, plan_migration
from bricksmith.storage.plan import CHECK
from bricksmith.storage.sql import quote

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of :func:`migrate`: the plan and whether anything ran."""

    plan: MigrationPlan
    applied: bool

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"applied": self.applied, "plan": self.plan.to_dict()}


def _keys(spark: Any, target: str) -> tuple[tuple[str, ...] | None, str | None]:  # noqa: ANN401 - Spark session
    parts = target.split(".")
    if len(parts) != 3:  # noqa: PLR2004 - catalog.schema.table
        return None, None
    catalog, schema, table = parts
    info = f"{quote(catalog)}.information_schema"
    try:
        rows = spark.sql(
            f"SELECT tc.constraint_name AS name, kcu.column_name AS col FROM {info}.table_constraints tc "
            f"JOIN {info}.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
            f"AND tc.table_schema = kcu.table_schema AND tc.table_name = kcu.table_name "
            f"WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = '{schema}' "
            f"AND tc.table_name = '{table}' ORDER BY kcu.ordinal_position"
        ).collect()
    except Exception:  # noqa: BLE001 - keys are informational and not always available
        return None, None
    if not rows:
        return (), None
    return tuple(r["col"] for r in rows), str(rows[0]["name"])


def read_live(spark: Any, target: str) -> TableSchema:  # noqa: ANN401 - Spark session
    """Read the live schema, comment, properties, checks and primary key of ``target`` (read-only)."""
    schema = spark.table(target).schema.jsonValue()
    props = {r["key"]: r["value"] for r in spark.sql(f"SHOW TBLPROPERTIES {quote(target)}").collect()}
    comment = spark.sql(f"DESCRIBE DETAIL {quote(target)}").collect()[0]["description"]
    key, name = _keys(spark, target)
    return schema_from_spark(schema, properties=props, comment=comment, primary_key=key, key_name=name)


def apply_plan(spark: Any, plan: MigrationPlan, *, reporter: Callable[[str], None] | None = None) -> bool:  # noqa: ANN401
    """Run ``plan`` in order after re-checking it is allowed and still current; return whether steps ran."""
    if plan.blocked:
        listing = "; ".join(f"{o.description}: {o.blocked}" for o in plan.blocked)
        msg = f"{plan.target}: migration blocked by policy, nothing was changed ({listing})"
        raise MigrationBlockedError(msg)
    if plan.empty:
        return False
    if fingerprint(read_live(spark, plan.target)) != plan.fingerprint:
        msg = f"{plan.target} changed since the plan was made; plan again"
        raise StaleMigrationError(msg)
    for step in plan.steps:
        if reporter is not None:
            reporter(step.description)
        if step.kind == CHECK:
            found = int(spark.sql(step.sql).collect()[0][0])
            if found != step.expect:
                msg = f"check failed: {step.description}: found {found}, expected {step.expect}"
                raise MigrationBlockedError(msg)
        else:
            spark.sql(step.sql)
    return True


def migrate(
    spark: Any,  # noqa: ANN401 - Spark session
    target: str,
    declared: TableSchema,
    policy: EvolutionPolicy | None = None,
    *,
    reporter: Callable[[str], None] | None = None,
) -> MigrationResult:
    """Read, plan, apply and verify; a table that already matches is left untouched."""
    policy = policy or EvolutionPolicy()
    plan = plan_migration(target, declared, read_live(spark, target), policy)
    applied = apply_plan(spark, plan, reporter=reporter)
    if applied:
        remaining = diff_schemas(declared, read_live(spark, target), policy.renames)
        if remaining:
            listing = "; ".join(f"{c.kind} {c.path}" for c in remaining)
            msg = f"{target} still differs from the declaration after the migration: {listing}"
            raise MigrationVerificationError(msg)
    return MigrationResult(plan, applied)
