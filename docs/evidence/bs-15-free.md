# BS-15 evidence: free environment

- Date: 2026-10-14
- Environment: `free` (named profile, serverless); host, profile and identity intentionally not recorded
- Versions: Python 3.12, databricks-sdk (remote group), Databricks Connect via the project lock

## Command and result

`BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_governance.py -q`
-> 2 passed (about 70-85 s per run, two consecutive runs).

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Declare two tags (domain, classification) and a SELECT grant to the built-in group `account users` on a synthetic managed table; apply | passed | no refusal |
| Inspect through `information_schema.table_tags` and `SHOW GRANTS`; all controls `verified` | passed | |
| Re-plan after apply is a no-op | passed | |
| Out-of-band tag reported as external, plan stays empty, tag still present after apply | passed | not deleted |
| Reading `column_masks` / `row_filters` | passed | readable, none present |
| Classification reported as effective masking | passed (false) | `effective_masking` is false |
| Mask / row-filter mutation | unsupported | no adapter in this release (by design, not a platform refusal) |
| Schema, catalog, view controls and table properties on the platform | not run | offline only |

## Cleanup

Teardown removed the test-owned schema and table with verification (no leftovers reported).
