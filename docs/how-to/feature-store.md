---
type: how-to
title: "Feature store"
description: "How to declare closing-based feature tables on tasks and build training and scoring sets by exact closing."
resource: "src/bricksmith/mlops/featurestore/**"
timestamp: "2026-11-23T08:46:31-03:00"
audience: both
authority: current
---

# Feature store

A feature table holds one row per entity and **closing**. A closing is the last day of a period: a month, quarter,
half-year, year, week or day. Batch models read the value of a feature *as known at a closing*. Each row is therefore
stored at the closing when its source **became available**, not at the closing it describes. A training set joins
features to a spine by **exact closing equality**. Training and scoring use the same builder; only the spine differs.

Feature tables are plain Delta tables written by ordinary tasks, so they share discovery, the graph and the runner
with every other task. Their catalog and schema are your choice. Databricks Feature Engineering is an optional
adapter, never a requirement (see [decision 0012](../decisions/0012-closing-based-feature-store.md)).

## Declare a feature table on a task

```python
from bricksmith.mlops.featurestore import parse_feature_table
from bricksmith.mlops.featurestore.spark import stamp_availability
from bricksmith.tasks import task


@task(
    kind="features",
    outputs="account_balances",
    metadata={
        "feature_table": {
            "entity_keys": ["account_id"],
            "closing": "closing_date",  # DATE, part of the primary key (default name)
            "frequency": "monthly",  # default; quarterly, semiannual, annual, weekly, daily
            "availability_lag": 1,  # periods between the closing described and the closing available
            "features": {
                "balance_avg_3": {
                    "description": "Mean balance over three closings",
                    "type": "double",
                    "owner": "team-a",
                },
                "segment": {
                    "description": "Account segment",
                    "type": "string",
                    "owner": "team-a",
                    "default": "unknown",
                },
                "balance_hist": {
                    "description": "Balances of the last three closings",
                    "type": "array<double>",
                    "owner": "team-a",
                    "order": "index 0 is the most recent closing",
                },
            },
        }
    },
)
def account_balances(ctx):
    computed = ...  # one row per account, with the closing the row describes in "described"
    definition = parse_feature_table(...)  # the same literal as above
    ctx.write("account_balances", stamp_availability(computed, definition, described="described"))
```

The declaration must be a literal, because tasks are discovered statically. The keys are:

| Key | Default | Meaning |
|---|---|---|
| `entity_keys` | required | Columns identifying the entity. With the closing they form the primary key. |
| `closing` | `closing_date` | DATE column with the closing the row is available at. |
| `frequency` | `monthly` | `daily`, `weekly`, `monthly`, `quarterly`, `semiannual`, `annual`. Month-based closings are period-end dates (quarters end in March, June, September and December). A date that is not a closing is refused, never snapped. |
| `availability_lag` | `0` | Whole periods between the closing a row describes and the closing it is stored at. |
| `features` | required | Name → `description`, `type`, `owner`, optional `default` (double and string only), and `order` for arrays and maps. |
| `closing_key` | none | Name of a STRING copy of the closing. Only the Feature Engineering adapter's `exact` mode uses it. |
| `output` | the only output | Which task output is the feature table, when the task has several. |

Types are `double` for numeric features, `string` for categorical ones, and `array<double>`, `array<string>`,
`map<string,double>` and `map<string,string>`. Arrays and maps must document their `order`. The table is wide: one
row per entity and closing. Model input vectors are never stored; supervised transforms belong to the model, not
the table.

## What the plan and the load enforce

- The plan derives the write metadata: `write: replace_partitions`, `partition_by: [closing]` and
  `keys: entity_keys + [closing]`. Re-running a closing replaces only that closing. Declared values must agree;
  a conflict is the plan error `invalid_feature_table`.
- With declared `keys`, `replace_partitions` checks that no key is null and that keys are unique in the input.
  A duplicated primary key fails the load and nothing is written. This applies to every `replace_partitions`
  write that declares `keys`.
- `check_rows` (rows) and `check_frame` (Spark) refuse missing or mistyped feature columns, null keys,
  unaligned closings and duplicate primary keys. The builders run them on every table they read.

## Build a training or scoring set

```python
from bricksmith.mlops.featurestore import Lookup
from bricksmith.mlops.featurestore.spark import build_training_set

definitions = {"main.features.account_balances": definition}
lookups = [
    Lookup("main.features.account_balances"),
    Lookup("main.features.account_balances", features=("balance_avg_3",), offset=1, prefix="prev_"),
]
training = build_training_set(spine, lookups, definitions, spark=spark)  # spine: keys, closing_date, label
scoring = build_training_set(current_spine, lookups, definitions, spark=spark)  # same lookups, no label
```

- The spine holds entity keys, the observation closing (`closing`, default `closing_date`) and anything else, such
  as a label, which is kept.
- Each lookup reads the closing `offset` periods before the observation closing. A negative offset would read
  the future and is refused. `keys` maps spine columns to the entity keys in order; by default the names match.
  `prefix` renames the outputs. An output column produced twice, or clashing with the spine, is refused.
- An entity with no row at the wanted closing gets each feature's `default` (else null). A stored null stays null.
- `training.reads` maps each table to the Delta version that was read; tables are read `VERSION AS OF` that
  version. Pass frames with `frames=` to skip the catalog; their `reads` entry is then `None`. The row engine
  (`bricksmith.mlops.featurestore.rows.build_training_set`, with `data=`) reports a SHA-256 of the rows instead.
  Log `reads` as MLflow inputs to make a model reproducible.

The row engine and the Spark engine are equivalent; the Spark engine is proved equal on the free workspace
([evidence](../project/evidence/feature-store-free.md)).

## Optional: Unity Catalog Feature Engineering

Install the `features` extra (`databricks-feature-engineering`) to use `bricksmith.mlops.featurestore.uc`. It
publishes the primary key and builds the client's training set, so `log_model` records feature lineage. The join
mode is explicit:

- `mode="exact"` looks up on the entity keys plus the declared STRING `closing_key`. Add it with
  `with_closing_key(frame, definition)` before writing. The client cannot match a DATE key exactly.
- `mode="timeseries"` makes the DATE closing a TIMESERIES key. The client then performs an **as-of** join: the
  latest closing at or before the wanted one. When a closing is missing, this is not the same as the builders.

```python
from bricksmith.mlops.featurestore import uc

uc.publish_primary_key(spark, "main.features.account_balances", definition, mode="exact")
training_set = uc.create_training_set(client, spine, lookups, definitions, mode="exact", label="label")
```

Declared defaults are not applied on this path: the client returns nulls for missing entities. Feature Views are
not used.
