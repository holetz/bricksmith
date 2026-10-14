# Model datasets

Population, features, labels and training/prediction datasets are built by ordinary `@task`
functions, so they share the project's discovery, graph and runner with data, quality and
monitoring tasks. A model can live in the same schema as its feature tasks or in a dedicated one;
nothing here depends on a catalog, schema or folder name.

## Explicit metadata

A model is described once in a spec file (TOML or JSON, `models/churn_v1.toml` in the examples) and
each dataset task points to it in its `metadata`:

```python
@task(
    kind="population",
    outputs=["churn_population", "churn_exclusions"],
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "churn_v1", "role": "population", "spec": "models/churn_v1.toml"},
    },
)
def churn_population(ctx):
    spec = dataset_spec(churn_population)
    population, exclusions = build_population(ctx.read("customer_features"), spec)
    ctx.write("churn_population", population)
    ctx.write("churn_exclusions", exclusions)
```

- `model_dataset.model` is the identity: an explicit identifier. The spec must declare the same
  `model`, otherwise the task fails with `dataset_declaration`; a moved or swapped file never selects
  another model silently. `role` is `population`, `features`, `labels`, `training` or `prediction`;
  `spec` is a path relative to the project root, or an inline mapping.
- Spec keys: `model`, `reference_date`, `columns` (`key`, `snapshot`, `feature_time`), `sample`
  (`fraction`, explicit integer `seed`), `segments` and `exclusions` (ordered `{name, where}`),
  ordered `features` (`name`, `type`), `label` (`name`, `type`, `maturity_days`), `split`
  (`train_until`, `evaluation_from`, `separate_keys`).
- Predicates: `{column, op, value}` with `eq ne lt le gt ge in is_null not_null`, plus `all`, `any`,
  `not`. A comparison with null is false.

## Construction

`bricksmith.mlops.spark` works on DataFrames; `bricksmith.mlops.reference` is the same logic over
lists of row dicts (the oracle in unit tests).

- `build_population` keeps the keys whose seeded hash fraction (first 32 bits of
  `sha256("<seed>:<key>")` over 2^32) is below the sample fraction, then records every excluded row
  with its first matching reason (`model`, key, snapshot, `exclusion_reason`) and assigns the first
  matching segment to the others.
- `build_features` / `build_labels` project the contract (types cast, order kept; the label also
  gets `label_time = snapshot + maturity_days`).
- `build_training` joins on key and snapshot, adds `split`, drops rows in the gap between the
  windows; immature labels are kept unless `drop_immature=True`, so validation can diagnose them.
- `build_prediction` takes the `as_of` snapshot and only features observed by then.
- `provenance_frame` and `resolve_fingerprint` record model, dataset, fingerprint, config hash, input
  table versions and counts in a table written by the training task.

## Validation and fingerprint

`validate_frame(df, spec)` (or `validate_dataset(spec, rows, columns=...)` offline) returns a report
with these codes; `report.require()` raises `DatasetValidationError`, so a training task stops before
fitting: `feature_missing`, `feature_unexpected`, `feature_type`, `feature_order`, `immature_label`,
`label_missing`, `snapshot_after_reference`, `feature_after_reference`, `label_after_reference`,
`feature_after_snapshot`, `split_window`, `split_time_overlap`, `split_label_overlap`,
`split_key_overlap`. `fingerprint(spec, inputs, role=...)` hashes the canonical configuration and
the input table versions; inputs without a version are listed as `unpinned`.
Spark validation collects only the projected key, time, label and split columns.

## CLI and examples

```bash
bricksmith dataset validate --project examples/shared_schema [--model churn_v1] [--json]
bricksmith dataset describe --project examples/shared_schema
```

Both are offline. `validate` exits 4 on findings: graph errors, a bad spec, a model id mismatch,
conflicting specs, two tasks with the same model and role, and a training or prediction task that
does not (transitively) read its population, features and labels tasks. `examples/shared_schema`
has two models and their datasets next to feature and quality tasks in one schema;
`examples/dedicated_scope` puts the data tasks and the churn model in separate schemas. Both use
synthetic data written by a task, so they run through `bricksmith run` unchanged.
