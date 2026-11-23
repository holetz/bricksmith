---
type: decision
title: "0012. A closing-based feature store on plain Delta tables"
description: "Why feature tables are keyed by entity and closing, stamped at availability and joined by exact closing, with Feature Engineering only as an optional adapter."
resource: "src/bricksmith/mlops/featurestore/**"
timestamp: "2026-11-23T08:46:31-03:00"
audience: both
authority: current
---

# 0012. A closing-based feature store on plain Delta tables

- Status: accepted
- Date: 2026-11-23
- Extends: [0009](0009-one-library-shared-task-scopes.md) and
  [0010](0010-behavior-derived-independent-development.md)

## Context

Model projects rebuilt the same features inside each model revision. There was no shared rule for which closing
of a feature an observation reads. Batch models run on periodic closings, so a feature must be the value known at
a closing, not at an approximate event time. Training from frozen copies while scoring from live tables invites
training/serving skew.

The managed feature store cannot express this contract on its own. A DATE lookup key must be a TIMESERIES key,
which gives an as-of join, or a STRING copy for an exact match. Feature Views are in public preview and refuse date
entity columns.

## Decision

- A feature table is an ordinary task output declared with the `feature_table` metadata key. Its primary key is
  the entity keys plus a DATE closing column.
- A row is stored at the closing at which its source became available: the closing it describes moved by
  `availability_lag` periods. Reads need no lag arithmetic.
- One training-set builder joins a spine to feature lookups by exact closing equality, optionally `offset`
  periods back; future closings are refused. Training and scoring differ only by the spine.
- A row engine and a Spark engine implement the builder and are proved equal. The builder reports the Delta
  version, or a row fingerprint, of every table it read.
- The write is replace by closing, keyed by the primary key. A duplicated primary key fails the load.
- Databricks Feature Engineering is an optional, lazily imported adapter behind the `features` extra, with an
  explicit `exact` (STRING closing key) or `timeseries` (as-of) mode. Feature Views are not a dependency.

## Consequences

- Feature tables live wherever the project puts them; no catalog or schema is implied.
- Event-time as-of joins for sub-period sources and online serving are out of scope; a future `timeseries` mode of
  the declaration can add the former.
- `replace_partitions` writes that declare `keys` now check key nullness and uniqueness for every task, not only
  feature tables.
- The Feature Engineering adapter is proved offline against a fake client until a live run is added.
