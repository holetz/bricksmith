---
type: project
title: "Feature engineering evidence: free environment"
description: "Remote parity and benchmark evidence for the feature-engineering engines on the free Databricks environment."
resource: "tests/remote/test_features*.py"
timestamp: "2026-11-24T17:00:00-03:00"
audience: both
authority: current
---

# Feature engineering evidence: free environment

- Date: 2026-11-23
- Specification: 77 (epic 85), `bricksmith.features`
- Environment: `free` (named profile, serverless compute). The workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`).
- Versions: databricks-connect 17.3 line (pyspark 4.0, ANSI mode on), Python 3.12, numpy reference engine
- Data: seeded synthetic tables only (nulls, missing closings, one- and two-closing histories, constant series)

## Parity scenarios

| Scenario | Command | State | Note |
|---|---|---|---|
| Native Spark equals the numpy reference on every output (whole catalog, p10/p50/p95, windows 1–12, ratios, differences, category counts) | `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_features.py -q` | passed | 2 passed; tolerance 1e-9 relative |
| Native Spark refuses rows after `as_of` and returns only that closing | same | passed | `PointInTimeError` |
| Arrow engine equals the reference; `auto` with mixed engines equals the reference | `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_features_arrow.py -q` | unsupported | the free workspace refuses every Python UDF: a plain `udf`, `mapInPandas` and `applyInArrow` all fail with `ISOLATION_STARTUP_FAILURE.SANDBOX_STARTUP` (exec format error). The test probes for this and skips with that reason (2 skipped). Alternative: the local Arrow evidence below; corporate proof is on demand |

The Arrow engine, `applyInPandas` fallback and mixed `auto` routing were checked against the reference on a
local Spark (pyspark 4.0.1, `local[2]`, ANSI on): zero mismatches on every output. This is supporting
the accepted Arrow evidence (epic 85 decision of 2026-11-23). The Databricks proof of the Arrow engine waits for an
on-demand corporate run.

## Benchmark

Command: `uv run --group remote python scripts/features_benchmark.py --engines reference,spark --sizes 200x24x4,2000x24x4`.
Seconds per family; sizes are entities × monthly closings × value columns; windows 3, 6 and 12. `source` is
the time to materialize the generated input alone. One warm-up run per engine is excluded.

### Free environment (serverless): reference and native Spark

| Family | Engine | Outputs | 200x24x4 (s) | 2000x24x4 (s) |
|---|---|---:|---:|---:|
| all | source | 0 | 1.62 | 0.76 |
| basic | reference | 108 | 0.99 | 9.28 |
| basic | spark | 108 | 2.19 | 3.42 |
| distribution | reference | 72 | 1.19 | 7.61 |
| distribution | spark | 72 | 4.27 | 4.38 |
| sequence | reference | 156 | 1.45 | 13.68 |
| sequence | spark | 156 | 4.61 | 6.06 |
| terciles | reference | 48 | 0.44 | 5.27 |
| terciles | spark | 48 | 2.67 | 2.40 |
| moments | reference | 96 | 0.83 | 8.58 |
| moments | spark | 96 | 3.81 | 3.48 |
| trend | reference | 60 | 0.60 | 5.45 |
| trend | spark | 60 | 3.31 | 3.49 |
| all | reference | 540 | 5.25 | 49.89 |
| all | spark | 540 | 9.23 | 14.04 |

### Local Spark (`local[4]`, 12 cores): native Spark and Arrow

| Family | Engine | Outputs | 200x24x4 (s) | 2000x24x4 (s) |
|---|---|---:|---:|---:|
| all | source | 0 | 2.46 | 0.18 |
| basic | spark | 108 | 2.13 | 1.59 |
| basic | arrow | 108 | 1.16 | 4.22 |
| distribution | spark | 72 | 3.56 | 4.24 |
| distribution | arrow | 72 | 1.06 | 4.53 |
| sequence | spark | 156 | 2.65 | 6.15 |
| sequence | arrow | 156 | 1.46 | 7.33 |
| terciles | spark | 48 | 0.90 | 1.53 |
| terciles | arrow | 48 | 0.75 | 3.57 |
| moments | spark | 96 | 1.45 | 3.18 |
| moments | arrow | 96 | 0.92 | 5.50 |
| trend | spark | 60 | 1.57 | 3.84 |
| trend | arrow | 60 | 0.81 | 3.22 |
| all | spark | 540 | 7.48 | 20.63 |
| all | arrow | 540 | 3.62 | 19.25 |

## JVM array kernel (spec 91, decision 0019)

- Date: 2026-11-24. The kernel JAR is built by `scripts/build_kernel.py` (Scala 2.13.16, `spark-sql-api` 4.0.1
  provided) and packaged in the wheel.
- Data: the same seeded synthetic tables as above.

### Registration paths

| Path | Where | State | Note |
|---|---|---|---|
| Unity Catalog Scala function (`bricksmith features install-kernel`, `LANGUAGE SCALA`, `environment_version = '4'`) | free, serverless | passed | `auto` records `jvm` over `unity-catalog`; `auto` and `engine="jvm"` equal the reference on every output (whole catalog, p10/p50/p95, windows 1–12, ratios, differences, category counts). Install about 5 s; the first call (probe) about 9 s, once per session |
| Session registration (`addArtifacts` and `registerJavaFunction`) | free, serverless | unsupported | `ISOLATION_STARTUP_FAILURE.SANDBOX_STARTUP`: serverless refuses session-scoped JVM UDFs. Without a named function, `auto` runs native and records this reason |
| Session registration (`ADD JAR` and `registerJavaFunction`) | local Spark 4.0.1, classic | passed | `tests/jvm`: 21 passed (golden vectors, five migration entities, all closings, `as_of`, recorded fallback when the JAR is missing) |

Command for the remote proof: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest
tests/remote/test_features_jvm.py -q -rs` (1 passed). The handler signature was settled on the free workspace:
the Unity Catalog matcher accepts Scala case classes and `Option[Int]`, and refuses Spark `Row` for a STRUCT and
`java.lang.Integer` for an INT, so the function calls `WindowKernel.handle` over case classes, and the session
UDF keeps `Row`. A Scala test pins that both return the same values.

Probe order: the Unity Catalog function first when one is named, then the session registration. Serverless runs
only the function. A classic session registers the JAR itself.

### Free environment (serverless): JVM kernel (Unity Catalog) and native Spark

The whole catalog (`all`, 540 outputs), seconds. Each measurement was run twice, warm-up included, on the same
generated frame; the second run is shown.

| Size | Closings | jvm (s) | spark (s) |
|---|---|---:|---:|
| 200x24x4 | all | 4.20 | 8.88 |
| 200x24x4 | one (`as_of`) | 3.83 | 8.45 |
| 2000x24x4 | all | 4.94 | 9.42 |
| 2000x24x4 | one (`as_of`) | 3.84 | 7.54 |

### Local Spark (`local[4]`, 12 cores, Java 21): JVM kernel (session) and native Spark

Command: `scripts/features_benchmark.py`'s `run_benchmark` on a classic local session, engines `jvm,spark`.

All closings:

| Family | Engine | Outputs | 200x24x4 (s) | 2000x24x4 (s) |
|---|---|---:|---:|---:|
| all | source | 0 | 1.09 | 0.17 |
| basic | jvm | 108 | 0.67 | 1.01 |
| basic | spark | 108 | 0.86 | 1.18 |
| distribution | jvm | 72 | 0.67 | 0.86 |
| distribution | spark | 72 | 1.45 | 2.41 |
| sequence | jvm | 156 | 0.93 | 1.63 |
| sequence | spark | 156 | 2.00 | 3.90 |
| terciles | jvm | 48 | 0.92 | 0.51 |
| terciles | spark | 48 | 0.46 | 1.05 |
| moments | jvm | 96 | 0.53 | 1.06 |
| moments | spark | 96 | 0.80 | 1.99 |
| trend | jvm | 60 | 0.51 | 0.83 |
| trend | spark | 60 | 0.97 | 2.62 |
| all | jvm | 540 | 2.27 | 4.79 |
| all | spark | 540 | 4.02 | 11.23 |

One closing (`--single-closing`):

| Family | Engine | Outputs | 200x24x4 (s) | 2000x24x4 (s) |
|---|---|---:|---:|---:|
| all | source | 0 | 0.12 | 0.10 |
| basic | jvm | 108 | 0.46 | 0.53 |
| basic | spark | 108 | 0.54 | 0.75 |
| distribution | jvm | 72 | 0.37 | 0.45 |
| distribution | spark | 72 | 0.75 | 0.90 |
| sequence | jvm | 156 | 0.53 | 0.70 |
| sequence | spark | 156 | 0.87 | 1.23 |
| terciles | jvm | 48 | 0.33 | 0.24 |
| terciles | spark | 48 | 0.37 | 0.52 |
| moments | jvm | 96 | 0.43 | 0.60 |
| moments | spark | 96 | 0.67 | 0.80 |
| trend | jvm | 60 | 0.33 | 0.45 |
| trend | spark | 60 | 0.75 | 0.79 |
| all | jvm | 540 | 1.59 | 1.68 |
| all | spark | 540 | 2.65 | 3.38 |

The kernel is faster than native Spark for the whole catalog at every size and closing scope, about twice as fast
at 2000 entities. Only terciles at the small size were slower locally. Each family's time includes its own
grouping pass.

## Default engine per family

Since decision 0019, every family defaults to the JVM kernel (`DEFAULT_ENGINES`). Native Spark is its fallback,
and `auto` records the reason when it falls back. The analysis below, from spec 77, chose native Spark before the
kernel existed:

- Native Spark is the only Spark engine proven on the free environment.
- Locally, at 2000 entities, native Spark is faster or within noise for basic, distribution, sequence,
  terciles and moments. Arrow is faster for trend (3.2 s against 3.8 s) and for the whole catalog in one
  pass (19.3 s against 20.6 s). Neither margin justifies a Python UDF path that serverless may refuse.
- On serverless, native Spark computes the whole catalog for 48,000 rows in about 14 s, against 50 s for
  the single-process reference.

Arrow stays available with `engine="arrow"` or per family through `engines=`.
