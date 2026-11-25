---
type: how-to
title: "Data stability"
description: "How to declare data-stability checks on tasks and read their dashboard-ready results."
resource: "src/bricksmith/monitor/**"
timestamp: "2026-11-24T16:20:00-03:00"
audience: both
authority: current
---

# Data stability

Data stability tells you, on every run, whether a table still looks like the data it was designed and validated on.
You declare it on the task that writes the table. After the write, the shared runner profiles the table period by
period and compares each period with a baseline. It judges every metric with a traffic light, a trend check and
persistence alerts, stores the results in six dashboard-ready tables, and applies the gate you declared: warn
(the default) or fail the task and block its dependents.

It shares its statistical core with [model monitoring](monitoring.md): bins (`BinSpec`), PSI, versioned references,
and the limits, trend and persistence modules in `bricksmith.monitor`.

The [end-to-end model flow](model-flow.md) declares data stability on the feature tables a model reads, with a fixed baseline over the development closings.

## Declare it on a task

```python
@task(
    kind="table",
    metadata={
        "write": "overwrite",
        "stability": {
            "period": "month",  # required: the column that splits the table into periods
            "exclude": ["sale_id"],  # never monitored (period, segments and weight never are)
            "segments": ["channel", ["channel", "region"]],
            "baseline": {"kind": "fixed", "start": "2026-01", "end": "2026-03"},
            "gate": {"fail_on": ["red"]},  # default: warn only
        },
    },
)
def sales_monthly(ctx): ...
```

A declaration must be a literal, because tasks are discovered statically. A task with several outputs gives one
declaration per output in a list, each with `output` naming it. The full list of keys:

| Key | Default | Meaning |
|---|---|---|
| `period` | required | Column whose text value is the period (`2026-01`, `2026-01-31`, `202601`...). Periods sort as text. |
| `columns` | `"auto"` | `"auto"` infers bins from types; or a list of names; or a mapping name → bin configuration (as in `monitor fit`). |
| `exclude` | `[]` | A list of columns never monitored. A bare string is a plan error. |
| `segments` | `[]` | Columns (or lists of columns) whose values are also checked separately. The whole table is always checked. |
| `weight` | none | Numeric column: PSI uses weight shares, and `weight_change` is reported. |
| `baseline` | `fixed` | `fixed` (`start`/`end`, default the periods the bins were fitted on), `previous` (`lag`), `rolling` (`periods`, `min_periods`). `exclude: red` (default `none`) makes `previous` and `rolling` skip a period whose `volume_change` or `psi` was red in the segment and take the next eligible one. |
| `fit_periods` | `1` | Earliest periods the bins are fitted on when no fixed range is given. |
| `limits`, `column_limits` | see below | Per metric `{green, red, direction, center}` or `null` to disable; per column overrides. |
| `trend`, `trend_metrics` | window 12, min 6 points, 2σ/3σ; `psi`, `volume_change` | Prediction-interval trend check (see the trend check under [Metrics and default limits](#metrics-and-default-limits)); `false` disables it. |
| `trend_directions` | `row_count` and `mean` both, `null_rate` and `unseen_rate` higher | Per metric `higher`, `lower` or `both`: the direction of the trend check for a metric without limits. A limit's own direction wins. |
| `persistence` | yellow 3, red 2, close after 3 greens, recurrence 12 | When an alert opens, escalates and closes; `immediate_red` opens on one red. |
| `gate` | `{fail_on: [], on_error: "warn", periods: "latest"}` | `fail_on` takes `red`, `yellow`, `critical`, `alert`; `on_error` takes `warn` or `fail`; `periods` takes `latest` or `profiled` (see [Backfills and reprocessing](#backfills-and-reprocessing)). |
| `notify` | none | Delivers opened, escalated and recurred alerts through the run's notifier: `true`, `false` or `{on, min_severity}`. See [Notifications](#notifications). |
| `min_rows` | `30` | Below this, distribution metrics are `insufficient_volume` (volume metrics still judge). |
| `refresh_periods` | `1` | The latest N periods are profiled again on every run (late or corrected data). |
| `max_periods`, `max_segments` | `36`, `50` | Computation window and the most frequent segment values kept per segment set, ranked over every period the run loads (see [Profiles](#what-happens-in-a-run)). |
| `rules` | `[]` | [Limit rules](#limit-rules): an inline list, or the path of a TOML or JSON file, that picks limits or marks segments informational per dataset, column, segment, metric and date. A rule's `metric` must be a stability metric. |
| `scope`, `prefix`, `dataset` | output's schema, `stability_`, output name | Where the tables live and how the dataset is named. |
| `epsilon` | `1e-4` | Floor of a bin share in PSI and CSI. Must be a finite number greater than 0. |
| `inference` | 10 bins, >20 distinct = numeric, 20 categories, >1000 text values skipped, exact-zero bin | Options of `"auto"`. |

**Migration: stricter declaration checks.** Three declarations used to pass plan and misbehave at run time. They
now fail plan with `invalid_stability`:

- a rule whose `metric` is not a stability metric, inline or in a rules file (for example `"PSI"` or
  `"mean_shfit"`). Such a rule never matched, so its limits never applied. The error lists the known metrics;
- an `exclude` given as a string (`"exclude": "sale_id"`). It was read letter by letter, so the column was still
  monitored. Write `"exclude": ["sale_id"]`;
- an `epsilon` of `0`, a negative value or a non-finite one. With `0`, every PSI or CSI with an empty bin failed
  at run time. Remove the key to use the default `1e-4`.

Model monitoring applies the same `epsilon` bound.

`"auto"` uses quantile bins (with a separate bin for exact zeros) for numeric columns with many distinct values.
Other numeric, text and boolean columns are categorical. Dates, timestamps, binary and complex types, constant
columns and text with very many values are skipped. The reason for each skip is stored on the binning row.

## What happens in a run

1. **Plan.** The plan lists the five stability tables of the task as declared `merge` targets, so plan review and dry
   runs show every write. There are no hidden writes. An invalid declaration is a plan error (`invalid_stability`).
2. **Binning.** The task's output is read back through the run's table I/O. The active binning is the latest version
   fitted with the same bin-relevant configuration. Otherwise a new version is fitted once and frozen. Bins are never
   refitted per run, because moving quantiles produce false drift.
3. **Profiles.** Periods without a stored profile, plus the latest `refresh_periods`, are profiled. Each profile
   holds counts, weights and per-bin value sums and squares, per period, segment, column and bin. Older periods
   come from the stored profiles, so the table is not rescanned. The kept segments of each segment set are the
   `max_segments` most frequent values over every period the run loads (the window and the history its baseline
   reads), not only the periods it profiles, so the set does not change with `refresh_periods`. A segment that
   already has stored profiles in those periods stays kept even when it falls out of the top, so the set can exceed
   `max_segments` until its history leaves the window. A kept segment with no rows in a profiled period after its
   first period gets profile rows at zero, so a segment whose volume collapses or vanishes is still judged
   (`volume_change` down to -1). A segment that newly enters the top starts with `no_baseline`; its earlier periods
   are not profiled again.
4. **Results.** Every period of the window is judged against its baseline. Results are written for new periods, or
   for the whole window when limits, baseline, trend or persistence changed.
5. **Alerts.** Status histories are replayed into alerts. A replay produces the same `alert_id`, an open alert whose
   opening left the window, or opened inside it before the window's red run, is carried (see Persistence), a closed alert
   stays closed while the judgment is unchanged, and other stored alerts it no longer produces become `superseded`.
6. **Gate.** The gate is evaluated on the latest period (or on every period profiled in the run with `gate.periods`). A failing gate marks the task `failed` with error code
   `stability_gate`, and its dependents become `blocked`. The output itself is already published: the gate protects
   downstream tasks, it does not roll back. The task result (`--json` reports and `task_finished` events) carries a
   `stability` summary, and the text summary prints one line per checked output.

Dry runs and failed tasks skip the check. Stability writes to a shared table are serialized per process and retried
on concurrent-modification conflicts.

## Notifications

Stored alerts can also be delivered through an [`AlertNotifier`](operations.md). Pass the notifier to the run:

```python
run_plan(plan, ..., notifier=AlertNotifier([my_adapter]), notify=True)
```

`notify` on `run_plan` is the project default; a task's `stability.notify` replaces it (`false` turns it off for
that task). Both take `true` (every transition), `false`, or `{on: [opened, escalated, recurred], min_severity:
warning|critical}`. With a notifier but no `notify` anywhere, nothing is sent.

Each alert that **opened**, **escalated** from warning to critical or **recurred** becomes one `statistical_alert`
event whose dedup key is `stability:<dataset>:<alert_id>:<transition>`. Events are derived by comparing the replayed
alerts with the ones stored before the run, so replaying unchanged data sends nothing, and a sliding window that
drops the opening period of an open alert keeps its `alert_id` and sends nothing either. Delivery happens after the
rows are stored, never raises, and cannot fail the task or change the gate; `bricksmith stability check` does not
deliver.

## Backfills and reprocessing

A backfill or a correction of an older period is invisible to a run: only periods without a stored profile and the
latest `refresh_periods` are profiled, so the stored profile, results and alerts of the older period stay stale.
Name the rewritten periods instead of raising `refresh_periods` for every run (which rescans the table each time):

```bash
bricksmith stability reprocess --config stability.json --table catalog.schema.sales --dataset sales \
  --periods 2026-01..2026-03 --state stability-state.json   # a list (a,b) or an inclusive first..last range
```

`check(data, config, store, dataset=..., run_id=..., reprocess=["2026-01..2026-03"])` is the same input in the API.
The named periods are profiled again together with the declared refresh range, the whole window is judged again
and the alerts are replayed (an alert the new data no longer produces becomes `superseded`; notifications still
fire only for real transitions). A period that is not in the data, or lies outside the `max_periods` window, is an
error: the window bounds how much of the table a reprocess may rescan, and the run message reports how many periods
were profiled. With `revision` on, each reprocessed period is compared with its first publication like any
re-profiled period.

When the runner rewrites a range, pass it to the run and every task that declares `stability` profiles again the
periods it has (the ones it lacks are ignored, so one request can serve several datasets):

```python
run_plan(plan, project_root=root, execution=execution, table_io=io, reprocess=["2026-01..2026-03"])
```

`gate.periods: profiled` makes the gate count the red (and yellow) results of every period profiled in this run,
not only the latest, so a reprocessed older period that turns red fails the task. The default `latest` is
unchanged. `stability_runs.periods_reprocessed` records the reprocessed periods of a run (empty when none).

## Human actions

Alerts are rebuilt on every run, so what people decide about them lives in the append-only `stability_actions`
table. Record a decision with `bricksmith stability action`:

```bash
bricksmith stability action --dataset orders --alert-id <alert_id> --action risk_accepted \
  --reason "known migration drift" --author ana --valid-until 2026-12-31 --scope catalog.schema
```

The command prints the `INSERT` statement to run (or appends the row to a `--state` file). The actions are:

- `analysis` records who is looking at the alert and changes nothing else;
- `risk_accepted` keeps the alert out of the gate and out of notifications until `--valid-until` (inclusive); the day
  after, it counts again while the action stays visible;
- `closed` takes the alert out of the gate and requires a `--resolution`: `fixed`, `expected`, `new_normal` or
  `false_positive`.

The latest action of each alert wins. Stored alerts keep their automatic state; the run folds the actions into the gate
counts and the notifications on the run date, and the message reports how many alerts are accepted. Actions enter the
judgment fingerprint, so a new decision re-judges the window and a replay stays deterministic. Resolutions are stored
and counted by `stability_false_positive_rate`; their effect on baselines belongs to period marks, which are separate.

## Revision reliability

Re-published history (late loads, corrections) silently changes numbers that were already reported. Declare
`revision: true` to compare each period with its **first publication**, the profile the monitor stored the first
time it saw the period:

```python
metadata = {"stability": {"period": "month", "revision": True, "refresh_periods": 3}}
```

The first publication goes to one extra table, `stability_revisions`, written once per period and never rewritten.
Periods that already had stored profiles when `revision` was switched on are snapshotted from those stored rows. At
every run, each period of the window that was re-profiled is compared with its snapshot, per segment, and the results
join `stability_results` with `baseline_kind = revision` and `baseline_label = first publication`. They are judged by
`limits`, `column_limits`, rules, `min_rows`, persistence alerts and the gate exactly like the other metrics.

A revision is visible only while the period is re-profiled. `refresh_periods` (default 1) must cover how far back
history can be republished, or a late change to an older period goes unseen. Snapshots belong to one binning
version, so a refit compares from its own first observation.

A period that was published and then **emptied** is a revision too. When a period with a first-publication snapshot
lies inside the refresh range (the `refresh_periods` most recent periods of stored history, the latest included
even if it vanished) and the data no longer has rows for it, each snapshotted segment gets
`revision_row_change = -1.0`, judged by the configured limits like any revision result, and the period's current
profile is replaced by zero-count rows of the run. When the vanished period is the newest one, it is the latest
period of the run, so its red results count for the gate. Periods older than the refresh range are not judged.

The revision run **reports** the emptied period in full: `row_count` is 0 and its other metrics are recomputed, so
`stability_results`, the `latest` view and `dataset_status` never keep the stale first-publication count. Its
zero-count profile is **never a baseline** for another period under the `previous` and `rolling` baselines: they skip
it and take the next eligible period, so an untouched period is judged the same with `revision` on or off. The
`fixed` baseline is unchanged. The check reads stored profiles, not the Delta history
of the table, so it works on every engine.

## Metrics and default limits

| Metric | Per | Value | Default limits |
|---|---|---|---|
| `psi` | column | Σ (q − p) ln(q / p) over bins, shares floored at 1e-4 | higher: 0.10 / 0.25 |
| `null_rate`, `null_rate_delta` | column | share of nulls; difference from baseline | delta higher: 0.05 / 0.15 |
| `unseen_rate`, `unseen_rate_delta` | column | share outside the fitted bins (new categories, values below/above the range) | delta higher: 0.05 / 0.15 |
| `mean`, `mean_shift` | numeric column | mean of non-null values; (mean − baseline mean) / baseline std | shift both: 0.25 / 0.50 |
| `row_count` | table | rows in the period | none |
| `volume_change` | table | rows / mean baseline rows per period − 1 | both: 0.20 / 0.50 |
| `weight_change` | table | same, with the weight column | both: 0.20 / 0.50 |
| `revision_row_change` | table | rows now / rows at first publication − 1 (only with `revision`) | both: 0.01 / 0.05 |
| `revision_psi` | column | PSI of the period's bin shares against its first publication | higher: 0.05 / 0.10 |
| `revision_mean_shift` | numeric column | (mean − first-publication mean) / first-publication std | both: 0.10 / 0.25 |

Bounds are inclusive. With `higher`, a value up to `green` is green, up to `red` is yellow, and above `red` is red.
`lower` mirrors this, and `both` judges the distance from `center`. Values that cannot be judged get an explicit
state instead of a color: `no_data`, `insufficient_volume`, `no_baseline` or `no_rule`. Their rank is -1, so they
never count as green.

**Trend check.** The trend check fits a least-squares line through the last `window` colored values of a series (at least
`min_points`) and extrapolates it one period. The current value is standardized by the **prediction standard error**
`s * sqrt(1 + 1/k + (k - x̄)² / Sxx)`, which carries the uncertainty of the fitted line itself, and judged against
**Student-t quantiles** with `k - 2` degrees of freedom. `yellow` and `red` stay sigma-equivalents (default 2 and 3):
each is the normal tail probability it implies (about 95.4% and 99.7% two-sided), turned into the t quantile for the
window at hand, so pure noise flags at the nominal rate for any window size.

| Key | Default | Meaning |
| --- | --- | --- |
| `min_abs_change` | `0` | A departure at most this large (original scale) is never yellow or red. |
| `min_rel_change` | `0.01` | A departure at most this fraction of the expected value is never yellow or red. |
| `transform` | `none` | `log` fits `log1p` of the values, so multiplicative growth is a straight line. |
| `fit` | `ols` | `theil_sen` (median of the pairwise slopes) or `huber` (Huber-weighted least squares) resist outliers in the history; both are computed in the library without extra dependencies. `theil_sen` is the recommended robust choice. |
| `scale` | `std` | `mad` uses 1.4826 times the median absolute deviation of the residuals instead of their standard deviation, so one past spike does not widen the band. |
| `exclude` | `red` | Which judged values stay out of the trend history: `red` drops periods whose light or trend was red, `flagged` also yellow, `none` keeps all. The values are still stored and reported. |

A flat history has no spread: the deviation is stored empty (never infinite) and the point is red only when it
exceeds the floor. The result also carries `trend_sigma` (the prediction standard error in the fitted scale) and
`trend_lower` / `trend_upper` (the red-level interval, in the original scale) so a dashboard can draw the band.
The trend history of a period at the start of a rewritten window is seeded from the stored results of the `window`
periods before it, so a re-judged window gives the same trend status as the first judgment.

**Anomalous periods.** With `exclude: red` a red spike no longer enters the history, so it cannot mask a later jump
(a clean history flagging a 5.3σ jump kept reporting it green after one earlier spike). The caveat: a genuine permanent
level shift stays red until a human declares a regime change, because the new level is never added to the history. The
same rule runs for the stored history that seeds a rewritten window, and for the model monitor's trend.
`baseline.exclude: red` applies the idea to `previous` and `rolling` baselines. Periods marked by a human (a separate
capability) plug into the same path through the `excluded` input of `compute`.

**Migration note.** The trend judgment changed, so the judgment fingerprint changes: the first run after upgrading
re-judges the whole window once, supersedes the alerts the old statistic opened, and adds the three band columns
to `stability_results` through table evolution. Expect fewer `trend` alerts.

The trend needs a direction but not traffic-light limits. A metric with limits is trended in the direction of its
limits; any other metric in `trend_metrics` (`row_count`, `mean`, `null_rate`, `unseen_rate`) is trended in its
`trend_directions` entry or default. Its light stays `no_rule`, its `trend_status` is filled and a red trend opens a
`trend` alert (never a persistence alert). A `trend_metrics` entry with no direction is a plan error. Absolute counts
are the natural series for a trend, since a fixed limit does not generalize across tables. With a `fixed` baseline the
trend on `volume_change` equals the trend on `row_count` (the change is an affine function of the count), so prefer
`row_count` with a `rolling` or `previous` baseline.

**Persistence.** `yellow` consecutive yellow-or-worse periods open a warning. `red` consecutive reds open a
critical alert, or escalate an open warning to critical. Severity never goes down. `close_after` greens close the
alert, and suppressed periods neither count nor break a run. An alert that reopens within `recurrence` periods is
flagged. A red trend check opens a `trend` alert.

An open alert whose opening period has left the window is carried: the replay resumes it before the first period of
the window, so it keeps its `alert_id`, escalates and closes on the window's statuses even when the window alone would
not open it. Because the replay only sees the window, keep `close_after` and `red` at or below `max_periods`: a longer
streak can never complete inside the window, and a carried alert would then stay open.

The same holds for an open alert that opened inside the window when its red run began before the window and the
window alone no longer reproduces it (a sliding window of `red` reds): it is carried from its opening period instead
of being superseded, so it closes after `close_after` greens.

A closed alert whose opening period has left the window stays closed, and so does any closed alert while the
judgment (limits, baseline, trend or persistence) is unchanged: the replay can no longer re-judge it, so it is
never marked `superseded` and stays in the open and closed alerts. A changed judgment re-judges the window. Recurrence spans the window too: an alert that
opens within `recurrence` periods of the last stored close of the same metric, column, segment and kind is flagged
and notified as `recurred`. The distance counts the periods of the checked table between that close and the window,
so pass the whole table, not only the window.

## Limit rules

`limits` and `column_limits` give one set of limits per metric (and column). When a segment is informational, a
column is naturally volatile or one dataset needs stricter bounds, declare **rules** instead of scattering overrides
in code. Rules are reviewable data: an inline `rules` list, or the path of a project file that git versions.

```toml
# monitoring/rules.toml
[[rules]]
id = "sales-psi-strict"      # reported on every result row it judges
version = 2                  # the highest version wins a tie
dataset = "sales_monthly"    # selectors: dataset, column, segment, metric (absent = any)
metric = "psi"
valid_from = "2026-01-01"    # inclusive ISO dates, compared with the period's date
limits = { green = 0.05, red = 0.10 }

[[rules]]
id = "pilot-channels"
informational_segments = ["channel=pilot"]   # or controlled_segments = [...]: every other segment is informational
```

```python
"stability": {"period": "month", "segments": ["channel"], "rules": "monitoring/rules.toml"}
```

- **Path base.** A relative rules file path resolves against the project root (the `--project` of the plan and run
  commands), not the working directory, so plan build and a worker running elsewhere read the same file. An absolute
  path is used as given. Without a project root (the standalone `stability check` command) it resolves against the
  working directory.
- **Specificity.** Among the rules that match a result, the highest score wins: a set `dataset` scores 4, `segment` 2
  and `column` 1. Equal scores go to the highest `version`, then to the smallest `id`. `metric` is a required filter of
  limits rules. With no matching rule, `column_limits`, `limits` and the defaults apply as before.
- **Validity.** `valid_from` and `valid_to` are inclusive. A rule with a bound never matches a period whose text is
  not a date, so a time-boxed rule cannot judge an undated period by accident. `limits = null` disables the
  judgment (`no_rule`).
- **Informational segments.** `informational_segments` lists segment values (`channel=pilot`: the canonical segment
  key) that are computed and stored with the status `informational`: never a color, never in a trend, persistence
  or alert, never counted by the gate. `controlled_segments` lists the segments that stay controlled and makes
  every other segment informational. The whole table is never informational. The most specific informational rule
  decides alone. Informational rows show their value on the dashboards, with empty limits.
- **Audit.** Every result row carries `rule_id` and `rule_version` (empty and `0` without a rule); an informational
  row carries the informational rule's. Changing a rule rewrites the window's results like any limits change.
- Stability rules use `absolute` limits only, like `limits`. A table created before the rule columns gains them
  through the `evolution` write option.

See [decision 0021](../decisions/0021-limit-rules-selectors.md).

## Tables

All tables are flat and typed. Reports only filter on scalar columns. `binnings.body` keeps the reference JSON,
which reports never use.

| Table | Key | Main columns |
|---|---|---|
| `stability_binnings` | dataset, version | config_fingerprint, fingerprint, period_column, fit_periods, fit_rows, columns, skipped, body |
| `stability_profiles` | dataset, version, period, segment, column, bin | period_date, segment_label, bin_order, bin_label, count, weight, value_sum, value_sum_sq, share, baseline_share, contribution |
| `stability_results` | dataset, version, period, segment, column, metric | period_date, segment_label, value, current, baseline, status, status_rank, reason, green_limit, red_limit, direction, rows, baseline_rows, in_baseline, baseline_kind, baseline_label, trend_status, trend_expected, trend_deviation, trend_sigma, trend_lower, trend_upper |
| `stability_alerts` | dataset, alert_id | version, kind, metric, column, segment, severity, severity_rank, state, opened_period, escalated_period, closed_period, last_period, last_status, last_value, flagged_periods, recurrence, message |
| `stability_actions` | dataset, action_id | alert_id, action, resolution, reason, author, valid_until, created_at; appended by people, only read by runs |
| `stability_revisions` | dataset, version, period, segment, column, bin | only with `revision`: the profile columns of the first publication (count, weight, value_sum, value_sum_sq), run_id, published_at |
| `stability_runs` | run_id, dataset | task_id, physical, version, latest_period, periods_profiled, status, gate, red, yellow, open_alerts, critical_alerts, message, judgment_fingerprint, started_at, finished_at, periods_reprocessed |

Table-level metrics use column `""`, and the whole population uses segment `""` (label `(all)`). Segment keys are
canonical text, `col=value,col2=value2`, with `(null)` for nulls. `period_date` is the first day the period
denotes when it can be read as a date.

## Dashboards

`bricksmith stability views --scope catalog.schema` prints the views to create next to the tables:

- `stability_latest`: results of the latest period of the latest version of each dataset;
- `stability_dataset_status`: worst status, red and yellow counts and row count per dataset and period;
- `stability_open_alerts`: alerts still open by their automatic state, with the latest human action and the
  `effective_state` it gives (`open`, `accepted` or `closed`);
- `stability_actions_latest`, `stability_alert_response` and `stability_response_summary`: the latest action per alert,
  and the days to the first action and to the closing of each alert;
- `stability_false_positive_rate`: closures typed `false_positive` against all closures, per rule.

A BI model on these tables works as a star:

- **Facts.** `stability_results` (one row per metric judgment), `stability_profiles` (bin detail),
  `stability_alerts` and `stability_runs`.
- **Dimensions.** Derive them from the facts: dataset (`dataset`), period (`period`, `period_date`; relate it to a
  calendar on `period_date`), segment (`segment`, `segment_label`), column (`dataset`, `column`) and metric
  (`metric`, `direction`, `green_limit`, `red_limit`).
- **Relationships.** Results and profiles join on dataset, version, period, segment and column. Alerts join results
  on dataset, version, segment, column and metric, with `last_period` as the period.
- **Suggested measures.** Worst status (`max(status_rank)`), count of red and yellow results, open alerts by
  severity, PSI over time per column with `green_limit` and `red_limit` as reference lines, and the bin-share
  comparison of `share` against `baseline_share` with `contribution` explaining a PSI jump.

## Outside a project

```bash
bricksmith stability check --config stability.json --data sales.csv --state stability-state.json
bricksmith --json stability check --config stability.json --table catalog.schema.sales   # exit 4 when the gate fails
```

`check(data, config, store, dataset=..., run_id=...)` in `bricksmith.monitor.stability` runs the same engine on row
lists or a Spark DataFrame. It returns the rows to write per table and the outcome. `MemoryStore` holds the history
in memory.

## Example

`examples/shared_schema/catalogs/demo/ml/stability.py` declares stability on `sales_monthly` with a fixed
January–March baseline and `fail_on: ["red"]`. `sales_report` reads it. Run with the parameter
`stability_drift=yes` to shift the last month: amount PSI and the `channel=app` volume turn red, `sales_monthly`
fails with `stability_gate`, and `sales_report` is blocked.

## Limits

- Fitting collects one small row per distinct value of each column. Above 100,000 distinct values, the Spark engine
  uses approximate percentiles instead.
- Computing results collects the stored profiles of the window and of the history its baseline reads, for the active
  binning version only: periods × segment values × columns × bins rows. The filters on version and period run in the
  storage read, so older periods and other versions are never collected. Open and closed alerts of the dataset and the
  runs of the active version are read as well. Keep `max_periods`, `max_segments` and the column list proportionate.
- Results compare periods within one binning version. Changing the bin-relevant configuration fits a new version in
  the next run, which profiles and judges every period of the window at once (the first run of a version is not quiet,
  and the gate can fail in it). Alerts of the previous version are marked `superseded`.
- Revision reliability compares stored profiles, not rows: it says that a period changed and by how much, not which
  keys changed, and it does not read the Delta history of the table.
- Not yet covered: reading rules from a Delta table. It is captured as a follow-up specification.
