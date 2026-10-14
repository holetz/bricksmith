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

## Bounded transport diagnosis and local hardening

A separate tiny tracking-only diagnostic on explicitly selected Free created one uniquely owned
experiment/run and uploaded a synthetic text artifact through the same product boundary. Actual
run metadata pointed to `dbfs:/databricks/mlflow-tracking/<experiment>/<run>/artifacts`; there were
no logged-model outputs, which is expected for `save_model` followed by `log_artifacts`.
The upload returned without error, but both root and `model` artifact inventories were empty.
Run-relative downloads failed with the same generic missing-path exception. A direct absolute
URI probe reported that the run could not be found; it provides no basis for changing the selected
authentication context or inferring a replacement artifact source. The experiment was deleted
in `finally`; no Unity Catalog model was created by this diagnostic.

The installed MLflow 3.17.1 run artifact repository suppresses underlying download exceptions
before reporting the missing-path error. The external upload/download cause remains unresolved.
Official [MLflow client contracts](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html)
support run-relative registration sources and artifact inventories; the failure does not establish
that run-relative URIs or Free Edition registration are unsupported.

The scoped local correction checks every adapter payload/metadata file in the persisted run
inventory before claiming finished training. Empty/partial inventory or a lookup failure marks
the run failed and raises a safe model error, retaining a lookup exception as its cause. Five
new offline regressions cover silent uploads, incomplete/complete nested payloads, safe transport
errors, missing registration payloads and sources bound to the wrong run. These checks do not prove remote transport.
No further diagnostic or lifecycle retry was run; BS-12 and the transport finding remain open.
