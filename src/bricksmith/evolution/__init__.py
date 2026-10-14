"""Schema evolution and maintenance: a Spark-free model, diff and planner, and a thin Spark/Delta adapter."""

from bricksmith.evolution.apply import MigrationResult, apply_plan, migrate, read_live
from bricksmith.evolution.declaration import Declaration, load_declaration, parse_declaration
from bricksmith.evolution.diff import Change, diff_schemas
from bricksmith.evolution.errors import (
    MigrationBlockedError,
    MigrationVerificationError,
    SchemaSpecError,
    StaleMigrationError,
)
from bricksmith.evolution.maintenance import (
    MaintenancePlan,
    MaintenanceSpec,
    parse_maintenance,
    plan_maintenance,
    run_maintenance,
)
from bricksmith.evolution.model import TableSchema, schema_from_spark
from bricksmith.evolution.planner import EvolutionPolicy, MigrationPlan, Operation, parse_policy, plan_migration
from bricksmith.evolution.types import compare_types, parse_type

__all__ = [
    "Change",
    "Declaration",
    "EvolutionPolicy",
    "MaintenancePlan",
    "MaintenanceSpec",
    "MigrationBlockedError",
    "MigrationPlan",
    "MigrationResult",
    "MigrationVerificationError",
    "Operation",
    "SchemaSpecError",
    "StaleMigrationError",
    "TableSchema",
    "apply_plan",
    "compare_types",
    "diff_schemas",
    "load_declaration",
    "migrate",
    "parse_declaration",
    "parse_maintenance",
    "parse_policy",
    "parse_type",
    "plan_maintenance",
    "plan_migration",
    "read_live",
    "run_maintenance",
    "schema_from_spark",
]
