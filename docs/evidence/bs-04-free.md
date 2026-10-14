# BS-04 evidence: free environment

- Date: 2026-10-13
- Environment: `free` (named profile, serverless compute); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_storage.py -q`
- Result: 8 passed (about 9 minutes)
- Versions: databricks-connect 17.3 line, Python 3.12, serverless Delta (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| overwrite repeated; empty input fail / skip / allow_empty | passed | skip leaves the table version unchanged |
| static_refresh populated skip and `force` | passed | |
| protection before mutation | passed | refused write leaves version and rows unchanged; granted write applies |
| partition replacement (named, from input, window, late drop/fail) | passed | neighbours untouched, repeat run identical |
| merge and keyed append, repeat runs | passed | duplicate and null keys rejected without a commit |
| window with late_input fail / drop / accept | passed | |
| append with `allow_duplicates` | passed | documented as not idempotent |
| SCD2 two batches, repeats, backdated, conflicting redelivery | passed | one current row per key, no overlaps, hand-derived rows |
| view and SQL function (repeat) | passed | |
| materialized view | unsupported | `MV_NOT_ENABLED_ON_SERVERLESS_GENERIC_COMPUTE`; reported as `UnsupportedError` |
| history, restore, retention, pre/post hooks, post-check recovery | passed | restore to `version_before` recovers a committed bad write |
| registry comments/properties on write | passed | unrelated column comment and table property kept; BS-07.4 write side only |
| runner `apply` of a two-task project (protected merge target) | passed | refused without grant; applied twice with identical Delta tables |

An empty `MERGE` (dropped late input) still commits an empty Delta version; rejected batches commit nothing.

## Cleanup

Each test used a unique test-owned schema; teardown verified by listing that none remained (no leftovers
were reported). Nothing else was created in the workspace.
