---
type: project
title: "Data stability evidence: free environment"
description: "Remote test evidence for the data-stability epic on the free Databricks environment."
resource: "tests/remote/**"
timestamp: "2026-11-23T01:23:04-03:00"
audience: both
authority: current
---

# Data stability evidence: free environment

- Date: 2026-11-23
- Specifications: epic 64, members 65–69 (shared core, binning, profiles and metrics, run integration, dashboard tables)
- Environment: `free` (named profile, serverless compute). The workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`).
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_stability.py -q`
- Result: 2 passed on the final code (3.5 minutes)
- Versions: databricks-connect 17.3 line (pyspark 4.0), Python 3.12, serverless Delta (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Spark `fit_spark_binning` equals the pure `fit_rows_binning` (identical reference, fingerprint and skip reasons) | passed | inferred columns: quantile bins with an exact-zero bin, categorical text and boolean, excluded and weight columns |
| Spark `profile_spark` equals the pure `profile_rows` on three periods, two segment sets and a weight | passed | counts, weights, value sums and squares per bin (1e-9) |
| `check` on a DataFrame equals `check` on rows: every result row and the outcome | passed | `previous` baseline, a new category counted in `unseen_rate` |
| The example's `sales_monthly` runs through the shared runner with `SparkTableIO`; the plan lists the five stability tables as `merge` targets | passed | all five tables created, including an empty alerts table for the dashboard views |
| Identical months give PSI exactly 0 and no alerts; a rerun profiles only the refreshed month and changes no row count | passed | merge on the documented keys |
| A drifted last month: amount PSI red, `channel=app` volume change 1.0 red, `promoted` green; a critical alert opens | passed | segment columns are not monitored as distributions |
| The gate `fail_on: ["red"]` fails `sales_monthly` with `stability_gate` and blocks `sales_report` | passed | the output stays published; the report carries the stability summary |
| `bricksmith stability views` SQL creates `stability_latest`, `stability_dataset_status`, `stability_open_alerts`, and they can be queried | passed | the worst status of the drifted month is `red` |

The first runs failed for reasons fixed before the final run:

- A wrong expectation about which synthetic segments exist.
- The example declared its configuration through a module constant. Declarations must be literals, so the example
  now writes it inline, and an offline unit test plans the example.
- No alerts table on a quiet first run, which broke the views. Stability tables are now created with an empty
  write when they do not exist yet.
- An expectation that a segment column is monitored, when by design it is not.

## Stable kept segment set (spec 116)

- Date: 2026-11-24
- Environment: `free` (named profile, serverless compute); nothing ran on the corporate environment.
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true BRICKSMITH_TEST_ENV=free uv run --group remote pytest
  tests/remote/test_stability.py -q -k "pure_engine or action_count"`
- Result: 3 passed (76 seconds)

| Scenario | State | Note |
|---|---|---|
| `profile_spark` equals `profile_rows` when segments are ranked over the loaded periods and a stored segment is carried | passed | a kept segment that vanished gets zero rows; a carried segment outranked over the loaded periods stays kept; the truncation report is identical |
| A second `check` on a DataFrame equals the same `check` on rows after a first run: results and outcome | passed | the collapsed segment's `volume_change` is red and the `fail_on: ["red"]` gate fails on both engines |
| Profiling stays three Spark actions for narrow and wide segmented tables | passed | the ranking job also returns each segment's first period |

## Cleanup

Each test used a unique test-owned schema. Teardown verified that none remained, and no leftovers were reported.
Nothing else was created in the workspace.
