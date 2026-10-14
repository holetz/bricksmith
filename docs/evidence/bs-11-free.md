# BS-11 evidence: free environment

- Date: 2026-10-14
- Environment: `free` (named profile, serverless compute); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_model_datasets.py -q`
- Result: 1 passed (about 2 minutes)
- Versions: databricks-connect 17.3 line (pyspark 4.0), Python 3.12, serverless Delta (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| `examples/shared_schema` (two model identities, one schema) run through the runner: 13 tasks succeed | passed | source, feature, check, then population, features, labels, training, prediction per model |
| exclusions persisted with first matching reason (churn: 4 minor, 2 inactive rows) | passed | equals hand-derived rows, including customer 6 (minor before inactive) |
| seeded sample (upsell keeps customers 3, 4, 7, 8; customer 4 excluded as low tenure) | passed | Spark selection equals the pure reference |
| training counts (churn 10 rows, 5 train, 4 positives; upsell 6 rows) and prediction count (5) | passed | hand-derived |
| provenance rows: fingerprint, three pinned input versions, no unpinned inputs | passed | |
| training task validates before writing; declared reference date accepted | passed | |
| earlier reference date: immature labels diagnosed on the Spark training table | passed | 5 rows, `immature_label` |

An earlier run of the same scenario failed in the upsell training task: the pre-fit validation reported
`split_label_overlap` for a 60-day label horizon; the example was corrected to 45 days.

## Cleanup

The test used a unique test-owned schema; teardown verified that none remained (no leftovers were
reported). Nothing else was created in the workspace.
