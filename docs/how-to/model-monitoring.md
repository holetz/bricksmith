---
type: how-to
title: "Model monitoring on tasks"
description: "How to declare model monitoring on the task that writes predictions and read its dashboard-ready results."
resource: "src/bricksmith/monitor/model/**"
timestamp: "2026-11-23T18:00:00-03:00"
audience: both
authority: current
---

# Model monitoring on tasks

Model monitoring tells you, on every run, whether a model still behaves the way it did when it was developed. You
declare it on the task that writes the model's predictions. After the write, the shared runner measures the
predictions period by period against the development reference (and, when declared, the out-of-time reference),
colors every metric with a traffic light (fixed bands, a deterioration relative to a reference, or a control chart),
checks its trend, opens alerts for lights that persist, stores the results in five dashboard-ready tables, and
applies the gate you declared: warn (the default) or fail the task and block its dependents.

It reads tables only; it never calls the model. It shares its statistical core with
[monitoring](monitoring.md) (bins, PSI, KS, AUC, calibration, limits, trend, persistence) and can share feature bins with
[data stability](stability.md). Spark and the model monitoring package load only for tasks that declare it.

## Declare it on a task

The prediction table carries the development and out-of-time scored rows beside the production rows, told apart by
a sample column (or by a period range).

```python
@task(
    kind="table",
    outputs="churn_scores",
    metadata={
        "model_monitoring": {
            "model": "churn",  # required: the model name on every row
            "period": "month",  # required: the column that splits rows into periods
            "score": "score",  # required: the predicted probability or score
            "label": "label",  # enables discrimination and calibration
            "grade": "grade",  # optional rating column, judged like a feature (grade_psi)
            "segment": "region",  # optional: one column; every metric is also measured per segment
            "sample": "sample",  # the column telling reference rows from production rows
            "references": {
                "development": {"sample": "dev"},  # required
                "out_of_time": {"sample": "oot"},  # optional; or {"start": "2025-04", "end": "2025-05"}
            },
            "features": ["age", "plan"],  # columns watched with CSI
            "critical_fields": ["income"],  # columns whose null rate is watched
            "model_version_column": "version",  # or "model_version": "3"
            "label_lag": 1,  # labels mature one period later
            "gate": {"fail_on": ["red"]},  # default: warn only
        }
    },
)
def churn_scores(ctx):
    ctx.write("churn_scores", score_customers(ctx))
```

| Key | Meaning |
| --- | --- |
| `output` | The task output to watch; required only when the task has several outputs. A list of declarations needs distinct outputs. |
| `model`, `period`, `score` | Required: model name, period column and score column. |
| `label`, `grade`, `segment`, `sample` | Optional column roles (see the example). |
| `references` | `development` (required) and `out_of_time`; each `{"sample": <value>}` or an inclusive `{"start", "end"}` period range. |
| `features`, `critical_fields` | Columns for CSI and for completeness. A feature may carry bin settings: `{"age": {"type": "numeric", "bins": 5}}`. |
| `feature_binning` | `own` (default: fit on development) or `{"stability": "<dataset>"}` to reuse the latest stored stability binning of that dataset (`prefix` defaults to `stability_`). |
| `metrics` | A subset of the families below (default: every family the declared columns allow). |
| `model_version` / `model_version_column` | A literal version, or the column holding it. |
| `label_lag` | How many observed periods a label takes to mature (default 0); the default lag of discrimination and calibration. |
| `lags` | Per metric or family, how far back it reads: an integer or `{"periods": n}` (observed periods) or `{"months": n}` (calendar months). See [Periods and maturation](#periods-and-maturation). |
| `limits` | Per metric `{"green", "red", "direction", "center", "kind", "reference"}` overriding the defaults; `null` drops a light. See [Limit kinds](#limit-kinds). |
| `control_min_points` | Reference periods a control-chart baseline needs (default 3). |
| `trend`, `trend_metrics` | Trend check `{"window", "min_points", "yellow", "red"}` (default 12, 6, 2, 3; `null` disables) and the metrics it applies to (default `psi`, `ks`, `auc`, `calibration_ratio`). |
| `persistence` | When alerts open, escalate and close: `{"yellow", "red", "close_after", "immediate_red", "recurrence"}` (default 3, 2, 3, false, 12). |
| `gate` | `fail_on` (`red`, `yellow`, `critical`, `alert`) and `on_error` (`warn`, the default, or `fail`). |
| `min_rows`, `max_periods`, `score_bins`, `calibration_bins`, `epsilon` | Volume floor for a light, monitored periods kept (default 24), score bins (default 10), calibration bins, PSI smoothing. |
| `scope`, `prefix` | Where the tables live (default: the output's schema) and their name prefix (default `model_monitoring_`). |

Unknown keys and invalid values are plan errors (`invalid_model_monitoring`), so a typo fails `bricksmith plan`
instead of silently watching nothing.

## Metric families

| Family | Metrics | Needs |
| --- | --- | --- |
| `score_psi` | `psi` of the score, bins frozen on development | always |
| `feature_csi` | `csi` per feature | `features` |
| `grade_psi` | `grade_psi`, the grade distribution | `grade` |
| `discrimination` | `ks`, `auc`, `gini` | `label` |
| `calibration` | `brier`, `ece`, `calibration_ratio` (observed / expected events) | `label` |
| `volumetry` | `row_count`, `volume_ratio` (rows per period / development rows per period) | always |
| `completeness` | `null_rate` per critical field | `critical_fields` |

Default limits color `psi`, `csi` and `grade_psi` (green below 0.10, red from 0.25), `volume_ratio` (green within
±20% of 1, red beyond ±50%) and `calibration_ratio` (green within 0.7–1.3, yellow within 0.5–1.5, red beyond). Other
metrics carry the light `no_rule` until you declare limits for them, for example
`"limits": {"ks": {"green": 0.3, "red": 0.2, "direction": "lower"}}`. A result measured on fewer than `min_rows`
rows is `insufficient_volume`.

## Limit kinds

A limit's `kind` decides which number its bands judge. Every result stores it as `compared_value`, beside
`limit_kind` and `limit_reference`.

| Kind | Judges | Example |
| --- | --- | --- |
| `absolute` (default) | the value itself | `{"green": 0.3, "red": 0.2, "direction": "lower"}` |
| `relative` | `(value - reference value) / abs(reference value)`, the reference being `development` (default) or `out_of_time` | KS falling more than 15% is yellow, more than 30% red: `{"kind": "relative", "green": -0.15, "red": -0.30, "direction": "lower"}` |
| `control` | `(value - mean) / sd`, the mean and sample standard deviation of the metric measured per period over the development **and** out-of-time references | z beyond 2 yellow, beyond 3 red: `{"kind": "control", "green": 2, "red": 3, "direction": "both"}`; only a fall: `{"kind": "control", "green": -2, "red": -3, "direction": "lower"}` |

`direction` keeps its meaning on the compared value: `lower` for a quantity whose fall is bad, `higher` for a rise,
`both` for a distance either way (with `center`). A relative limit whose reference value is missing or zero, and a
control limit with fewer than `control_min_points` reference periods, give the light `no_baseline`. Control
baselines are stored per result (`baseline_mean`, `baseline_sd`, `baseline_points`). Measuring them costs one pass
per reference period, for the metrics declared with control limits only.

```python
"limits": {
    "ks": {"kind": "relative", "green": -0.15, "red": -0.30, "direction": "lower"},
    "auc": {"kind": "relative", "green": -0.05, "red": -0.10, "direction": "lower", "reference": "out_of_time"},
    "gini": {"kind": "control", "green": 2.5, "red": 3.5, "direction": "both"},
    "calibration_ratio": {"green": 0.3, "red": 0.5, "direction": "both", "center": 1.0},
}
```

## Trend and persistence

Each model version, segment, column and metric is a series over the monitoring periods. For the `trend_metrics`
with a light, the trend check fits a least-squares line through the previous compared values (up to `window`, at
least `min_points`) and judges how far the current one departs from it, in the limit's direction: `trend_status`,
`trend_expected` and `trend_deviation` on the result (`insufficient_history` while the series is short).

The series of lights is then replayed into **persistence** alerts: `yellow` consecutive yellow-or-red periods open
a warning, `red` consecutive red periods open (or escalate to) critical, `close_after` green periods close it, and
an alert opening within `recurrence` periods of the last close is flagged as a recurrence. Suppressed lights
(`no_data`, `no_baseline`, ...) neither count nor break a run. Trend statuses are replayed into **trend** alerts the
same way, one red trend opening a critical alert.

Each family is measured in isolation: a family that raises writes its result rows with no value, light `no_data`
and reason `error: <type>: <message>`, marks the run `partial`, and leaves the other families intact.

## Periods and maturation

Monitoring periods are the latest `max_periods` periods outside the references. Every result row carries two
periods:

- `monitoring_period`: the period the dashboard shows the result under;
- `data_period`: the period whose rows were measured.

Distribution, volumetry and completeness metrics measure the monitoring period itself. Discrimination and
calibration need mature labels, so they measure the period `label_lag` **observed** periods earlier: with
`label_lag: 1`, the July result of AUC is computed on June's rows. The lag counts the periods present in the data,
not calendar months, so a missing month shifts to the previous observed one. When the history is too short, the
result has no value and the reason `data_period_unavailable`.

`lags` refines this per metric or family; a metric wins over its family, a family over the default (`label_lag`
for discrimination and calibration, no lag otherwise):

```python
"lags": {
    "discrimination": {"months": 12},  # KS, AUC and Gini read the same month one year earlier
    "brier": 6,                        # six observed periods
    "volumetry": {"periods": 1},
}
```

A calendar lag subtracts whole months from the monitoring period and writes the result in the period's own text
format (`YYYY-MM`, `YYYY-MM-DD`, `YYYYMM` or `YYYYMMDD`; the day clamps to the month's end). If that month was not
loaded, the result has no value and the reason `data_period_unavailable`: it never falls back to a neighbouring
period, so immature data is not shown as mature. Each result carries `lag` and `lag_unit` (`periods` or `months`)
beside `data_period`, and series, alerts and reprocessing stay keyed on the monitoring period. See
[decision 0017](../decisions/0017-model-monitoring-maturation-lags.md).

Each result also carries `development_value` and `out_of_time_value`: the same metric measured on the reference
samples, so a dashboard can show how far production moved from development and out-of-time.

## Tables and dashboards

The plan lists five tables as declared `merge` targets of the task (an empty write still creates them):

| Table | One row per |
| --- | --- |
| `<prefix>binnings` | frozen binning version of a model (score, grade and feature bins as a JSON body, the stability binning it shares) |
| `<prefix>results` | model, model version, run, monitoring period, segment, column and metric: value, light, limits and their kind, compared value, control baseline, trend, data period, reference values, rows |
| `<prefix>details` | bin of a PSI/CSI result (shares and contributions) or of the calibration table (mean score, observed rate) |
| `<prefix>alerts` | `light` alert of a red (`critical`) or yellow (`warning`) result, or a `persistence` or `trend` alert span (state, opened, escalated, closed and last periods, flagged periods, recurrence) |
| `<prefix>runs` | run: model version, task, physical input and its Delta version, bricksmith version, binning, references, status, counts, open and critical alerts |

Results, details and alerts are keyed with the run id, and the run row is written **last**. A run that died before
its record, or ended `failed`, is therefore never read by the views. Print them with:

```bash
bricksmith model-monitoring views --scope main.monitoring
```

- `<prefix>latest`: the results of the latest finished run (`success` or `partial`) per model, model version and
  monitoring period;
- `<prefix>latest_details`: the bins of those results;
- `<prefix>model_status`: one row per model, version and period with the worst light and red/yellow counts;
- `<prefix>open_alerts`: the open alerts of those latest finished runs (light alerts, and persistence and trend spans
  still open).

Tables created before a column existed gain it on the next write: the tables are written with the `evolution`
option, which admits compatible additions only (see [schema evolution](schema-evolution.md)).

The binning is frozen: every run reuses the stored version whose declaration matches, so results stay comparable.
Changing the score bins, the features or the shared stability binning fits a new version, visible in the binnings
table.

## Alerts without recomputing

Stored results already carry each value, its light, its reference values and its control baseline. To rebuild
alerts from an export of the results and runs tables, optionally recoloring the stored values with new limits (a
relative limit reads the stored reference value, a control limit the stored baseline) and replaying them with the
declaration's trend and persistence policies:

```bash
bricksmith model-monitoring alerts --state exported.json --config declaration.json --fail-on-red
bricksmith model-monitoring alerts --state exported.json --fail-on-critical  # exit 4 on an open critical span
```

Without `--config`, the stored lights and trend statuses are replayed with the default persistence policy. A
control limit declared after a result was measured has no stored baseline: that result is `no_baseline`.

`exported.json` holds `{"results": [...], "runs": [...]}`. In Python, `bricksmith.monitor.model.alerts.replay_alerts`
does the same.

## The gate and the run report

The gate counts red and yellow results of the latest monitoring period (`red`, `yellow`) and the open persistence
and trend alerts (`critical`: an open critical one; `alert`: any open one). When it fails, the task fails with
`model_monitoring_gate`, its dependents are blocked, and the predictions it already wrote stay published. An error of
the check itself follows `gate.on_error`: `warn` records the outcome with status `error` (and a `failed` run row),
`fail` fails the task. The run report carries the outcomes under `model_monitoring` and the summary prints one line
per model, for example
`model monitoring passed (success): churn 2025-09: 0 red, 0 yellow, 0 open alert(s), 0 error(s)`.

When a task declares both `stability` and `model_monitoring`, data stability runs first, then model monitoring.

## Run it outside a project

`bricksmith.monitor.model.engine.check(data, config, store, run_id=...)` measures rows or a Spark DataFrame and
returns the rows to write per table, so a notebook can run the same check. Rows and Spark give identical results.
