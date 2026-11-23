# Data stability

Data stability tells you, on every run, whether a table still looks like the data it was designed and validated on.
You declare it on the task that writes the table. After the write, the shared runner profiles the table period by
period and compares each period with a baseline. It judges every metric with a traffic light, a trend check and
persistence alerts, stores the results in five dashboard-ready tables, and applies the gate you declared: warn
(the default) or fail the task and block its dependents.

It shares its statistical core with [model monitoring](monitoring.md): bins (`BinSpec`), PSI, versioned references,
and the limits, trend and persistence modules in `bricksmith.monitor`.

## Declare it on a task

```python
@task(
    kind="table",
    metadata={
        "write": "overwrite",
        "stability": {
            "period": "month",                       # required: the column that splits the table into periods
            "exclude": ["sale_id"],                  # never monitored (period, segments and weight never are)
            "segments": ["channel", ["channel", "region"]],
            "baseline": {"kind": "fixed", "start": "2026-01", "end": "2026-03"},
            "gate": {"fail_on": ["red"]},            # default: warn only
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
| `exclude` | `[]` | Columns never monitored. |
| `segments` | `[]` | Columns (or lists of columns) whose values are also checked separately. The whole table is always checked. |
| `weight` | none | Numeric column: PSI uses weight shares, and `weight_change` is reported. |
| `baseline` | `fixed` | `fixed` (`start`/`end`, default the periods the bins were fitted on), `previous` (`lag`), `rolling` (`periods`, `min_periods`). |
| `fit_periods` | `1` | Earliest periods the bins are fitted on when no fixed range is given. |
| `limits`, `column_limits` | see below | Per metric `{green, red, direction, center}` or `null` to disable; per column overrides. |
| `trend`, `trend_metrics` | window 12, min 6 points, 2σ/3σ; `psi`, `volume_change` | Least-squares trend check; `false` disables it. |
| `persistence` | yellow 3, red 2, close after 3 greens, recurrence 12 | When an alert opens, escalates and closes; `immediate_red` opens on one red. |
| `gate` | `{fail_on: [], on_error: "warn"}` | `fail_on` takes `red`, `yellow`, `critical`, `alert`; `on_error` takes `warn` or `fail`. |
| `min_rows` | `30` | Below this, distribution metrics are `insufficient_volume` (volume metrics still judge). |
| `refresh_periods` | `1` | The latest N periods are profiled again on every run (late or corrected data). |
| `max_periods`, `max_segments` | `36`, `50` | Computation window and the most frequent segment values kept per segment set. |
| `scope`, `prefix`, `dataset` | output's schema, `stability_`, output name | Where the tables live and how the dataset is named. |
| `inference` | 10 bins, >20 distinct = numeric, 20 categories, >1000 text values skipped, exact-zero bin | Options of `"auto"`. |

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
   come from the stored profiles, so the table is not rescanned.
4. **Results.** Every period of the window is judged against its baseline. Results are written for new periods, or
   for the whole window when limits, baseline, trend or persistence changed.
5. **Alerts.** Status histories are replayed into alerts. A replay produces the same `alert_id`, and stored alerts
   it no longer produces become `superseded`.
6. **Gate.** The gate is evaluated on the latest period. A failing gate marks the task `failed` with error code
   `stability_gate`, and its dependents become `blocked`. The output itself is already published: the gate protects
   downstream tasks, it does not roll back. The task result (`--json` reports and `task_finished` events) carries a
   `stability` summary, and the text summary prints one line per checked output.

Dry runs and failed tasks skip the check. Stability writes to a shared table are serialized per process and retried
on concurrent-modification conflicts.

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

Bounds are inclusive. With `higher`, a value up to `green` is green, up to `red` is yellow, and above `red` is red.
`lower` mirrors this, and `both` judges the distance from `center`. Values that cannot be judged get an explicit
state instead of a color: `no_data`, `insufficient_volume`, `no_baseline` or `no_rule`. Their rank is -1, so they
never count as green.

**Trend check.** An ordinary-least-squares line is fitted through the last `window` earlier values of the same
series. The current residual is standardized by the residual standard deviation: beyond 2σ is yellow, beyond 3σ is
red, and only the worse side counts.

**Persistence.** `yellow` consecutive yellow-or-worse periods open a warning. `red` consecutive reds open a
critical alert, or escalate an open warning to critical. Severity never goes down. `close_after` greens close the
alert, and suppressed periods neither count nor break a run. An alert that reopens within `recurrence` periods is
flagged. A red trend check opens a `trend` alert.

## Tables

All tables are flat and typed. Reports only filter on scalar columns. `binnings.body` keeps the reference JSON,
which reports never use.

| Table | Key | Main columns |
|---|---|---|
| `stability_binnings` | dataset, version | config_fingerprint, fingerprint, period_column, fit_periods, fit_rows, columns, skipped, body |
| `stability_profiles` | dataset, version, period, segment, column, bin | period_date, segment_label, bin_order, bin_label, count, weight, value_sum, value_sum_sq, share, baseline_share, contribution |
| `stability_results` | dataset, version, period, segment, column, metric | period_date, segment_label, value, current, baseline, status, status_rank, reason, green_limit, red_limit, direction, rows, baseline_rows, in_baseline, baseline_kind, baseline_label, trend_status, trend_expected, trend_deviation |
| `stability_alerts` | dataset, alert_id | version, kind, metric, column, segment, severity, severity_rank, state, opened_period, escalated_period, closed_period, last_period, last_status, last_value, flagged_periods, recurrence, message |
| `stability_runs` | run_id, dataset | task_id, physical, version, latest_period, periods_profiled, status, gate, red, yellow, open_alerts, critical_alerts, message, judgment_fingerprint, started_at, finished_at |

Table-level metrics use column `""`, and the whole population uses segment `""` (label `(all)`). Segment keys are
canonical text, `col=value,col2=value2`, with `(null)` for nulls. `period_date` is the first day the period
denotes when it can be read as a date.

## Dashboards

`bricksmith stability views --scope catalog.schema` prints three views to create next to the tables:

- `stability_latest`: results of the latest period of the latest version of each dataset;
- `stability_dataset_status`: worst status, red and yellow counts and row count per dataset and period;
- `stability_open_alerts`: alerts still open.

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
- Computing results collects the stored profiles of the window: periods × segment values × columns × bins rows. Keep
  `max_periods`, `max_segments` and the column list proportionate.
- Results compare periods within one binning version. Changing the bin-relevant configuration starts a new version,
  whose history begins at the next run.
- Not yet covered: human alert actions, notification delivery, a maturation lag between monitoring and data periods,
  per-segment rule specificity and revision reliability. They are captured as follow-up specifications.
