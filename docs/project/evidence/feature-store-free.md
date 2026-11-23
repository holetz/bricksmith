---
type: project
title: "Feature store evidence: free environment"
description: "Remote test evidence for the closing-based feature store (spec 76) on the free Databricks environment."
resource: "tests/remote/**"
timestamp: "2026-11-23T08:46:31-03:00"
audience: both
authority: current
---

# Feature store evidence: free environment

- Date: 2026-11-23
- Specification: spec 76 (epic 85), closing-based feature store
- Environment: `free` (named profile, serverless compute). The workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`).
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_feature_store.py -q`
- Result: 2 passed on the final code (about 1 minute)
- Versions: databricks-connect 17.3 line (pyspark 4.0), Python 3.12, serverless Delta (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Spark `shift_column` / `is_closing_column` equal the row calendar (monthly −13 and +1, quarterly, annual, weekly) | passed | month ends include 2024-02-29 |
| Spark `stamp_availability` equals the row engine (lag 1, the described column replaced by the closing) | passed | January 2024 rows stored at 2024-02-29 |
| Spark `build_training_set` equals the row engine row for row and column for column | passed | two lookups (offset 0 and offset 1 with a prefix), a missing entity with declared defaults, a stored null kept null |
| `reads` reports the Delta version read | passed | version 0 for a freshly written table, read with `VERSION AS OF` |
| A duplicated primary key in a frame is refused by `check_frame`; an unaligned observation closing is refused | passed | |
| A `feature_table` task plans `replace_partitions` keyed by entity and closing and writes through `SparkTableIO` | passed | derived options, no write metadata declared by the task |
| Re-running one closing replaces only that closing | passed | the other closing and the other account are untouched |
| A duplicate primary key fails the load (storage key check) and writes nothing | passed | `check failed: keys are unique in the input` |

The Unity Catalog Feature Engineering adapter was not run live: the remote dependency group does not
install the optional `features` extra, so the adapter is proved by offline tests against a fake client
(`tests/unit/mlops/test_featurestore_uc.py`).

The first run failed once for a reason fixed before the final run: the synthetic task declared its
`feature_table` through a module constant. Declarations must be literals, so the test now writes it inline.

## Cleanup

Each test used a unique test-owned schema. Teardown verified that none remained, and no leftovers were reported.
Nothing else was created in the workspace.
