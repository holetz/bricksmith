---
type: how-to
title: "Feature selection"
description: "How to choose model inputs with a reproducible funnel and keep a per-variable selection ledger."
resource: "src/bricksmith/mlops/selection/**"
timestamp: "2026-11-23T12:00:00-03:00"
audience: both
authority: current
---

# Feature selection

`bricksmith.mlops.selection` chooses model inputs from a training set in a fixed, configurable funnel. It records
why every variable was kept or dropped. Each variable gets a ledger entry for every step it reached and every
metric it was judged on. The entry holds the value, the threshold and rule, the decision and the reason. The same
configuration over the same data reproduces the same ledger, so a reviewer can rerun the choice instead of
trusting a notebook.

The funnel reuses the monitoring core: stability bins come from `bricksmith.monitor.bins`, and PSI, KS and AUC
from `bricksmith.monitor.stats`. Thresholds are configuration with defaults. A default is a starting point, not a
standard your model must meet.

The [end-to-end model flow](model-flow.md) runs the funnel on a training ABT, stores the ledger and variables tables, and trains on the features it kept.

## Run it

```python
from bricksmith.mlops.selection import select_features

SELECTION = {
    "name": "churn_selection",
    "closing": "closing_date",  # the closing (snapshot) column
    "target": "churned",  # binary 0/1 by default; target_kind: "continuous" for a number
    "development": ["2025-01-31", "2025-12-31"],
    "out_of_time": ["2026-01-31", "2026-03-31"],
    "exclude": ["customer_id"],  # every other column is a candidate, or list them in "features"
    "expected_signs": {"days_past_due": "+"},
    "judgment": {
        "keep": {"tenure_months": "required by the model owner"},
        "drop": {"branch_code": "not available at scoring time"},
    },
}

result = select_features(training_frame, SELECTION)  # a Spark DataFrame, or a list of row dicts
result.selected  # ("days_past_due", "tenure_months", ...), in candidate order
result.ledger_rows()  # one row per variable, step and metric
result.variable_rows()  # one row per variable: selected, left_at, reason
```

The input is split by closing **before any metric**. Closings are compared as ISO text, and both ranges are
inclusive. Rows outside both ranges are ignored. Quality, univariate power, redundancy and the multivariate step
use the development sample. Stability compares development with out-of-time, and each closing with the one
before it.

## The steps

Steps run in this order. Disable one with `"steps": {"stability": false}`. A variable is evaluated only by the
steps it reaches.

| Step | Metrics (ledger `metric`) | Default rule |
| --- | --- | --- |
| `quality` | `missing_share`, `constant_share` (top non-null value share), `cardinality` (categorical only) | drop above 0.95, 0.99, 50 |
| `stability` | `psi_oot` (development vs out-of-time), `psi_mom_max` (worst consecutive closings) | drop above 0.25 |
| `univariate` | binary target: `iv`, `ks`, `gini`; continuous target: `spearman` (numeric), `eta` (categorical); `direction` when an expected sign is declared | drop when IV < 0.02, \|Spearman\| < 0.05, eta < 0.05; KS and Gini reported only |
| `redundancy` | `max_correlation` (numeric only), then `vif` | drop above 0.8, then above 10 |
| `multivariate` | `coefficient`, `permutation_importance` | off by default; drop a zero coefficient or importance ≤ 0 |
| `judgment` | `manual` | your declared keeps and drops, each with a reason |

- **Stability** fits quantile bins (numeric) or category bins on development. Missing values get their own bin,
  and categories outside the fitted set land in `unseen`. Set `"stability": {"psi_max": "critical"}` to use a
  sample-size-aware threshold for each comparison instead of a fixed number: `chi2_{B-1}(1 - alpha) * (1/N + 1/M)`,
  where `B` is the number of non-empty bins and `N` and `M` are the two sample sizes. `alpha` defaults to 0.05.
- **Univariate power** for a binary target computes WoE as `ln(share of non-events / share of events)` over
  quantile bins. With `"binning": "monotone"`, adjacent bins are pooled until the event rate moves in one
  direction. A group with no events or no non-events gets `smoothing` (0.5) added to both counts. Numeric KS and
  Gini use the raw values; categorical KS and Gini order the categories by event rate. The `direction` metric
  (AUC above or below 0.5, or the Spearman sign) is compared with `expected_signs`, and a mismatch drops the
  variable.
- **Redundancy** ranks the numeric survivors by univariate strength (IV, or the absolute Spearman or eta). Going
  down that list, each variable leaves when its absolute Pearson correlation with an already-kept variable is
  above the limit. The reason names the variable it duplicates. The VIF of what remains comes from the inverse of
  the correlation matrix over complete rows of a bounded sample. The largest VIF above the limit leaves, then VIF
  is recomputed. Categorical variables get an `info` entry and pass.
- **Multivariate** needs the `mlops` extra (`bricksmith[mlops]`, for scikit-learn). It fits an L1 logistic
  regression (`c`, `l1_ratio`) or an elastic net (`alpha`, `l1_ratio`) on standardized complete rows of the
  development sample. Permutation importance (`repeats`) is then measured on the out-of-time sample, or on
  development when out-of-time is empty. Without the extra, the step fails with a message that names it.
- **Judgment** runs last. A drop removes a variable. A keep reinstates a variable that an earlier step dropped,
  and its outcome reads `kept by judgment: <reason>`. Every judgment needs a reason.

Ledger decisions are `pass`, `drop`, `info` (reported but not judged: no threshold, or not computable) and
`keep`.

## Supervised transforms stay in training

`result.woe` holds the WoE tables fitted on development. `apply_woe(rows, result.woe)` returns new rows with
`<variable>_woe` columns for training or scoring; bins without development rows encode as 0. Use them inside the
model pipeline, and never write them into feature tables. A feature table stays unsupervised and shared, while a
WoE encoding belongs to one target and one development window.

## Store the ledger

Two tables, keyed for a merge, so a rerun with the same selection name and run id replaces its own rows:

| Table kind | Key | Columns |
| --- | --- | --- |
| `ledger` | `selection, run_id, variable, step, metric` | `config_fingerprint, step_order, value, threshold, rule, decision, reason, computed_at` |
| `variables` | `selection, run_id, variable` | `config_fingerprint, kind, selected, left_at, reason, computed_at` |

```python
from bricksmith.mlops.selection import log_selection, select_features, table_rows, to_frame, write_options


@task(kind="table", outputs="churn_selection_ledger", metadata=write_options("ledger"))
def churn_selection_ledger(ctx):
    frame = ctx.read("churn_training")
    result = select_features(frame, SELECTION)
    log_selection(result)  # params, metrics and JSON artifacts when an MLflow run is active
    rows = table_rows(result, "ledger", run_id="2026-03-31", now=datetime.now(UTC).isoformat())
    ctx.write("churn_selection_ledger", to_frame(frame.sparkSession, "ledger", rows))
```

`log_selection` logs the configuration as `selection.*` params, plus its fingerprint. It logs the candidate,
selected and per-step dropped counts as metrics, and writes `selection/ledger.json`, `variables.json`,
`features.json` and `woe.json` as artifacts. Without MLflow or an active run it does nothing and returns `False`.

## Engines

The row engine and the Spark engine compute the same aggregates and fold them with the same code. They produce
the same ledger, and a remote test proves this on the free workspace. Spark computes value counts per closing
(one row per distinct value), class counts and moments as conditional sums, and Spearman by joining mid ranks
back onto the rows. VIF and the multivariate step read a bounded sample (`sample_rows`, default 50,000, seeded
with `seed`). Below that bound, both engines use every row. Above it, Spark draws a seeded sample, and exact
parity is not promised.

## Limits

- Value counts collect one row per distinct value of each variable and closing. Very high-cardinality numerics
  cost as much as they do in stability fitting.
- Correlation and VIF cover numeric variables only. A categorical variable is judged by quality, stability,
  univariate power and judgment.
- There is no automated stepwise search over model families, and SHAP is not used.

The ledger, the selected features and the WoE tables feed the technical model document; see
[model evaluation and technical documentation](model-evaluation.md).
