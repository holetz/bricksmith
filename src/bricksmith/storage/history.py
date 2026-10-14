"""History, restore and retention helpers: plans first, then explicit application; nothing runs on read."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.storage.errors import WriteSpecError
from bricksmith.storage.plan import MUTATE, Step, WritePlan, ensure_authorized
from bricksmith.storage.sql import literal, quote

if TYPE_CHECKING:
    from collections.abc import Mapping


def _plan(target: str, operation: str, step: Step, *, protected: bool, granted: bool) -> WritePlan:
    ensure_authorized(target, operation, protected=protected, granted=granted)
    return WritePlan(
        target, operation, operation, protected, granted, "allow_empty", "", (step,), {"strategy": operation}
    )


def plan_restore(
    target: str,
    *,
    version: int | None = None,
    timestamp: str | None = None,
    protected: bool = False,
    granted: bool = False,
) -> WritePlan:
    """Plan ``RESTORE TABLE`` to a version or an ISO timestamp (exactly one); restore adds a new version."""
    if (version is None) == (timestamp is None):
        msg = "give exactly one of version or timestamp"
        raise WriteSpecError(msg)
    point = f"VERSION AS OF {int(version)}" if version is not None else f"TIMESTAMP AS OF {literal(timestamp)}"
    step = Step(MUTATE, f"restore the table to {point.lower()}", f"RESTORE TABLE {quote(target)} TO {point}")
    return _plan(target, "restore", step, protected=protected, granted=granted)


def plan_retention(
    target: str,
    *,
    log_days: int,
    deleted_file_days: int,
    protected: bool = False,
    granted: bool = False,
) -> WritePlan:
    """Plan the Delta log and deleted-file retention table properties (days, at least 1)."""
    if log_days < 1 or deleted_file_days < 1:
        msg = "retention periods are whole days, at least 1"
        raise WriteSpecError(msg)
    props = (
        f"'delta.logRetentionDuration' = 'interval {log_days} days', "
        f"'delta.deletedFileRetentionDuration' = 'interval {deleted_file_days} days'"
    )
    step = Step(MUTATE, "set retention table properties", f"ALTER TABLE {quote(target)} SET TBLPROPERTIES ({props})")
    return _plan(target, "retention", step, protected=protected, granted=granted)


def run_plan(spark: Any, plan: WritePlan) -> None:  # noqa: ANN401 - Spark session
    """Execute a helper plan's steps after re-checking authorization."""
    ensure_authorized(plan.target, plan.operation, protected=plan.protected, granted=plan.granted)
    for step in plan.steps:
        spark.sql(step.sql)


def table_history(spark: Any, target: str, limit: int = 20) -> list[Mapping[str, object]]:  # noqa: ANN401 - Spark session
    """Return the newest ``limit`` Delta history entries (version, timestamp, operation); a pure read."""
    rows = spark.sql(f"DESCRIBE HISTORY {quote(target)} LIMIT {int(limit)}").collect()
    return [{"version": r["version"], "timestamp": r["timestamp"], "operation": r["operation"]} for r in rows]


def current_version(spark: Any, target: str) -> int | None:  # noqa: ANN401 - Spark session
    """Return the table's latest Delta version, or None when it is not a Delta table."""
    if not spark.catalog.tableExists(target):
        return None
    try:
        rows = spark.sql(f"DESCRIBE HISTORY {quote(target)} LIMIT 1").collect()
    except Exception:  # noqa: BLE001 - views and missing tables have no history
        return None
    return int(rows[0]["version"]) if rows else None
