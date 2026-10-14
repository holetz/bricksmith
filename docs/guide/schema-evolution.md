# Schema evolution and maintenance

Declare the table you want; bricksmith compares it with the live Delta table, plans the migration, and applies
only what your policy allows. Specification: [BS-05](https://github.com/holetz/bricksmith/blob/quenching/specs/archive/12.md).

## Declare

A TOML or JSON file (or `TableSchema.from_dict`):

```toml
comment = "orders"
primary_key = ["id"]                 # optional; omit to leave keys alone

[[columns]]
name = "id"
type = "int"
nullable = false

[[columns]]
name = "value"
type = "decimal(12,2)"

[checks]
positive = "value > 0"

[properties]
"team.tag" = "kept"

[evolution]                          # the policy; everything not listed is refused
renames = { amount = "value" }       # declared, never inferred
narrowing = "refuse"                 # or "checked"
incompatible = "refuse"              # or "checked"
destructive = false                  # true allows dropping columns

[maintenance]
cluster_by = ["id"]
optimize = {}
vacuum = { retain_hours = 168 }
schedule = "0 3 * * 0"
```

Only what you declare is managed: undeclared checks, properties and (unset) comments or keys stay as they are.

## Command line

```bash
bricksmith schema diff  main.sales.orders --declared orders.toml        # --check exits 4 on differences
bricksmith schema plan  main.sales.orders --declared orders.toml --json # no effects; exit 4 when blocked
bricksmith schema apply main.sales.orders --declared orders.toml        # --allow-destructive for drops
bricksmith schema maintain main.sales.orders --declared orders.toml     # plan only; add --run to execute
```

`--live snapshot.json` (a `TableSchema.to_dict` dump) runs `diff` and `plan` with no workspace. `--narrowing`,
`--incompatible` and `--allow-destructive` raise the policy for one run.

## What is allowed

| Change | Class | Needs |
|---|---|---|
| add nullable column or struct field, rename, widen a type, allow nulls, comments, checks, properties | compatible | declared (rename: `renames`) |
| narrowing, incompatible family, tighten nullability, add NOT NULL column | incompatible | `narrowing` / `incompatible` = `checked` |
| drop a column | destructive | `destructive = true` |

Widening is exact only: `int` to `bigint`, `float` to `double`, `decimal(10,2)` to `decimal(12,2)` or
`(12,4)`; `decimal(10,2)` to `(10,4)` is narrowing because integer digits shrink. `checked` converts only after
verifying every value survives a cast and back, then rewrites through a temporary column (the column moves to the
end); nested type rewrites are blocked. Rename and drop turn on Delta column mapping, and widening turns on type
widening; both are protocol upgrades that cannot be undone, and the plan lists them first.

A check that references a changing column is dropped before the change and re-created after it, with the
declared expression or the live one with renames applied. Re-running a migration is a no-op.

## In a write

```python
metadata = {"write": "append", "keys": ["id"], "evolution": {}}  # compatible changes only
```

The input's columns become the declaration; added columns and widened types are migrated before the write,
while narrowing and drops are refused with nothing written. Without `evolution` a differing schema is rejected.

## Maintenance

Writes never optimize, vacuum or cluster. `maintenance` metadata is read only by `plan_maintenance` /
`schema maintain`, so cost and schedule are decided apart from task writes; run it from its own job on the
declared `schedule`.
