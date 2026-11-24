---
type: project
title: "Model flow evidence: free environment"
description: "Remote test evidence for the end-to-end model flow example (spec 84) on the free Databricks environment."
resource: "tests/remote/test_model_flow.py, examples/model_flow/**"
timestamp: "2026-11-24T23:30:00-03:00"
audience: both
authority: current
---

# Model flow evidence: free environment

- Date: 2026-11-24
- Specification: spec 84 (epic 85), the end-to-end model flow example
- Environment: `free` (named profile, serverless compute). The workspace host, profile and identity are
  intentionally not recorded (`<free-workspace-host>`).
- Command: `python scripts/build_kernel.py`, then
  `BRICKSMITH_TEST_ENV=free BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_model_flow.py -q -s`
- Result: 1 passed on the final code (about 16 minutes)
- Versions: databricks-connect 17.3, MLflow 3.17, scikit-learn 1.9, Python 3.12

The test copies `examples/model_flow` into a temporary project and runs it through the shared runner. It maps the
`demo` catalog alias to the test catalog and the `flow` schema alias to a unique test-owned schema. The guide is
[End-to-end model flow](../../how-to/model-flow.md).

## Stage states

| Stage | State | Note |
|---|---|---|
| JVM kernel installed as a Unity Catalog Scala function in a test volume | passed | |
| Synthetic source `accounts_monthly` | passed | one row per account and closing |
| `account_features` and `customer_features` under `engine="auto"` | passed | the run report records `engine: jvm`, `path: unity-catalog` for both; no fallback |
| Data stability on both feature tables | passed | results stored in `stability_results` for both tables; warn-only gate |
| Population with the exclusion cascade | passed | every source row present, excluded rows flagged and kept |
| Labels | passed | both label values present |
| Training ABT (`dev` and `oot`) | passed | no excluded row; `feature_reads` names both feature tables |
| Selection ledger and variables tables | passed | at least one feature selected |
| Training run with the document artifacts and the model artifact | passed | eight features; run `FINISHED` |
| Registration and the `champion` alias | Unsupported | the free workspace refuses to create the version: "Unable to download model artifacts from source artifact location 'runs:/…/model' in order to upload them to Unity Catalog" (spec 88) |
| Scoring pinned by run (`model_run`) | passed | `dev`, `oot` and `live` samples; prediction contract plus the label; every row carries `model_version` `run:<run id>`; grades A to D |
| Model monitoring on the prediction table | passed | six periods measured, no error; lights and alerts stored in the `model_monitoring_*` tables |
| Evaluation tables | passed | results for the `dev` and `oot` samples at least, stamped with the training run id |
| Technical document published to the training run | passed | Markdown downloaded on its own; carries the feature sources and the coefficients |
| Unity Catalog Feature Engineering adapter | not run (supplementary) | the remote dependency group does not install the `features` extra |

Scoring by alias and the registered-version documentation tag are proven offline only: the unit tests pin by alias
over a fake store, and the spec 80 evidence covers the version tag on a workspace that supports registration.

## Defects found and fixed

The first remote runs found two defects in the example. Both were fixed before the final run:

- The labels task built the label as a boolean chain over a nullable "closed at" column. Spark evaluated it to
  null for accounts that never close, and the training ABT refused 727 rows with no label. The label is now a
  `when`/`otherwise` expression, so it is always 0 or 1.
- Under MLflow 3 the scikit-learn flavor serializes the model as `model.skops`, not `model.pkl`. Scoring by run now
  reads the file name from `MLmodel` and downloads each file on its own, because the free tier does not list
  artifacts.

A refused registration still creates the registered model before the version fails. The test therefore deletes the
model in `finally` whatever the registration outcome.

## Cleanup

The test used a unique test-owned schema, a managed volume for the kernel JAR and an experiment named after the
schema. In `finally` it deleted the registered model and the experiment. The volume fixture removed the volume, and
the schema fixture verified that the schema, its tables and the kernel function were gone. No leftovers were
reported. After the earlier runs, a separate check found no remaining test schema, registered model or experiment.
