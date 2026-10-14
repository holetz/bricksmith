# BS-11. Model datasets and shared-scope task behaviors

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/model-datasets.md).

## Decisions closed

- **Identity.** An explicit identifier in `metadata.model_dataset.model`, repeated in the spec file. Never derived
  from folder, class path, catalog or schema; a mismatch is an error.
- **Metadata.** `model_dataset = {model, role, spec}` (open metadata, ignored by the write layer). The spec is a
  TOML/JSON file relative to the project root or an inline mapping, so discovery stays static. At run time
  `dataset_spec(fn)` reads the declaration of the running task.
- **Sampling.** Key hash `sha256("<seed>:<key>")[:8] / 2^32 < fraction`, identical in Spark and the pure reference.
- **Exclusions.** Ordered; the first matching reason is persisted per excluded row. Rows outside the sample are not
  part of the cohort.
- **Point in time.** Reference date in the spec; features, snapshots and labels observed after it are findings, as
  are features newer than their snapshot. Labels are observable at `snapshot + maturity_days`.
- **Fingerprint.** SHA-256 over the canonical spec, the role and the pinned input table versions.
- **Task kinds.** Free labels (`population`, `features`, `labels`, `training_dataset`, `prediction_dataset`); the
  graph treats them like any other task and they may share a schema with data and check tasks.

## Public contract

`bricksmith.mlops`: `parse_spec`, `load_spec_file`, `ModelSpec`, `dataset_spec`, `parse_declaration`,
`validate_dataset`, `validate_prediction`, `ValidationReport`, `Finding`, `fingerprint`, errors `DatasetError`
(`dataset_invalid`) and `DatasetValidationError` (`dataset_validation_failed`). Submodules `reference` (row dicts),
`spark` (DataFrames), `project.scan_project`. CLI: `bricksmith dataset validate|describe`.

## Deviations and limits

Spark validation collects the projected key/time/label/split columns instead of aggregating on the cluster. Splits
are date based only. Training and registration are BS-12.

## Acceptance tests

`tests/unit/mlops/test_datasets.py` (hand-derived cohorts, leaks, maturity, renames, split overlap, fingerprint,
both examples) and `tests/remote/test_model_datasets.py` ([evidence](../evidence/bs-11-free.md)).
