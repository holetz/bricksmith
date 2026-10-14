"""Storage integration: let a write's declared ``evolution`` policy admit schema changes through the planner."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.evolution.apply import read_live
from bricksmith.evolution.errors import MigrationBlockedError
from bricksmith.evolution.model import schema_from_columns
from bricksmith.evolution.planner import MigrationPlan, parse_policy, plan_migration

if TYPE_CHECKING:
    from collections.abc import Mapping

EVOLUTION_KEY = "evolution"


def plan_for_write(spark: Any, physical: str, data: Any, options: Mapping[str, object]) -> MigrationPlan | None:  # noqa: ANN401
    """Plan the migration that lets ``data`` be written to ``physical``, or ``None`` without a declared policy.

    The declaration is the input's columns; the table's checks, keys, properties and comments are kept as they are.
    Blocked operations raise before anything is written.
    """
    raw = options.get(EVOLUTION_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        msg = "write metadata 'evolution' is a mapping (see the schema evolution guide)"
        raise MigrationBlockedError(msg)
    policy = parse_policy(raw)
    live = read_live(spark, physical)
    declared = schema_from_columns(data.schema.jsonValue(), base=live)
    plan = plan_migration(physical, declared, live, policy)
    if plan.blocked:
        listing = "; ".join(f"{o.description}: {o.blocked}" for o in plan.blocked)
        msg = f"{physical}: the input schema needs changes the evolution policy does not allow ({listing})"
        raise MigrationBlockedError(msg)
    return plan
