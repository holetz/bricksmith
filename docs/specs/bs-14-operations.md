# BS-14. Operational observability and cost controls

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/operations.md).

## Decisions closed

**Events.** `run_plan(..., events=sink, run_id=None)` emits `run_started`, `task_started` (per attempt),
`task_retry`, `task_finished` (every plan entry, including skipped, blocked and not-run) and `run_finished`.
An `Event` has run id, plan fingerprint, environment, time, task id, physical targets, attempt, seconds, state,
a classified error and redacted `fields`; `to_json_line` is stable JSON. Sinks: `JsonLinesSink(stream)`,
`LoggingSink(logger)` (stdlib), `ListSink`; any object with `emit(event)` works. A sink that raises is counted and
ignored: observability never changes a run or its `RunReport`.

**Error classes** (`ERROR_CLASSES`): `task_failure` (default), `missing_input`, `configuration`, `platform`,
`statistical_alert`. `classify_error` takes an exception or the report's `{code,type,message}` record; an
`error_class` attribute or `class` key wins. `StatisticalAlertError` is the explicit statistical failure.
Quality-check and write-check failures count as statistical; non-ready inputs as `missing_input`.

**Redaction.** Secret-looking assignments, bearer tokens, URL credentials, `dapi` tokens, JWTs, `Row(...)` and
literal secrets are masked and text is cut at 500 characters; keys such as `rows`, `data`, `values`, `password`
are masked in structured fields. Applies to events, diagnoses and alert payloads.

**Reports.** `build_ops_report(report, plan=None, provider=None)` gives counts, a diagnosis per failed/blocked
task (cause, class, upstream chain, root causes, possibly partial writes, replay command), recovery guidance and
resource usage; `render_ops_report` is the text form. Cost is `{"status": "unavailable", "reason": ...}` unless a
`UsageProvider` reports a value; nothing is ever estimated.

**Readiness.** Pure `evaluate_freshness`, `evaluate_completeness`, `evaluate_readiness` over `TableStats` from a
`TableInspector`. Statuses `ready`, `stale`, `incomplete`, `missing`, `unavailable` (unknown metadata is never
guessed). `SparkDeltaInspector` reads the last data-changing Delta history entry (maintenance entries ignored).

**Alerts.** `AlertEvent` (kind, subject, message, severity, error_class, target, run id, plan fingerprint,
details) is the generic input BS-13 emits; its `dedup_key` hashes kind, class, subject and target only.
`AlertNotifier(adapters, store, dedup_window_seconds, max_payload_bytes>=256, max_attempts, retry_delay)` never
raises: records are `delivered`, `suppressed` or `failed` (error redacted; a failure is not remembered, so the
next occurrence retries). Payloads are redacted and bounded. Adapters: `LogAdapter`, `FileAdapter`,
`WebhookAdapter` (https, stdlib `urllib`; errors never contain the URL). Stores: in memory or JSON file.

**Budgets** in `bricksmith.toml`: `[ops.budgets]` with `max_concurrency`, `max_transfer_bytes`, `max_scan_bytes`,
`max_maintenance_ops`; sizes come only from `[ops.estimates."<task id>"]` or task `metadata={"ops": {...}}`.
`explain_plan` lists expensive operations (rewrites, merges, maintenance, external sinks), violations, and the
tasks with no declared size (`unchecked`). `[[ops.inputs]]` declares readiness requirements.

## Public contract

`bricksmith.ops` (see `__all__`); `run_plan(events=, run_id=)` extends BS-03 additively.
CLI: `bricksmith ops report|diagnose|readiness|explain`; exit 4 when inputs are not ready or a budget is violated.

## Deviations and pending

- Pending: BS-13 integration (monitor alerts must emit `AlertEvent`; statistical alerts use class `statistical_alert`).
- Budgets are checked at planning (`ops explain`, `explain_plan(...).enforce()`), not enforced inside `run_plan`.
- `RunReport` is unchanged (no run id); the run id lives on events.
- No notification CLI command; delivery is a library call (`notify_report`).

## Acceptance tests

`tests/unit/ops/test_ops.py`: events and redaction, sinks, distinct classes, diagnosis with upstream context,
cost unavailable, readiness statuses, notification failure leaving the run intact, dedup, payload bound, adapters,
budget violation in dry-run, config validation, CLI. `tests/remote/test_ops.py`: runner events, injected failure
and Delta readiness on the free environment.
