# BS-05. Schema evolution and maintenance

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/schema-evolution.md).
`bricksmith.evolution`; the model, diff and planner are stdlib only and never import Spark.

## Decisions closed

**State model.** `TableSchema` holds columns (name, nested type, nullability, comment), table comment, check
constraints, primary key and table properties. Declared `comment` and `primary_key` of `None` mean "not
managed"; declared checks and properties are managed by key only, so undeclared live ones are never touched.
Column order is not compared. Nested paths use Delta syntax (`a.b`, `a.element.b`, `m.value`).

**Type rules** (defined in `evolution/types.py`, not borrowed from an engine). `widen` means every old value is
exactly representable: integral rank (byte < short < int < long), float to double, an integral to double up to
int and to float up to short, an integral to a decimal whose integer digits hold its range, decimal to decimal
when integer digits (`p - s`) and scale do not shrink, date to timestamp_ntz. `narrow` is a change inside the
numeric or temporal family that can lose information (smaller rank, long to double, decimal to float, fewer
integer or fraction digits, timestamp to date). Everything else (string with numbers, boolean, timestamp versus
timestamp_ntz, binary) is `incompatible`.

**Operation classes.** `compatible`: add nullable column or field, rename, widen, allow nulls, comment, add or
change a check, key, property. `incompatible`: narrowing, incompatible family, tightening nullability, adding a
NOT NULL column. `destructive`: dropping a column.

**Policy** (`EvolutionPolicy`, task metadata `evolution`): `renames` (live path to new leaf name; renames are
declared, never inferred), `narrowing` and `incompatible` (`refuse` default | `checked`), `destructive`
(default false). An operation the policy refuses is `blocked` in the plan and nothing runs. `checked` allows a
top-level type change only after a data check that every value converts and converts back unchanged
(`try_cast` round trip), then rewrites through a temporary column (add, update, drop, rename; the column moves
to the end). Tightening NOT NULL needs `narrowing: checked` and a null-count check; a NOT NULL add needs
`incompatible: checked` and an empty table. Nested rewrites are blocked.

**Order and dependencies.** Required properties first (column mapping `name` with reader 2 / writer 5 for
rename, drop and rewrite; type widening for widening), then checks that reference a changing column or whose
expression changed are dropped, then renames, adds, type changes, nullability, comments, drops, then checks are
re-created (declared expression, else the live one with renames substituted; lost when its column is dropped),
keys, properties, table comment. Adding a check is preceded by a violation count.

**Idempotence and safety.** The plan carries a fingerprint of the live state; `apply_plan` refuses a stale plan.
`migrate` verifies the diff is empty afterwards. A table that matches yields an empty plan. Statements are
separate Delta commits, so a failure mid-plan leaves partial progress; re-planning resumes from the live state
(declare your checks so a dropped one is re-created).

**Storage integration.** With `evolution` in a table write's metadata, `SparkTableIO` plans the migration from
the input's schema (checks, keys, properties and comments stay as they are), refuses blocked operations before
anything is written and applies the rest between the pre-checks and the first write step. Without the key the
BS-04 rejection stands. `explain` never migrates. Not supported with `scd2`. `SparkTableIO.migrations` records
applied plans.

**Maintenance.** `maintenance` metadata (`cluster_by`, `optimize{where,zorder_by}`, `vacuum{retain_hours}`,
`schedule`) is never read by ordinary writes. `plan_maintenance` is pure; `run_maintenance` or `schema maintain
--run` executes. Vacuum retention below 168 hours needs `allow_short_retention`.

## Public contract

`TableSchema`, `diff_schemas`/`Change`, `plan_migration`/`MigrationPlan`/`Operation`, `EvolutionPolicy`,
`parse_policy`, `read_live`, `apply_plan`, `migrate`/`MigrationResult`, `parse_maintenance`/`MaintenanceSpec`,
`plan_maintenance`/`MaintenancePlan`, `run_maintenance`, `load_declaration`, errors `SchemaSpecError`,
`MigrationBlockedError`, `StaleMigrationError`, `MigrationVerificationError`. CLI `bricksmith schema
diff|plan|apply|maintain TABLE --declared FILE` (`--live FILE` for an offline snapshot, `--narrowing`,
`--incompatible`, `--allow-destructive`, `--check`, `maintain --only --run`). Exit 4 when a plan is blocked or
`diff --check` finds differences.

## Deviations

`pyproject.toml` ignores `S608` under `src/bricksmith/evolution` (DDL from validated identifiers). The BS-04
column-mismatch message now points to the evolution policy.

## Acceptance tests

`tests/unit/evolution/`: type table, nested diff, classification, rename with dependent check order, idempotent
and round-tripping plan, destructive and narrowing policy, apply guards, maintenance, CLI, storage bridge.
`tests/remote/test_evolution.py`: see [evidence](../evidence/bs-05-free.md).
