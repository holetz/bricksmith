"""Storage: Spark/Delta table I/O and task write strategies planned offline, executed on Spark."""

from bricksmith.storage.errors import EmptyInputError, UnsupportedError, WriteCheckError, WriteSpecError
from bricksmith.storage.history import (
    current_version,
    plan_restore,
    plan_retention,
    run_plan,
    table_history,
)
from bricksmith.storage.plan import Step, WritePlan, ensure_authorized, plan_write, scd2_invariant_steps
from bricksmith.storage.registry_sync import apply_registry_metadata, plan_registry_metadata
from bricksmith.storage.scd2 import scd2_violations
from bricksmith.storage.spark import HookEvent, SparkTableIO, WriteResult
from bricksmith.storage.spec import STRATEGIES, Window, WriteSpec, parse_write_spec

__all__ = [
    "STRATEGIES",
    "EmptyInputError",
    "HookEvent",
    "SparkTableIO",
    "Step",
    "UnsupportedError",
    "Window",
    "WriteCheckError",
    "WritePlan",
    "WriteResult",
    "WriteSpec",
    "WriteSpecError",
    "apply_registry_metadata",
    "current_version",
    "ensure_authorized",
    "parse_write_spec",
    "plan_registry_metadata",
    "plan_restore",
    "plan_retention",
    "plan_write",
    "run_plan",
    "scd2_invariant_steps",
    "scd2_violations",
    "table_history",
]
