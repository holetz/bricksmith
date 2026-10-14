# BS-12. Model training, artifacts and lifecycle

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/models.md). Extends
[BS-11](bs-11-model-datasets.md); does not replace it.

## Decisions closed

- **Model library and registry.** scikit-learn (logistic regression) behind a `ModelAdapter` protocol; MLflow with
  the Unity Catalog registry (`databricks-uc`) through `Clients.tracking()`, whose registry URI is now set
  explicitly (`clients.tracking_uris`). Both stay in the `mlops` extra and the `remote` group.
- **Adapter contracts.** `train(spec, rows, params)`, `evaluate(model, features, label, rows)`,
  `predict(model, features, rows)`, plus `save`/`load` (the artifact format) and `dependencies()`. Rows are lists of
  dicts; `evaluate`/`predict` receive only the ordered `(name, type)` feature contract, so they work from an
  artifact without the project spec. Undefined metrics (AUC with one class) are `None`.
- **Artifact format.** An MLflow `sklearn` model directory with signature, plus `bricksmith_model.json`
  (`bricksmith.model/1`): model, adapter, feature contract, label, key, snapshot, spec hash, datasets (BS-11
  fingerprint, config hash, input table versions, rows), parameters, metrics per split, dependency versions,
  code revision, trained-at, card fields, `migrated_from`. The run mirrors it as params, metrics and tags.
- **Identity and naming.** `model_task.model` is explicit. The registered name is `<catalog>.<schema>.<model>`:
  from the declared logical `scope` through the context mapping, else from the physical schema of the task's
  first output. A registered model and each version carry `bricksmith_model`; a mismatch is `model_incompatible`.
- **Resolution.** Evaluate and predict resolve one explicit `version` or `alias`; none, both, `latest`-style
  names and `vN` aliases are `model_ambiguous`; unknown ones `model_missing`; retired versions are refused.
- **Compatibility.** Before loading: adapter, feature contract equality and python / scikit-learn / numpy
  major.minor against the recorded dependencies. `worker` repeats this in a fresh interpreter.
- **Lifecycle state** lives in tags: version `bricksmith_state` (`active`/`retired`) and `bricksmith_events`
  (last 8 review events), registered-model `bricksmith_history_<alias>` (last 8 moves, used by rollback).
- **Retirement** never deletes runs or artifacts; it removes aliases and tags versions. Consumers are
  `evaluate`/`predict` tasks whose pinned version or current alias target is retired; downstream readers are listed.

## Public contract

`bricksmith.mlops`: `adapter` (`ModelAdapter`, `SklearnAdapter`, `get_adapter`), `meta` (`ModelMetadata`,
`render_card`, `check_runtime`), `store` (`ModelStore`, `registered_name`, `check_selector`), `training`
(`train_model`, `register_run`, `predict_rows`, `evaluate_rows`, `predict_in_subprocess`, `relog_model`),
`lifecycle` (`plan_promotion`, `plan_rollback`, `apply_alias_plan`, `plan_retirement`, `apply_retirement`,
`alias_impact`, `model_uses`), `tasks` (`parse_model_task`, `train_task`, `evaluate_task`, `predict_task`).
Errors `model_invalid`, `model_missing`, `model_ambiguous`, `model_incompatible`, `model_in_use`,
`feature_contract_mismatch`. CLI `bricksmith model train|register|promote|rollback|retire|card`.

## Deviations and limits

Training collects the (tiny) training dataset to the driver process. Features must be numeric or boolean for the
sklearn adapter. Datasets are recorded as tags and metadata, not as MLflow dataset inputs. The train task writes a
run record and does not register: registration and promotion are explicit steps.

## Acceptance tests

`tests/unit/mlops/test_models.py` (fake tracking client) and `tests/remote/test_models.py`
([evidence](../evidence/bs-12-free.md)).

## Outcome

Recovered implementation on 2026-10-14: the unchanged local gate passes (Ruff lint and format,
Pyright, 415 tests passed / 34 skipped). Offline tests cover independent model identities in one
schema, explicit selectors, incompatible artifacts, metadata, promotion/rollback, active-consumer
retirement and artifact preservation. Free Edition lifecycle proof is required before this slice
is marked complete; skipped remote tests in the local gate do not supply that proof. The explicit
Free Edition recovery run failed while registering the logged artifact in Unity Catalog:
MLflow could not download `runs:/<run>/model` for upload. Earlier training/tracking assertions
passed; alias prediction, the fresh-process loader and lifecycle steps did not run. Test resources
were removed. The [failure evidence](../evidence/bs-12-free.md) records the reproduction; BS-12
remains open, with no remote retry or corporate fallback.
