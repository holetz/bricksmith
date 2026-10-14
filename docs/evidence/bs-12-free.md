# BS-12 evidence: free environment

- Date: 2026-10-14
- Environment: explicitly selected `free`, named profile, serverless compute. Workspace host,
  profile, identity and run identifiers are omitted.
- Command: `BRICKSMITH_TEST_ENV=free BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_models.py -q`
- Result: **failed**, 1 failed / 2 warnings in 314.91 seconds. No fallback or retry was run.
- Versions: Python 3.12, databricks-connect 17.3.14, MLflow 3.17.1, scikit-learn 1.9.1,
  NumPy 2.5.4; serverless Delta.

## Scenario states

| Scenario | State | Evidence |
|---|---|---|
| Missing and ambiguous model selectors fail without creating a model or experiment | passed | Assertions before the training step ran |
| Shared runner constructs datasets, trains and records metadata and metrics | passed | Training record identity, 5 training / 5 evaluation rows, tracking tags, evaluation metrics and three input table versions asserted |
| Register the logged model in Unity Catalog | failed | `tests/remote/test_models.py:91`, `src/bricksmith/mlops/store.py:193`: MLflow could not download `runs:/<run>/model` for the Unity Catalog upload |
| Alias-bound predict/evaluate tasks | not run | Stopped at registration |
| Pinned prediction in a fresh interpreter and deliberate re-log | not run | Stopped at registration |
| Promotion, rollback and consumer-aware retirement | not run | Stopped at registration |

The error was `MlflowException: Failed to download artifacts from path 'model'`. MLflow's
registration wrapper reported that it could not download the source model artifact to upload
it to Unity Catalog. This is a reproduced failure, not a claim that Free Edition lacks model
registration. The local suite passes its fake-client lifecycle tests, which do not replace this
remote acceptance proof. BS-12 remains incomplete and its epic tasks stay unchecked.

## Cleanup

The test's `finally` block removed its model/experiment resources and the schema fixture
completed without a teardown error or a `LEFTOVERS` report. An additional read-only SDK query
under the explicitly selected free context found zero schemas matching the configured test
schema prefix after completion. No model or experiment was retained for diagnosis; the local
captured log is outside the repository and is not a public artifact.
