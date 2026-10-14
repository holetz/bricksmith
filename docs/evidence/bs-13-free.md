# BS-13 evidence: free environment

- Date: 2026-10-14
- Environment: `free` (named profile, serverless compute); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_monitoring.py -q`
- Result: 2 passed on the final code, in two invocations: the parity test passed in the combined run (4.5 minutes)
  whose second test failed for the reason below, and the runner test then passed alone (3 minutes).
- Versions: databricks-connect 17.3 line (pyspark 4.0), Python 3.12, serverless Delta (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Spark `fit_spark` equals the pure `fit_reference` on the same tiny data (identical reference and fingerprint) | passed | ties, a null, unseen and out-of-range values, a missing label |
| Spark `measure_spark` equals the pure `measure` for three windows (later, fit, none), including segments and monthly stability | passed | statuses, reasons, counts, values (1e-9) and per-bin details equal |
| Unavailable metrics keep their reason on Spark (`missing_scores` for a segment, no invented value) | passed | |
| `monitor_references`, `monitor_measurements`, `monitor_alerts` run through the runner beside the feature tasks, one schema | passed | tasks of kind `monitor`, merge strategy |
| Persisted measurements equal the pure engine on the example's synthetic prediction table | passed | PSI, AUC, KS, Brier, ECE, coverage, per segment |
| Alert persisted with severity `critical`, class `statistical_alert`, kind `monitor_threshold`; `insufficient_history` visible | passed | |
| Repeat run is idempotent (same row counts and contents in all four tables) | passed | |

An earlier combined run failed because the shared example also contains model tasks owned by another slice that
need an MLflow experiment context; the test now plans only the monitor tasks and their upstream tasks. A first
parity run failed on a wrong expectation of mine (`missing_labels` where the data correctly gives `missing_scores`).

## Cleanup

Each test used a unique test-owned schema; teardown verified that none remained (no leftovers were reported).
Nothing else was created in the workspace.
