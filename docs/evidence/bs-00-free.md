# BS-00 evidence: free environment

- Date: 2026-10-13
- Environment: `free` (named profile, serverless compute); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote -q`
- Result: 4 passed

## Stage states

| Stage | State | Note |
|---|---|---|
| configured | passed | profile mode, expected host set, catalog and prefix set, serverless |
| authenticated | passed | resolved host equals `<free-workspace-host>`; identity resolved, not printed |
| compute-tested | passed | `select 1` on serverless through Databricks Connect |
| resources-tested | passed | unique schema and managed volume created and removed |
| deployment-tested | passed | bundle validate, deploy, run and destroy under target `ci` |

## Version matrix

| Component | Version |
|---|---|
| Databricks CLI | 1.11.0 |
| databricks-sdk | 0.152.0 |
| databricks-connect | 17.3.14 |
| databricks-bundles | 1.20.0 |
| Local Python | 3.12.11 |
| Serverless server Spark | 4.2.0 (job environment version 3) |

Databricks Connect 19.2.0 was previously locked transitively and was not exercised remotely; the
`remote` group pins the 17.3 line that was proven.

## Cleanup

After the run: no schema with the test prefix remains in the test catalog, no bundle job remains,
and the bundle target folder was removed. An empty parent folder `.bundle/bricksmith_fixture` stays
in the user's workspace area (left by the CLI's destroy). Injected failure: the teardown test
raised inside the owned-schema scope, removed the schema and verified by listing that none remained.

## Unsupported and not run

- Corporate environment: not run.
- Unattended CI authentication (OAuth machine credentials): not proven; CI remote workflows
  (BS-00.6) not configured.
- Classic compute: not applicable on Free Edition.
- Concurrency: sequential runs only (budget 2).
