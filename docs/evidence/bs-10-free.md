# BS-10 evidence: free environment

- Date: 2026-10-14
- Environment: `free` (named profile, serverless compute); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_experiments.py -q`
- Result: 1 passed (about 81 seconds)
- Versions: databricks-connect 17.3 line (pyspark 4.0), Python 3.12, serverless Delta (see [BS-00](bs-00-free.md))

## Scenario states

| Scenario | State | Note |
|---|---|---|
| pin a Delta input by version (events at version 0 while live data moved to version 1) | passed | plan resolved the pin against the real table |
| run the revision in a revision-suffixed schema through the common runner | passed | isolated output equals hand-derived rows built from version 0 only |
| baseline output untouched | passed | Delta version and rows unchanged |
| compare baseline and candidate with `bricksmith.quality` | passed | 0 missing, 0 extra, 2 differing rows; gate verdict fail as expected |
| clean experimental outputs only | passed | isolated table and schema dropped; baseline and source tables kept |
| promotion apply, timestamp pins on Delta, unseeded backfill | not run | covered offline only |

## Cleanup

The experimental table and schema were retired by the revision's retirement plan; the test-owned schema
was removed by teardown with no leftovers reported.
