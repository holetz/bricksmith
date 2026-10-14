# BS-08 evidence: free environment

- Date: 2026-10-13
- Environment: `free` (named profile, serverless compute); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_quality.py -q`
- Result: 4 passed (about 3.5 minutes)
- Versions: databricks-connect 17.3 line (pyspark 4.0), Python 3.12, serverless Delta (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| comparison counts on Delta tables (1 missing, 1 extra, 3 matched, 1 differing row, per-column 1/1) | passed | equals hand-derived values and the pure reference report |
| tolerances at and beyond the boundary (absolute 0.5 vs 0.25; relative 0.25 vs 0.2) | passed | exactly representable operands |
| bounded samples (limit 2 and 1; none without a limit) | passed | counts stay complete |
| identical, empty and one-sided-empty datasets | passed | |
| duplicate keys and null keys invalidate | passed | no value comparison, gate fails |
| unsupported types (map, binary) | passed | warnings when implicit, plan blocked when explicit |
| each check kind on Spark (grain, cardinality, nullability, accepted values, completeness, freshness) | passed | unavailable for empty data, wrong type, missing column |
| check task through the runner (`kind="check"`) | passed | passes on good data; fails with the quality error on bad data |
| write conditions as `input_checks` / `target_checks` | passed | refused batches leave the Delta version unchanged |

Tolerant comparison on Spark runs in double precision; the boundary is exact only for exactly
representable values (the pure reference uses decimals).

## Cleanup

Each test used a unique test-owned schema; teardown verified that none remained (no leftovers were
reported). Nothing else was created in the workspace.
