# Registry and naming checks

`bricksmith registry check` validates a column registry and naming rules offline (no Spark).

## Files

`registry/columns.toml`:

```toml
version = 1

[[columns]]
name = "customer_id"
type = "string"
description = "Stable customer key."
not_null = true
sensitivity = "internal"

[tables."main.sales.orders"]
description = "One row per order."
grain = ["order_id"]

[tables."main.sales.orders".columns.order_id]
type = "bigint"
description = "Order key."

[tables."main.sales.orders".columns.customer_id]
sensitivity = "confidential"   # override of the base column
```

`registry/naming.toml` (optional; every key is optional):

```toml
version = 1
vocabulary = ["order", "customer", "id"]

[scopes.column]
case = "snake"
vocabulary = true        # tokens must come from the vocabulary
suffixes = ["_id", "_at"]

[severity]
"naming.*" = "error"
"registry.description_missing" = "warning"

[[exceptions]]
finding = "naming.suffix:column:status"
reason = "Legacy column kept until the next migration."
expires = "2027-03-31"
```

## Ratchet

`bricksmith registry check --write-baseline` records current errors in `registry/baseline.json`.
Later runs tolerate those and fail on new errors. Use `--json` for stable sorted output in CI.

## Metadata for table writes and docs

`table_metadata(registry, "main.sales.orders", existing_comments, existing_properties)` returns
the table comment, column comments and `bricksmith.*` properties merged over what already
exists. `sensitivity_coverage(registry)` lists classified, unclassified and unknown columns.
