---
type: decision
title: "0012. Feature engineering: one catalog, a numpy oracle and native Spark by default"
description: "Why windowed features are declared once and computed by a numpy reference, native Spark expressions or an Arrow engine."
resource: "src/bricksmith/features/**"
timestamp: "2026-11-23T09:30:00-03:00"
audience: both
authority: current
---

# 0012. Feature engineering: one catalog, a numpy oracle and native Spark by default

- Status: accepted
- Date: 2026-11-23
- Specification: 77 (epic 85)
- Extends: [0009](0009-one-library-shared-task-scopes.md) and
  [0010](0010-behavior-derived-independent-development.md)

## Context

Windowed features over long tables are often computed by packing each entity's history into a positional
float array and running row-at-a-time Python. That approach produces unnamed single-precision outputs,
recomputes each window, and has known statistical mistakes:

- absolute variation computed as a net change;
- moments taken about the wrong mean;
- R² computed on a constant series;
- terciles that divide by zero;
- percentiles that index past the window.

The product needs named outputs, exact definitions, and an engine that runs on serverless compute.

## Decision

- **One declarative catalog** (`bricksmith.features.catalog`) defines every aggregate: its family, output type
  and null rule. A `FeatureSpec` names outputs `<column>_<aggregate>_<window>p`.
- **A vectorized numpy reference** over `(rows × window)` blocks is the oracle. Golden tests pin every
  aggregate on hand-computed vectors, including the five mistakes above. Every other engine must equal it
  within 1e-9 relative.
- **Native Spark is the default engine.** Spark SQL expressions are generated from the spec over one shared
  window per spec, with no Python UDF, and ANSI-safe lookups and divisions.
- **An Arrow engine** (`applyInArrow`, else `applyInPandas`) runs the numpy reference per entity. Its cost is
  measured, not assumed. `DEFAULT_ENGINES` routes each family from the recorded benchmark; today every family
  is native, see the [evidence](../project/evidence/features-free.md).
- numpy and pyarrow live in the `features` extra. Importing `bricksmith.features` loads neither, and an engine
  call without them names the extra.

## Consequences

- A new aggregate needs a catalog entry, a reference implementation with a golden test, and a native
  expression. An aggregate with no native expression raises `UnsupportedAggregateError`, which points to the
  Arrow engine.
- The Arrow engine is Unsupported on Free Edition, because the free workspace refuses Python UDFs
  (`ISOLATION_STARTUP_FAILURE.SANDBOX_STARTUP`). It is proven locally: numpy parity on a local Spark session,
  plus a recorded benchmark. Its Databricks proof waits for an on-demand corporate run. No family defaults to
  Arrow until that proof exists.
