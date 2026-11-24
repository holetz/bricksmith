---
type: how-to
title: "Windowed feature engineering"
description: "How to declare windowed features over a long table and compute them with the JVM kernel, native Spark, Arrow or the numpy reference."
resource: "src/bricksmith/features/**"
timestamp: "2026-11-24T17:30:00-03:00"
audience: both
authority: current
---

# Windowed feature engineering

`bricksmith.features` turns a long table (one row per entity and closing) into one row per entity and closing
with named windowed aggregates: the mean of the last 6 months, the slope of the last 12, the longest run of
missing values, and so on. You declare what to compute once. Every engine computes exactly the same values:

- A compiled JVM kernel (`engine="jvm"`, the default under `engine="auto"`). One Scala pass per entity covers
  its whole closing history. The JAR ships inside the bricksmith wheel.
- Native Spark expressions (`engine="spark"`). These are the kernel's fallback when it cannot run on the
  current compute, and the fallback is always recorded.
- An Arrow engine (`engine="arrow"`), which runs the numpy reference per entity on the cluster. It runs only
  when you ask for it. It is proven locally only; its Databricks proof waits for an on-demand corporate run.
- A numpy reference (`compute_rows`) over Python rows. It is the oracle of every test.

Install the extra for the numpy and Arrow engines: `pip install 'bricksmith[feature-engines]'`. The JVM and native
engines need no extra. Importing `bricksmith.features` loads none of numpy, pyarrow or pyspark.

## Declare the features

```python
from bricksmith.features import CategoricalCount, Difference, FeatureSpec, Ratio, WindowFeature

spec = FeatureSpec(
    keys=("customer_id",),
    closing="closing_date",
    period="month",  # month (default), quarter, week (Monday start) or day
    features=(WindowFeature(("balance", "payments"), (3, 6, 12), ("mean", "max", "slope", "null_run_max", "p90")),),
    ratios=(Ratio("payment_ratio_3p", "payments_mean_3p", "balance_mean_3p"),),
    differences=(Difference("balance_vs_mean_6p", "balance", "balance_mean_6p"),),
    categorical=(CategoricalCount("status", ("late", "paid"), (6,)),),
)
```

- **Window outputs** are named `<column>_<aggregate>_<window>p`: `balance_mean_6p`, `payments_p90_12p`.
- **Ratios and differences** read earlier outputs or source columns at the current closing; a ratio by zero is
  null.
- **Category counts** are named `<column>_count_<category>_<window>p` (`status_count_late_6p`) and count the
  closings in the window whose value is that category.

The same declaration as a plain dict, for configuration files, goes through `parse_spec`. It refuses unknown keys:

```python
from bricksmith.features import parse_spec

spec = parse_spec(
    {
        "keys": ["customer_id"],
        "closing": "closing_date",
        "features": [{"columns": ["balance"], "windows": [3, 6], "aggregates": ["mean", "slope"]}],
    }
)
```

## Input contract

- One row per entity (`keys`) and closing. A duplicate within one period is refused: `compute_rows` raises
  `FeatureError`, and Spark fails with `DUPLICATED_MAP_KEY`.
- A window of `w` periods holds the current closing and the `w - 1` periods before it, by calendar period, not
  by row. A missing closing and a null value are both null inside the window.
- One output row is produced per input row. `as_of` keeps only that closing and refuses any row after it with
  `PointInTimeError`, so a feature never reads the future. Filter the input first.

## Compute

```python
from bricksmith.features import compute_features, compute_rows

rows = compute_rows(records, spec)  # numpy, a list of dicts
frame = compute_features(df, spec)  # auto: the JVM kernel, native Spark if it cannot run
frame = compute_features(df, spec, kernel_function="main.features.bricksmith_kernel")  # an installed UC function
frame = compute_features(df, spec, engine="jvm")  # the kernel or an error, never a fallback
frame = compute_features(df, spec, engine="spark")  # native Spark expressions only
frame = compute_features(df, spec, engine="arrow")  # every window output on the Arrow engine
frame = compute_features(df, spec, engines={"trend": "arrow"})  # one family moved to Arrow
frame = compute_features(df, spec, as_of="2026-06-30")  # one closing, point in time
```

Category counts, ratios and differences are computed natively whichever engine computes the window outputs.

The JVM kernel groups each entity's history into one array, calls the kernel once per entity, and explodes the
result into one row per closing. With `as_of` it returns only that closing. The native engine builds Spark SQL expressions from the spec over one shared window per spec. It uses no Python
UDF, is ANSI-safe, and runs on serverless. The Arrow engine groups by the keys and uses `applyInArrow`, falling
back to `applyInPandas` on runtimes without it. The cluster needs numpy and pyarrow; bricksmith ships with the
function.

**Arrow support is locally proven only.** It matches the numpy reference on a local Spark session, through both
`applyInArrow` and `applyInPandas`. On Databricks it is **Unsupported on Free Edition**: the free workspace
refuses Python UDFs (`ISOLATION_STARTUP_FAILURE.SANDBOX_STARTUP`). Its proof on a workspace that allows Python
UDFs waits for an on-demand corporate run. Use the JVM kernel or native Spark wherever you need a
Databricks-proven engine.

## Make the JVM kernel available

The kernel reaches a Spark session in one of two ways. `auto` probes them once per session, in this order, and
caches what it found:

1. **A Unity Catalog Scala function**, tried when you pass `kernel_function=`. This is the path serverless runs:
   it is proven on Free Edition serverless, where it matched the reference on every output. Install it once per
   bricksmith version, with permission to create functions in the schema and write to the volume:

   ```bash
   bricksmith features install-kernel --volume main.features.jars --function main.features.bricksmith_kernel
   ```

   The command uploads the packaged JAR to the volume as `bricksmith-kernel-<version>-<sha12>.jar`, only when that
   file is missing. It then creates or replaces the function (`LANGUAGE SCALA`, `environment_version = '4'`). From a
   bundle task or a notebook, `bricksmith.features.cli.install_kernel(Clients(ctx.execution), volume, function)`
   does the same. Grant `EXECUTE` on the function to whoever computes features. The function runs in a sandbox, so
   the first call of a session pays a cold start (about 9 s on the free workspace).
2. **Session registration**, with no installation. The JAR is added to the session (`addArtifacts` on Spark
   Connect, `ADD JAR` on a classic session) and registered with `registerJavaFunction`. A classic session with
   dedicated access registers it directly. **Classic compute with standard access** also needs the JAR's
   location on the metastore's artifact allowlist (Catalog Explorer > metastore > Allowed JARs/Init Scripts),
   and only a metastore admin can add it. Serverless refuses this path with
   `ISOLATION_STARTUP_FAILURE.SANDBOX_STARTUP`.

## The engine record

Every `compute_features` call records its choice: the requested engine, the engine that computed the window
outputs (`mixed` when `engines=` split them), the registration path and, after a fallback, the reason for every
path that failed:

- The `bricksmith.features` logger writes the choice: a fallback as a warning, any other choice at info.
- In a task run by bricksmith, the task result in the run report carries the list under `feature_engines`:

  ```json
  {"requested": "auto", "engine": "spark", "path": "", "outputs": 540,
   "reason": "session: SparkRuntimeException: [ISOLATION_STARTUP_FAILURE.SANDBOX_STARTUP] ..."}
  ```

When a run is slower than you expect, read this record first: `engine: "spark"` with a reason means the kernel
did not run. `engine="jvm"` turns that silent slowdown into `KernelUnavailableError`.

## Default engine per family

`DEFAULT_ENGINES` in `bricksmith.features.engines` maps every family to the JVM kernel
([decision 0019](../decisions/0019-jvm-array-kernel-default-engine.md)). The
[recorded benchmark](../project/evidence/features-free.md) measured the kernel at about twice the speed of native
Spark for the whole catalog, on Free Edition serverless and locally, for every closing and for one closing. Measure
your own sizes with:

```bash
uv run --group remote python scripts/features_benchmark.py --sizes 2000x24x4 --engines reference,jvm,spark,arrow
```

In the benchmark, `jvm` runs with `engine="jvm"`, so on a compute where the kernel cannot register, the benchmark
fails rather than timing native Spark under the kernel's name.

The script reads its context from `BRICKSMITH_*` variables in the process environment, and from a `.env` file only
when one exists, so a CI runner or a borrowed workstation needs no file. It prints one progress line per measurement
to stderr and the markdown report to stdout. Two options help with long runs:

- `--budget SECONDS` stops cleanly between measurements once the time is spent. The report keeps what ran and says
  it is partial, listing the measurements not run. A measurement already started finishes, so the run can overshoot
  by up to one.
- `--single-closing` times the last closing of each size instead of every closing. Engine rankings can differ
  between the two modes, so measure the one your pipeline uses.

`--engines` takes any of `reference` and the engines this build ships (`spark`, `arrow`, `jvm`); a name it does
not know is refused with the list it does.

## Catalog

A window holds lags 0 (the current closing) to `w - 1`. `n` is the number of non-null values. Every aggregate is
null when `n` is below its threshold. A *step* is two chronologically adjacent non-null values; a null breaks it.
The trend aggregates regress the value on the chronological position, where the oldest position is 0.

| Aggregate | Family | Type | Null below n | Definition |
|---|---|---|---:|---|
| `count` | basic | bigint | 0 | number of non-null values |
| `count_positive` | basic | bigint | 0 | number of values greater than zero |
| `sum` | basic | double | 1 | sum of the non-null values |
| `mean` | basic | double | 1 | sum / n |
| `mean_zero` | basic | double | 1 | sum / w: the mean with nulls counted as zero |
| `min` | basic | double | 1 | smallest value |
| `max` | basic | double | 1 | largest value |
| `range` | basic | double | 1 | max - min |
| `last` | basic | double | 1 | the value at the current closing (null when it is null) |
| `mode` | distribution | double | 1 | most frequent value; ties go to the smallest value |
| `mode_count` | distribution | bigint | 1 | how many times the mode occurs |
| `distinct` | distribution | bigint | 0 | number of distinct non-null values |
| `null_run_trailing` | sequence | bigint | 0 | consecutive nulls from the current closing backwards |
| `null_run_max` | sequence | bigint | 0 | longest run of consecutive nulls |
| `increments` | sequence | bigint | 0 | number of rising steps |
| `decrements` | sequence | bigint | 0 | number of falling steps |
| `increment_run_current` | sequence | bigint | 0 | rising steps in a row up to the current closing |
| `decrement_run_current` | sequence | bigint | 0 | falling steps in a row up to the current closing |
| `increment_run_max` | sequence | bigint | 0 | longest run of rising steps |
| `decrement_run_max` | sequence | bigint | 0 | longest run of falling steps |
| `delta` | sequence | double | 2 | newest non-null value - oldest non-null value |
| `delta_mean` | sequence | double | 2 | delta / periods between the newest and the oldest non-null values |
| `abs_variation` | sequence | double | 2 | sum of the absolute step differences; null without a step |
| `since_positive` | sequence | bigint | 1 | periods since the latest positive value; null when none |
| `since_max` | sequence | bigint | 1 | periods since the latest occurrence of the maximum |
| `tercile_old_mean` | terciles | double | 1 | mean of the oldest third of the positions; null for windows under 3 |
| `tercile_mid_mean` | terciles | double | 1 | mean of the middle third of the positions; null for windows under 3 |
| `tercile_new_mean` | terciles | double | 1 | mean of the newest third of the positions; null for windows under 3 |
| `tercile_diff` | terciles | double | 1 | tercile_new_mean - tercile_old_mean; null for windows under 3 |
| `var_pop` | moments | double | 1 | population variance (0 for a constant series) |
| `var_samp` | moments | double | 2 | sample variance (0 for a constant series) |
| `std_pop` | moments | double | 1 | population standard deviation |
| `std_samp` | moments | double | 2 | sample standard deviation |
| `cv` | moments | double | 2 | std_samp / \|mean\|; null when the mean is 0 |
| `rms` | moments | double | 1 | square root of the mean of the squares |
| `skew` | moments | double | 3 | m3 / m2^1.5 about the window's own mean; null for a constant series |
| `kurtosis` | moments | double | 4 | excess kurtosis m4 / m2^2 - 3; null for a constant series |
| `slope` | trend | double | 2 | OLS slope of the value on the chronological position |
| `intercept` | trend | double | 2 | OLS intercept at the oldest position (t = 0) |
| `r2` | trend | double | 2 | 1 - SSE / SST; null for a constant series |
| `resid_std` | trend | double | 3 | sqrt(SSE / (n - 2)) |
| `trend_distance` | trend | double | 2 | current value - the trend at the current position; null if it is null |
| `p1`..`p99` | distribution | double | 1 | percentile, linear interpolation over the non-null values |

Definitions that differ from naive positional-array code, each covered by a golden test:

- `abs_variation` is the sum of absolute step differences.
- `skew` and `kurtosis` use the window's own mean.
- `r2` is 1 − SSE/SST and is null on a constant series.
- Terciles are null below three positions instead of dividing by zero.
- Percentiles never index past the window.

## Coming from a positional-array kernel

Code that packs each entity's history into an array indexed by lag (index 0 is the current closing) and computes
features row by row reports different numbers for some definitions. bricksmith regresses and differences in
chronological order and counts periods from the current closing. Five small entities, given lag first as such
code stores them, are golden cases that every engine must reproduce:

| Lag-ordered input | bricksmith | What differs |
|---|---|---|
| `[3, 2, 1]` (rising over time) | `slope` 1, `delta` 2, `delta_mean` 1, `since_max` 0 | the trend is chronological, so a series rising over time has a positive slope, not the slope over the lag index (-1) |
| `[4, null, 1]` | `count` 2, `mean` 2.5, `delta` 3, `delta_mean` 1.5 | `delta_mean` divides by the periods between the oldest and newest non-null values (2), not by the number of values minus one |
| `[5, null, null]` | `count` 1, `delta` null, `slope` null | one value has no change and no trend: null, not 0 |
| `[null, null, null]` | `count` 0, `sum` null, `since_max` null | an all-null window counts 0; every value aggregate is null |
| `[2, 9, 4]` | `since_max` 1, `since_positive` 0 | periods since an event are 0-based lags (0 is the current closing), not 1-based indexes |

`kurtosis` is the **excess** kurtosis (m4 / m2² − 3), so a normal series is near 0. A kernel that reports plain
kurtosis (m4 / m2²) differs by exactly 3. Subtract 3 from its values before comparing.

See [decision 0014](../decisions/0014-feature-engineering-engines.md) for the catalog and the oracle, and
[decision 0019](../decisions/0019-jvm-array-kernel-default-engine.md) for the default engine.
