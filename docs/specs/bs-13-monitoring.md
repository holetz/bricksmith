---
phase: archive
outcome: done
date: 2026-10-14
---

# BS-13. Data and model monitoring

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/monitoring.md); builds on
[decision 0008](../decisions/0008-monitor-succeeds-psi-spark.md). Monitoring reads prediction and label tables (score,
label, segment and time columns named in the reference), never a model API.

## Statistical definitions (independent; closes the epic's "statistical definitions" row)

- **Numeric bins.** `edges e_0 <= ... <= e_m` give bins `[e_0, e_1]`, `(e_1, e_2]`, ... `(e_{m-1}, e_m]` (right-closed,
  first bin also closed on the left). *Fixed*: edges are given (strictly increasing). *Quantile* with `B` bins on `n`
  non-null finite fit values: cut `j` (1..B-1) is the nearest-rank value `x_(ceil(j*n/B))`; edges are
  `[min, distinct cuts below max, max]`. Equal values never create empty bins; constant data gives one bin.
- **Outliers.** Values below `e_0` or above `e_m` (infinities included) go to bins `below` / `above` (`outliers:
  separate`, default) or are folded into the first / last bin (`clip`). Null and NaN always go to the explicit bin `null`.
- **Categorical bins.** Categories kept at fit: count >= `min_count` (default 1), at most `max_categories` (default 20),
  ordered by count descending then value. Everything else non-null, in the fit or later, goes to `unseen`; nulls to `null`.
  Booleans are the strings `true`/`false`, other values their `str`.
- **Reference.** Holds bins with their fit counts, so a comparison never touches fit data and never refits. Serializable,
  versioned (`version`, SHA-256 `fingerprint` over canonical content, no timestamps), with fit source, fit window,
  `metric_config` (`epsilon`, `calibration_bins`, `stability_period`), evaluation window and alert policy.
- **PSI / CSI.** `p_i = r_i/R`, `q_i = c_i/C`; floor both at `epsilon` (default 1e-4, no renormalizing):
  `PSI = sum (q'_i - p'_i) * ln(q'_i / p'_i)`. PSI is reported for the score column, CSI (same formula, per bin
  contributions in `detail`) for every other characteristic. `R = 0` is `empty_reference`, `C = 0` is `empty_window`.
- **Coverage.** `coverage = (rows in non-null, in-range, seen bins) / rows`; `null_rate`; `row_count`;
  `volume_ratio = rows / fit rows`.
- **Stability.** PSI of each period (`day` = ISO date prefix, `month` = 7 characters of the time column) against the
  reference; value = maximum, per-period values in `detail`; fewer than two periods is `insufficient_periods`.
- **Discrimination** over rows with a label and a non-null score. `AUC = (sum of mid-ranks of positives - P(P+1)/2) / (P*N)`
  (ties count one half); `Gini = 2*AUC - 1`; `KS = max over distinct scores t |F_pos(t) - F_neg(t)|` with cumulative shares
  computed per distinct score (ties are one step). Unavailable: `empty_window`, `missing_labels`, `invalid_labels` (any
  label outside 0/1), `single_class`.
- **Calibration** with `B` equal-width score bins, `bin = min(floor(score*B), B-1)`: per bin count, mean score, observed
  rate; `ece = sum (n_b/N) |observed_b - mean_b|`; `brier = mean (score - label)^2`. A score outside [0, 1] makes both
  `score_out_of_range`.
- **Never normal-looking.** A metric that cannot be computed has `status: unavailable`, `value: null` and a `reason`.

## Alerts

`AlertRule(name, metric, op, threshold, severity, subject="*", segment="", min_count=0, min_history=0)`. Evaluation state
order: `unavailable` (reason from the metric), `insufficient_count`, `insufficient_history` (fewer prior ok measurements
of the same key than `min_history`), then `alert` or `ok`. Only `alert` emits `bricksmith.ops.AlertEvent` (kind
`monitor_threshold`, `error_class: statistical_alert`, `occurred_at` = window end so reruns are identical).

## Persistence and engines

Four tables written by ordinary `monitor` tasks with the `merge` strategy (idempotent): references (`reference`,
`version`), measurements, evaluations and alerts (window and rule keys; segment `""` is the whole population).
Retention, lineage and history are the BS-04 contracts; no hidden writes. Both engines reduce data to a `Summary` and
share one fold: `measure` (lists of mappings) and `monitor.spark.measure_spark` (aggregations run remotely).

## Contract

`bricksmith.monitor`: `fit_reference`, `measure`, `evaluate`, `Reference`, `BinSpec`, `AlertRule`, `Measurement`,
`Evaluation`, `monitor_frames` (outside-project adapter), `persist` row builders. CLI `bricksmith monitor fit|compare|evaluate`.

## Deviations and limits

Spark quantile fitting, AUC and KS collect per-distinct-value counts (grouped remotely), so continuous high-cardinality
columns return one small row per distinct value. Windows compare ISO strings. Free-form per-segment rules need `segment: "*"`.
Prediction tasks of BS-12 are not assumed: the example declares a synthetic prediction table.

## Acceptance tests

`tests/unit/monitor/` (hand-derived bins, PSI/CSI, null/unseen/empty/zero cases, AUC ties, KS, calibration, no refit,
shift alert, missing labels, insufficient history, CLI, example graph) and `tests/remote/test_monitoring.py`
([evidence](../evidence/bs-13-free.md)).

## Outcome

The implementation has real Free Edition proof for Spark/pure-engine parity, shared-runner
persistence, unavailable metrics, threshold alerts and repeat-run idempotence; its test-owned
schemas were removed (see the evidence above). Recovery on 2026-10-14 preserves that proof and
passes the unchanged local gate (Ruff lint and format, Pyright, 415 tests passed / 34 skipped).
High-cardinality aggregation limits remain as documented above. This achieved implementation
and acceptance scope is archived; independent verification remains required before push or PR.
BS-12's failed model registration proof does not supply monitoring evidence and remains open.
