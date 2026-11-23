---
type: how-to
title: "Windowed feature engineering"
description: "How to declare windowed features over a long table and compute them with the numpy, native Spark or Arrow engine."
resource: "src/bricksmith/features/**"
timestamp: "2026-11-23T09:30:00-03:00"
audience: both
authority: current
---

# Windowed feature engineering

`bricksmith.features` turns a long table (one row per entity and closing) into one row per entity and closing
with named windowed aggregates: the mean of the last 6 months, the slope of the last 12, the longest run of
missing values, and so on. You declare what to compute once. Every engine computes exactly the same values:

- A numpy reference (`compute_rows`) over Python rows. It is the oracle of every test.
- Native Spark expressions (`engine="spark"`, the default).
- An Arrow engine (`engine="arrow"`) that runs the numpy reference per entity on the cluster.

Install the extra for the numpy and Arrow engines: `pip install 'bricksmith[features]'`. Importing
`bricksmith.features` loads none of numpy, pyarrow or pyspark.

## Declare the features

```python
from bricksmith.features import CategoricalCount, Difference, FeatureSpec, Ratio, WindowFeature

spec = FeatureSpec(
    keys=("customer_id",),
    closing="closing_date",
    period="month",  # month (default), quarter, week (Monday start) or day
    features=(
        WindowFeature(("balance", "payments"), (3, 6, 12), ("mean", "max", "slope", "null_run_max", "p90")),
    ),
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

spec = parse_spec({
    "keys": ["customer_id"],
    "closing": "closing_date",
    "features": [{"columns": ["balance"], "windows": [3, 6], "aggregates": ["mean", "slope"]}],
})
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

rows = compute_rows(records, spec)                          # numpy, a list of dicts
frame = compute_features(df, spec)                          # Spark, the default engine per family
frame = compute_features(df, spec, engine="arrow")          # every window output on the Arrow engine
frame = compute_features(df, spec, engines={"trend": "arrow"})  # one family moved to Arrow
frame = compute_features(df, spec, as_of="2026-06-30")      # one closing, point in time
```

The native engine builds Spark SQL expressions from the spec over one shared window per spec. It uses no Python
UDF, is ANSI-safe, and runs on serverless. The Arrow engine groups by the keys and uses `applyInArrow`, falling
back to `applyInPandas` on runtimes without it. The cluster needs numpy and pyarrow; bricksmith ships with the
function. Workspaces that refuse Python UDFs cannot run the Arrow engine.

## Default engine per family

`DEFAULT_ENGINES` in `bricksmith.features.engines` maps every family to native Spark. The
[recorded benchmark](../project/evidence/features-free.md) chose it: native Spark is the only engine proven on
serverless, and Arrow is never faster by enough to justify a Python UDF. Measure your own sizes with:

```bash
uv run --group remote python scripts/features_benchmark.py --sizes 2000x24x4 --engines reference,spark,arrow
```

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

See [decision 0012](../decisions/0012-feature-engineering-engines.md) for why there are three engines.
