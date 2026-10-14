# BS-05 evidence: free environment

- Date: 2026-10-13
- Environment: `free` (named profile, serverless compute); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_evolution.py -q`
- Result: 6 passed (about 4 minutes)
- Versions: databricks-connect 17.3 line, Python 3.12, serverless Delta (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| compatible add (nested struct), widen `int` to `bigint`, column comment; re-apply | passed | second `migrate` applies nothing and the plan is empty; unrelated property and rows kept |
| rename with dependent check plus an unrelated check | passed | planned drop check, rename, re-add check; rows preserved; new check enforced; unrelated check kept; re-run is a no-op |
| narrowing refused without policy | passed | table version and type unchanged |
| narrowing `checked` (lossless) and refusal of lossy values | passed | rewrite through a temporary column; a malformed value stops the migration before any column change and no temporary column remains |
| drop column without and with `destructive` | passed | refused first with the column intact; rows kept for the remaining column |
| write with `evolution` policy | passed | compatible new column admitted, narrowing input refused with nothing written, rejection without the key unchanged |
| maintenance only when asked | passed | two writes carrying `maintenance` metadata produced no OPTIMIZE or VACUUM history; planning changed nothing; running added OPTIMIZE and rows were intact |
| primary key add/change on the platform | not run | covered offline only; the live reader tolerates `information_schema` being unavailable |
| clustering (`ALTER TABLE ... CLUSTER BY`) and `VACUUM` | not run | statements are covered by offline plan tests; only `OPTIMIZE` was run remotely |

## Cleanup

Each test used a unique test-owned schema; teardown verified none remained (no leftovers reported). Nothing else
was created in the workspace.
