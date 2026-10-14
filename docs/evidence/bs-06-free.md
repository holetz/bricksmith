# BS-06 evidence: free environment

- Date: 2026-10-13
- Environment: `free` (named profile, serverless compute); workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`)
- Command: `BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_jobs.py -q`
- Result: 1 passed (about 2 minutes), repeated runs stable

## Scenario states

| Scenario | State | Note |
|---|---|---|
| `load_resources` through the CLI (`python: resources: bricksmith.dab:load_resources`) | passed | bundle validate and deploy under target `ci` (development mode) |
| Managed schema resource | passed | created with a unique name, absent after destroy |
| Job from the graph: two dependent bricksmith tasks as `python_wheel_task` (console script `bricksmith-run`) with the locally built wheel in the serverless environment | passed | both tasks succeeded |
| Condition task, script task (`spark_python_task`), notebook task, run-job task | passed | all succeeded; run-job started the second declared job |
| Order | passed | per-task start/end times from the run: first, second, condition, script, notebook strictly sequential; run-job started after the condition |
| Paused Quartz schedule, `max_concurrent_runs`, timeout, tags | passed | accepted by the platform at deploy |
| Table-update trigger, run-as identity, e-mail notifications | not run | validated offline and converted; no table to trigger on, no second identity or real address |
| Existing-cluster compute | unsupported | rejected offline (`unsupported_compute`); Free Edition is serverless-only |
| Corporate environment | not run | |

## Version matrix

| Component | Version |
|---|---|
| Databricks CLI | 1.11.0 |
| databricks-bundles | 1.20.0 |
| databricks-sdk | 0.152.0 |
| databricks-connect | 17.3.14 |
| Local Python | 3.12.11 |
| Serverless job environment version | 3 (Spark 4.2.0) |

## Platform findings

- Relative notebook paths in Python-defined resources resolve against the file that created the resource
  (otherwise they point into the installed package). Resources are therefore registered with a `Location`
  on the project's `bricksmith.toml`; notebook paths need their file extension.
- A local wheel path in `environments[].spec.dependencies` is uploaded by `bundle deploy` and installed on
  serverless; no `artifacts` section is needed.
- `python_wheel_task` finds the entry point `bricksmith-run` (new console script) in the installed wheel.

## Cleanup

Teardown runs `bundle destroy` on every path, deletes the bundle's workspace folder
(`.bundle/bricksmith_jobs_fixture`, a parent the CLI otherwise leaves behind) and fails when a schema with
the test name survives. After the runs no job, schema or folder of this test remained. The empty
`.bundle/bricksmith_fixture` folder left by the BS-00 run was removed by hand; the BS-00 test still leaves it.
