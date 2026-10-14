# Writing tables from tasks

Declare how an output is written in the task's `metadata`; the runner hands it to the Spark/Delta backend
(`default_table_io`, Databricks Connect). Every write is planned and reported before it mutates.

```python
@task(kind="table", outputs="totals", metadata={"write": "merge", "keys": ["customer"], "on_empty": "skip"})
def totals(ctx):
    orders = ctx.read("orders")  # a DataFrame; reading never writes
    ctx.write("totals", orders.groupBy("customer").sum("amount"))
```

| `write` | Result of a repeat run | Metadata |
|---|---|---|
| `overwrite` | same table | `on_empty` |
| `static_refresh` | skipped once populated | `force` |
| `replace_partitions` | only the named partitions change | `partition_by`, `partitions` or `window` |
| `merge` | upsert on keys | `keys`, `update_columns`, `window`, `late_input` |
| `append` | inserts absent keys only | `keys` (or `allow_duplicates: true`, not idempotent) |
| `scd2` | unchanged input is a no-op | `keys`, `tracked`, `effective`, `late_input` |
| `view`, `materialized_view`, `function` | recreated | write the SQL text; functions add `params`, `returns` |

Policies: `on_empty` is `fail` (default), `skip` or `allow_empty`; `late_input` is `fail` (default), `drop`
or `accept`; `window` is `{column, start, end}` (start inclusive, end exclusive). `input_checks` and
`target_checks` are SQL boolean expressions checked before and after the write. Protected outputs need their
grant; the check happens before anything is read or written.

## SCD2

History columns `valid_from`, `valid_to` (empty for the current row) and `is_current` are added. Changed
tracked values close the current row and add a version at the effective time. Backdated input and
conflicting redeliveries fail (`late_input: drop` ignores backdated rows). Corrections of history and
removals are not supported.

## Inspect, recover, maintain

```python
io = SparkTableIO(spark)
plan = io.explain("c.s.t", frame, operation="merge", options=metadata)  # no effect
print(plan.to_json())
```

A failed write leaves the target at `WriteResult.version_before` unless a post-check failed after the commit;
then `run_plan(spark, plan_restore(target, version=...))` restores it, or re-run the task (writes are
repeat-safe). `plan_retention`, `table_history` and `current_version` cover retention and history. Materialized
views need compute that allows them; otherwise the write raises `UnsupportedError`.

Pass `registry=` and `registry_tables={physical_or_logical: registry_table}` to `SparkTableIO` to apply registry
comments and properties after each write while keeping unrelated metadata. Inside a task the session is
`ctx.io.spark`.
