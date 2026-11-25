---
type: how-to
title: "Monitoring: references, drift, model quality and alerts"
description: "How to monitor references, drift, model quality and alerts from tables."
resource: "src/bricksmith/monitor/**"
timestamp: "2026-10-14T13:52:10-03:00"
audience: both
authority: current
---

# Monitoring: references, drift, model quality and alerts

Monitoring reads **tables** (features, or predictions with score, label, segment and time columns); it never calls a
model. Definitions, formulas and edge cases are in the [BS-13 specification](https://github.com/holetz/bricksmith/blob/quenching/specs/archive/20.md).

To watch an ordinary table period by period, declared on the task that writes it and gated in the run, see
[data stability](stability.md); it shares bins, PSI and references with this page.
To monitor a model declared on the task that writes its predictions, with maturation-aware periods, a run record
and dashboard views, see [model monitoring on tasks](model-monitoring.md).

## Fit a reference, compare a window

```python
from bricksmith.monitor import fit_reference, measure, monitor_frames

reference = fit_reference(
    fit_df,  # a Spark DataFrame, or a list of row mappings (pure Python, identical results)
    {"score": {"edges": [0, 0.25, 0.5, 0.75, 1.0]}, "age": {"bins": 4}, "plan": {"type": "categorical"}},
    name="churn",
    version=1,
    score="score",
    label="churned",
    segment="plan",
    time="snapshot_date",
    fit_window={"column": "snapshot_date", "start": "2026-01-01", "end": "2026-02-01"},
    alert_policy=[{"name": "drift", "metric": "psi", "op": ">", "threshold": 0.25, "severity": "critical"}],
)
measurements = measure(reference, current_df, {"column": "snapshot_date", "start": "2026-03-01", "end": "2026-04-01"})
```

The reference stores bin edges, categories and the fit counts (JSON via `reference.to_json()`, SHA-256
`fingerprint`, no timestamps), so comparisons never see fit data and never refit. Bins are numeric (`quantile` with
`bins`, or fixed `edges`) or categorical; nulls, out-of-range values (`outliers: separate|clip`) and unseen
categories have explicit bins, and zero shares are floored at `epsilon` (default `1e-4`) in PSI.

Metrics: `psi` (score) and `csi` (characteristics) with per-bin contributions, `coverage`, `null_rate`, `stability`
(max PSI across `day`/`month` periods), `row_count`, `volume_ratio`, and with a label `auc`, `gini`, `ks`, `brier`,
`ece`. A metric that cannot be computed has `status: unavailable`, no value and a `reason` (`empty_window`,
`missing_labels`, `invalid_labels`, `single_class`, `score_out_of_range`, `insufficient_periods`, ...).

## Alert rules

`AlertRule(name, metric, op, threshold, severity, subject="*", segment="", min_count, min_history)`. Each rule on each
matching measurement ends in one state: `alert`, `ok`, `unavailable`, `insufficient_count` or `insufficient_history`
(prior ok values for that key, passed as `history`). Only `alert` emits a `bricksmith.ops.AlertEvent` with error
class `statistical_alert`; hand it to `AlertNotifier` for delivery. Segment values are measured separately; a rule
looks at them only with `segment="*"`.

## In a project: ordinary tasks

`examples/shared_schema/catalogs/demo/ml/monitoring.py` sits beside the feature and model tasks of the same schema:
`monitor_references` (fit and version), `monitor_measurements`, `monitor_alerts` (evaluations and fired alerts), all
kind `monitor` with `write: merge` keyed by reference, version, window and metric/rule, so a repeat run changes
nothing. Retention, history and lineage are the shared storage and graph contracts; there are no hidden writes. The
prediction table in the example is a declared synthetic stand-in; any task that writes score/label columns replaces it.

## Command line and outside a project

```bash
bricksmith monitor fit --config fit.json --data fit.csv --out reference.json    # or --table catalog.schema.table
bricksmith monitor compare --reference reference.json --data now.jsonl --window-start 2026-03-01 --window-end 2026-04-01
bricksmith --json monitor evaluate --reference reference.json --data now.jsonl --fail-on-alert   # exit 4 on alert
```

`monitor_frames(current, reference=..., window=...)` (or `baseline=` plus `columns=` to fit on the spot) computes
over DataFrames or row lists without a project and returns measurements, evaluations and alert events.

## Category and segment spelling

Both engines spell categories and segment names with one canonical text, so a reference, baseline or alert rule
keyed on one engine matches on the other:

- booleans are `true`/`false`;
- null and NaN are null: the `null` bin, the `(null)` segment, and an unlabeled row when the label is NaN;
- doubles are spelled as Python spells them (`10000000.0`, `1e-05`), which is how stored references already keep
  their categories. The Spark engine matches double categories by value instead of casting them to text, and names
  segments from their distinct values on the driver.

Migration: earlier releases named boolean segments `True`/`False` on the pure engine (the Spark engine already wrote
`true`/`false`). Per-segment measurements, baselines and segment-specific alert rules recorded by the pure engine
under the capitalized names do not match the new names; rename those segment values to `true`/`false`, or let the
history rebuild from the next runs.

## Limits

Quantile fitting, AUC and KS collect one small row per distinct value (grouped remotely), so very high-cardinality
continuous columns should use fixed edges or be sampled. Window bounds are compared as ISO text.
