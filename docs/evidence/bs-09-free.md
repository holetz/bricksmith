# BS-09 evidence: free environment and local Docker

- Date: 2026-10-13
- Environment: `free` (named profile, serverless); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Versions: Python 3.12, databricks-sdk 0.152.0, psycopg 3.3.6 (`postgres` extra), Docker 29.9.0,
  PostgreSQL 16.15 (`postgres:16-alpine`)

## Commands and results

| Proof | Command | Result |
|---|---|---|
| Workspace volumes | `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_volumes.py -q` | 2 passed (about 23 s) |
| PostgreSQL in Docker | `uv run --extra postgres pytest tests/unit/io/test_postgres_docker.py -q` | 1 passed (about 36 s with image pull) |
| Offline contract | `uv run pytest tests/unit/io -q` | passed (SQLite; Docker test skipped without the extra) |

## Scenario states

| Scenario | State | Note |
|---|---|---|
| Create two managed volumes in one test-owned schema | passed | no per-model naming |
| Write, read, list, delete a synthetic artifact; same relative path in both volumes | passed | |
| Byte limit on write; missing artifact; listing after delete | passed | |
| Opt-in checkpoint, restore, export and run cleanup on a volume | passed | |
| `/Volumes` filesystem client on compute | not run | proven offline on a temporary root only |
| PostgreSQL interrupted transfer (batch 4 of 7 fails), replay | passed | 24 rows committed, bookmark unchanged, rerun delivers 26 more, 50 rows exactly once; password via secret reference absent from errors and reports |
| SQL connector against the workspace network | unsupported | Free Edition restricts outbound networking; proof is local |

## Cleanup

Teardown removed the test-owned schema and both volumes with verification (no leftovers reported).
The disposable container was removed by the fixture (`docker ps` shows none of ours).
