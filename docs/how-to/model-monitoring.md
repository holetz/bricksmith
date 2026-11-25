---
type: how-to
title: "Model monitoring on tasks"
description: "How to declare model monitoring on the task that writes predictions and read its dashboard-ready results."
resource: "src/bricksmith/monitor/model/**"
timestamp: "2026-11-24T22:30:00-03:00"
audience: both
authority: current
---

# Model monitoring on tasks

Model monitoring tells you, on every run, whether a model still behaves the way it did when it was developed. You
declare it on the task that writes the model's predictions. After the write, the shared runner measures the
predictions period by period against the development reference (and, when declared, the out-of-time reference),
colors every metric with a traffic light (fixed bands, a deterioration relative to a reference, or a control chart),
checks its trend, opens alerts for lights that persist, stores the results in dashboard-ready tables, lets people record what they did about an alert, and
applies the gate you declared: warn (the default) or fail the task and block its dependents.

It reads tables only; it never calls the model. It shares its statistical core with
[monitoring](monitoring.md) (bins, PSI, KS, AUC, calibration, limits, trend, persistence) and can share feature bins with
[data stability](stability.md). Spark and the model monitoring package load only for tasks that declare it.

The [end-to-end model flow](model-flow.md) declares model monitoring on a pinned scoring task whose prediction table carries the development and out-of-time samples.

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
| `credit` | Enables the [credit-model performance checks](#credit-model-performance-checks): probability horizons, grade order, the expected-events gate, early-warning labels and the exclusion column. |

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

## Credit-model performance checks

Rating and probability-of-default models need more than discrimination and a global calibration ratio: each grade
must stay inside its predicted band, grades must stay in order, discrimination must hold on early horizons before
the full target matures, and the mix of exclusions in the scored population must not move. A `credit` block turns
on five families for that; a declaration without it keeps exactly the families above (listing a credit family in
`metrics` also enables it, with the block's defaults).

```python
"credit": {
    "horizon": 12,  # periods the score's probability covers
    "label_horizon": 12,  # periods the calibration label covers (default: horizon)
    "grades": ["A", "B", "C", "D"],  # best to worst (default: see grade order below)
    "min_expected_events": 5,  # expected-events gate per grade (default 5)
    "correlation": 0.12,  # enables the correlation-adjusted binomial test
    "early_warning": [  # shorter horizons, each with its own label column and lag
        {"label": "bad_3", "periods": 3},  # lag defaults to 3 observed periods
        {"label": "bad_6", "periods": 6, "lag": {"months": 6}},
    ],
    "exclusion": "exclusion_first",  # the stage cascade's first exclusion reason
},
```

| Family | Metrics | Needs | Default limits |
| --- | --- | --- | --- |
| `grade_calibration` | `grade_oe`, `grade_jeffreys_p`, `grade_binomial_p`, `grade_binomial_adjusted_p` per grade | `grade`, `label` | `grade_oe` green within 0.7–1.3, yellow within 0.5–1.5; p-values green from 0.05, red below 0.01 |
| `grade_order` | `grade_inversions` | `grade`, `label` | 0 green, 1 yellow, more red |
| `early_warning` | `early_ks`, `early_gini`, `early_oe` per horizon | `credit.early_warning` | `early_oe` like `grade_oe` |
| `exclusion_mix` | `exclusion_psi` | `credit.exclusion` | green below 0.10, red from 0.25 |
| `bad_rate_chart` | `bad_rate` | `label` | control chart: \|z\| beyond 2 yellow, beyond 3 red |

**Per grade.** A grade's predicted probability `PD` is the mean probability of its rows and its expected events
are their sum. `grade_oe` is observed over expected events. `grade_jeffreys_p` is the Beta(D + 0.5, N - D + 0.5)
CDF at `PD`, and `grade_binomial_p` is the exact binomial tail `P(X >= D)`, with `N` rows and `D` observed events.
Both are one-sided, so a small p-value means significantly more events than predicted. With a `correlation`,
`grade_binomial_adjusted_p` is the one-factor (Vasicek) large-portfolio tail of the observed rate. It is more lenient
than the plain binomial test when events cluster. A per-grade result's `column` is `<grade column>=<grade>`, for
example `grade=C`, so a [limit rule](#limit-rules) can target one grade.

**Expected-events volume gate.** A grade whose expected events are below `min_expected_events` is
`insufficient_volume` ("expected events 0.4 < min 5"), however many events it observed. `min_rows` does not apply to
per-grade results, so a small grade cannot hide behind a low row count, and a large grade with few expected events
is not judged by chance. Replaying alerts with a declaration keeps the gate: a stored "expected events" gate stays
`insufficient_volume` while its expected events are below the declaration's current `min_expected_events` (a raised
minimum needs a new run, since a row that passed the gate stores no expected events), and `min_rows` does not apply
there either. `grade_inversions` counts the consecutive grades, best to worst, whose observed rate falls.
It compares only the grades that pass the gate. Its details rows hold the calibration table of every grade: `count`,
`mean_score` (mean expected probability) and `observed_rate`.

**Grade order.** Grades run best to worst in the order `grades` declares. Grades it leaves out (all of them when
`grades` is absent) follow, ordered by number when every such grade text is a finite number (`1`, `2`, …, `10`,
`11`), else by text. Numeric grades where a larger number is a better grade, or labels such as `A1`..`A10`, declare
`grades` to get the right order.

**Horizons.** A label covering `k` periods compares with `1 - (1 - p)^(k/h)`, where `h` is `horizon`: a constant
hazard spread over the horizon. Each early-warning horizon has its own label column and lag (default: its `periods`
in observed periods, or `{"months": n}`). It writes `early_ks`, `early_gini` and `early_oe` with `column` set to that
label, so each horizon is its own series with its own rules, trend and alerts.

**Exclusion mix.** `exclusion_psi` is the PSI of the first exclusion reason between development and the period.
Rows the cascade kept (a null reason) form the `(none)` category, so a change in the exclusion rate counts too. The
details rows hold each reason's shares. The column is the cascade column of
[model development stages](model-development.md).

**Bad-rate chart.** `bad_rate` (observed events over rows, per segment) is judged by default as a control chart
centred on the **median** of the development per-period bad rates, with their standard deviation as the spread.
Only development periods count here, unlike other control limits. `baseline_mean` stores the median.

The statistics run on small per-grade sums (rows, events, expected events), reduced on rows or on Spark with
built-in functions, so Free Edition runs them. Rows and Spark give the same results. See
[decision 0022](../decisions/0022-credit-model-performance-checks.md).

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

## Limit rules

A `rules` list (or the path of a TOML or JSON file) refines `limits` per column, segment, metric and date, and marks
segments informational. The syntax, the specificity (dataset 4, segment 2, column 1, then highest `version`, then
smallest `id`), the validity dates and the informational states are the ones of
[data stability](stability.md#limit-rules); here the dataset selector is the `model`, the segment is the segment
value (`n`, not `region=n`), and the validity is compared with the monitoring period.

```python
"rules": [
    {"id": "ks-drop", "metric": "ks", "limits": {"kind": "relative", "green": -0.10, "red": -0.25, "direction": "lower"}},
    {"id": "pilot-regions", "informational_segments": ["pilot"]},
]
```

A model rule may use any limit kind. A `control` rule adds its metric to the ones whose per-period baselines are
measured. Every result carries `rule_id` and `rule_version`, and `replay_alerts` with a declaration recolors stored
rows with the current rules (an informational segment keeps its value but no light, alert or gate count). Tables
created before the columns gain them through the `evolution` write option. See
[decision 0021](../decisions/0021-limit-rules-selectors.md).

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
format (`YYYY-MM`, `YYYY-MM-DD`, `YYYYMM` or `YYYYMMDD`; the day clamps to the month's end, and a period that is the last day of its month maps to the last day of the target month, so month-end closings keep resolving their data period). If that month was not
loaded, the result has no value and the reason `data_period_unavailable`: it never falls back to a neighbouring
period, so immature data is not shown as mature. Each result carries `lag` and `lag_unit` (`periods` or `months`)
beside `data_period`, and series, alerts and reprocessing stay keyed on the monitoring period. See
[decision 0018](../decisions/0018-model-monitoring-maturation-lags.md).

Each result carries in `model_version` the version of the rows it measured. A lagged result is stamped with the
version that scored its `data_period`, not the monitoring period's: with `label_lag: 3` and version 2 released in
September, the matured Gini of September to November measures June to August and stays under version 1, and the
version 2 series starts once version 2's own rows mature. A data period scored by several versions is measured once
and stamped with all of them, sorted and joined by commas (`1,2`), as a mixed monitoring period is. Because trend,
persistence and alert identities are keyed by version, those results join the series, alerts and actions of the
version that produced them; alerts recorded before this rule under the newer version no longer match and close or
expire by their own rules. The run's `model_version` lists every version its results carry.

Each result also carries `development_value` and `out_of_time_value`: the same metric measured on the reference
samples, so a dashboard can show how far production moved from development and out-of-time.

## Tables and dashboards

The plan lists six tables as declared `merge` targets of the task (an empty write still creates them):

| Table | One row per |
| --- | --- |
| `<prefix>binnings` | frozen binning version of a model (score, grade and feature bins as a JSON body, the stability binning it shares) |
| `<prefix>results` | model, model version, run, monitoring period, segment, column and metric: value, light, limits and their kind, compared value, control baseline, trend, data period, reference values, rows |
| `<prefix>details` | bin of a PSI/CSI result (shares and contributions) or of the calibration table (mean score, observed rate) |
| `<prefix>alerts` | `light` alert of a red (`critical`) or yellow (`warning`) result, or a `persistence` or `trend` alert span (state, opened, escalated, closed and last periods, flagged periods, recurrence) |
| `<prefix>actions` | human action on an alert: analysis, closure or risk acceptance with reason, author and valid-until date (written by people, see [Alert actions](#alert-actions-and-governance-views)) |
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
  still open; alerts a person closed or accepted are not open);
- the [governance views](#alert-actions-and-governance-views): `<prefix>actions_latest`, `<prefix>acceptances`,
  `<prefix>coverage`, `<prefix>alert_response` and `<prefix>response_summary`.

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

## Alert actions and governance views

Alerts are rebuilt on every run, so what people decide about them is kept apart, in the append-only
`<prefix>actions` table. There are three actions:

| Action | Effect on the alert |
| --- | --- |
| `analysis` | stays `open`; the action, reason and author are shown on it |
| `closed` | state `closed`: a persistence or trend span stops counting as open |
| `risk_accepted` | state `accepted` through its `valid_until` date (inclusive): the alert stops gating; after the date it returns to its automatic state and the acceptance shows as expired |

Every action needs a reason and an author, and `risk_accepted` needs `valid_until` (no other action takes one). The
latest action of an alert (by `created_at`) decides, so a later `analysis` re-opens an accepted alert. Actions of
other models, or for ids no alert carries, change nothing. `closed` on a `light` alert is bookkeeping only: the
red result still counts for a `red` gate, while an active acceptance removes it from the count and the run
message reports how many alerts are accepted.

Find the alert id in the alerts table, the `open_alerts` view or the replay output, then write the action with the
`act` verb, which validates it and prints the `INSERT` statement for any SQL client:

```bash
bricksmith model-monitoring act --model churn --alert-id 3f9a1c0b2d4e5f60 --action risk_accepted \
  --reason "known seasonal shift, owner signed" --author ana --valid-until 2026-12-31 \
  --scope main.monitoring
```

Runs read the model's actions and fold them into the alerts at the run date. To replay a stored state on another
date, add `"actions": [...]` to the state file (`act --state exported.json` appends one) and pass
`alerts --as-of 2026-12-15`.

The governance views answer the questions a review asks:

- `<prefix>acceptances`: every risk acceptance as `active`, `expired` or `superseded` (a later action replaced
  it), with the days left;
- `<prefix>coverage`: per model and version, the periods monitored, first and latest period, the last finished run
  and the days since it ran;
- `<prefix>alert_response`: per alert, when it was first seen, when it was first acted on and when it was
  closed, with the days between;
- `<prefix>response_summary`: per model, the alerts, how many were acted on and resolved, and the mean days to the
  first action and to resolution;
- `<prefix>actions_latest`: the latest action of each alert;
- `<prefix>coverage_latest`: per declared model, whether it was monitored for the latest closing (the greatest
  monitoring period of any model), its own latest period and the days since its last finished run;
- `<prefix>coverage_summary`: how many declared models there are, how many were monitored for the latest closing and
  the share (`share_monitored`);
- `<prefix>open_alerts_by_severity`: the open alerts per model and severity, with the oldest opened period;
- `<prefix>alert_recurrence`: per model and metric, the alerts, how many recurred and the most periods one was flagged;
- `<prefix>acceptance_summary`: per model, the active, expired and superseded acceptances and the days to the next
  expiry.

A model is **declared** when it is listed with `--declared` or has any run of any status, so a model whose runs only
failed counts as not monitored. A model that never ran counts only when you list it:

```bash
bricksmith model-monitoring views --scope main.monitoring --declared churn,fraud,limit
```

Time to resolve is in `alert_response` and `response_summary`.

## The monitoring report

One self-contained report per model shows what a reviewer needs on one page:

```bash
bricksmith model-monitoring report --model churn --scope main.monitoring --out reports/
bricksmith model-monitoring report --model churn --state exported.json --format md --out reports/
```

`--scope` reads the tables of that schema through the selected context; `--state` reads an exported
`{"results": [...], "runs": [...], "actions": [...]}` file offline. Both write `<model>.html` and `<model>.md` (or
one of them with `--format`). Only results of finished runs are read, the latest per model version and period.

- The **period axis** is the sorted list of monitoring periods found in the data: nothing is hard-coded, and a
  period with no result stays a gap.
- There is **one tab or section per metric family**, and one series per metric, column and segment.
- Each series shows its **development and out-of-time reference values** beside the monitored periods: as dashed
  lines in the chart and as the first columns of its table.
- Each point carries its light with a text label, and a result with no value shows the reason it has none.
- The **alerts** tab lists the alerts with their state and the human action on them (analysis, closure or risk
  acceptance with its author, reason and `valid_until`), evaluated on `--as-of` (default today).

The HTML needs no script and loads nothing from outside, so it opens offline and in a review pane. In Python,
`bricksmith.monitor.model.report.build_report` returns the report as data and `render_html` and `render_markdown`
render it.

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
