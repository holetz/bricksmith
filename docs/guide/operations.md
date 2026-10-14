# Operations: events, reports, readiness, alerts and budgets

```python
from bricksmith.ops import JsonLinesSink
report = run_plan(plan, project_root=".", execution=ctx, table_io=io, events=JsonLinesSink(sys.stderr))
```

One JSON line per event carries run id, plan fingerprint, task, targets, attempt, timings, state and an error
with a class: `task_failure`, `missing_input`, `configuration`, `platform` or `statistical_alert`. Secrets and
row contents are redacted before they reach any event, report or alert.

## Reports and diagnosis

```bash
bricksmith run apply --plan plan.json --report report.json
bricksmith ops report --report-in report.json --plan plan.json          # add --json for machines
bricksmith ops diagnose --report-in report.json --plan plan.json --task main.sales.mart
```

A diagnosis names the cause, the upstream chain, the root causes, writes that may be partial and the review-first
`bricksmith run replay` command. Cost is shown as `unavailable` with a reason unless a usage provider reports it.

## Readiness

```toml
[[ops.inputs]]
table = "main.sales.raw_orders"
max_age_seconds = 86400
min_rows = 1
required_columns = ["order_id"]
max_null_fraction = { order_id = 0.0 }
```

`bricksmith ops readiness` evaluates them against the selected environment (exit 4 when not ready), or use
`--table NAME --max-age S --min-rows N`. Library: `evaluate_readiness(specs, inspector, now)` with any inspector.

## Alerts

```python
notifier = AlertNotifier([FileAdapter("alerts.jsonl")], JsonFileAlertStore("alerts-state.json"),
                         dedup_window_seconds=3600, max_payload_bytes=4096, max_attempts=3)
notify_report(report, notifier)   # delivery failures are recorded in notifier.records; the report is untouched
```

Adapters are opt-in and generic (`LogAdapter`, `FileAdapter`, `WebhookAdapter`); write your own with
`name` and `deliver(payload)`. Monitoring emits `AlertEvent`s to the same notifier.

## Budgets and the dry-run

```toml
[ops.budgets]
max_concurrency = 4
max_scan_bytes = 10_000_000_000
max_maintenance_ops = 2

[ops.estimates."main.sales.order_totals"]
scan_bytes = 2_000_000_000
```

`bricksmith ops explain --task ...` lists expensive operations and budget violations before anything runs (exit 4
on violation). Sizes are only what you declare; tasks without one are listed as unchecked.
