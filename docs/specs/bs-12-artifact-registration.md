# BS-12 follow-up: downloadable artifacts for Unity Catalog registration

Status: open next-round integration finding; tracking [issue 2](https://github.com/holetz/bricksmith/issues/2).
Discovered on 2026-10-14 during the autonomous recovery of
[BS-12](bs-12-models.md). No fix is included in the recovery changes.

## Problem

The explicitly selected Free Edition lifecycle test trains a model and passes training/tracking
assertions, but registration cannot retrieve its logged artifact for upload to Unity Catalog.
Consequently the real end-to-end model contract remains unverified: aliases, prediction,
fresh-interpreter artifact loading, migration, promotion, rollback and retirement never execute.

## Reproduction

From the configured development checkout, without changing workspace or weakening assertions:

```bash
BRICKSMITH_TEST_ENV=free BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/test_models.py -q
```

Observed with Python 3.12, Connect 17.3.14, MLflow 3.17.1, scikit-learn 1.9.1 and NumPy 2.5.4:
`1 failed, 2 warnings in 314.91s`. At `tests/remote/test_models.py:91`, `register_run` invokes
`src/bricksmith/mlops/store.py:193`. MLflow reports:

```text
MlflowException: Failed to download artifacts from path 'model', please ensure that the path is correct.
Unable to download model artifacts from source artifact location 'runs:/<run>/model' in order to upload them to Unity Catalog.
```

Host, profile, identity and run identifier are omitted. See the complete scenario and cleanup
states in [BS-12 Free evidence](../evidence/bs-12-free.md): training passed; registration failed;
later lifecycle steps did not run; cleanup completed, with zero matching test schemas afterward.

## Proposed investigation and acceptance

Resolve the logged artifact URI and destination using public MLflow contracts and confirm that
the artifact exists and is downloadable under the same explicit context before registration.
Fix the product's artifact logging/resolution as indicated by that evidence. Add a meaningful
offline regression where feasible, then rerun the unchanged Free lifecycle test and retain
verified cleanup. Do not alter dependencies, grants or execution surfaces without the owning
approval, weaken assertions, silently fall back to corporate, or claim Free support is absent
from this single failure. BS-12 stays open until that real scenario passes.
