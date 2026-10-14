# BS-14 evidence: free environment

- Date: 2026-10-14
- Environment: `free` (named profile, serverless); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Versions: Python 3.12.11, databricks-sdk 0.152.0, databricks-connect 17.3.2

## Commands and results

| Proof | Command | Result |
|---|---|---|
| Remote operations | `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_ops.py -q` | 1 passed (about 38 s) |
| Offline contract | `uv run pytest tests/unit/ops -q` | passed |

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Runner on Delta tables with one injected failing task: events for every entry, one run id, secret absent | passed | |
| Report diagnoses the blocked dependent with the failed task as root cause and upstream chain | passed | |
| Freshness, row count and null fraction on a real Delta table; absent table reported `missing` | passed | an earlier run exposed a client-time-zone error in history timestamps; fixed by reading epoch seconds |
| Stale/incomplete detection on a real table (row threshold above row count) | passed | |
| Cost information | unsupported | no billing data is read; reported as `unavailable` |
| Webhook delivery to a real endpoint | not run | proven offline with an injected opener |

## Cleanup

Teardown removed the test-owned schema with verification (no leftovers reported).
