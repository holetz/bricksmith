# BS-10. Isolated experiments and frozen inputs

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/experiments.md).

## Decisions closed

**Freezing and promotion format.** A revision is new configuration, not rewritten source: a TOML or JSON
file (`name`, `roots`, `downstream`, `suffix`, `parameters`, `pins`, `seeds`, `backfill`, `compare`, `retain`).
Freezing uses Delta version references: a pin names an input and one of `version`, `timestamp` (newest
version at or before it) or `current`; evidence stores the resolved version number. Everything else read
from outside the boundary is recorded as `live` (unpinned).

**Boundary.** Rebuilt tasks are the roots (plus their downstream closure with `downstream = true`). Upstream
tasks are never rebuilt: their outputs are inputs, frozen or live. Consumers outside the boundary are
listed, not rebuilt.

**Isolation.** Each rebuilt task's physical schema becomes `<schema><suffix>` (default `_x_<name>`) through
the context schema mapping; the runner plan is built on that graph, then reads of outputs not rebuilt are
rebound to the baseline objects. `ExperimentIO` reads pinned inputs at their version and refuses any write
outside the destination, so baseline objects cannot be modified by a run. Revision parameters override context
parameters.

**Collisions** (errors, before anything is created): `baseline_collision`, `schema_collision`,
`duplicate_target`, `target_exists` (store checked), `pin_unused`, `pin_unresolved`, `seed_not_isolated`.

**Stateful tasks.** A temporal edge (`read_state`) to a rebuilt output needs that output's history in the
destination. `[[seeds]]` copy baseline history (optionally at a version) before the run. Without a seed the
plan is an error (`unseeded_state`) when the revision declares `[backfill]`, a warning otherwise.

**Evidence.** `Evidence` holds canonical JSON (revision, resolved inputs, targets, seeds, effective parameters,
per-task source and declaration fingerprints, configuration fingerprint, creation stamp) and its SHA-256.
The file is created exclusively and verified on read. Running, comparing and promoting re-plan from the
frozen revision and pins and refuse stale evidence (`stale_evidence`). Source fingerprint is the whole task
module file (conservative); helper modules are not followed (limit).

**Compare, promote, retire.** `compare_revision` uses `bricksmith.quality` and names the regression report
after the evidence fingerprint. `plan_promotion` needs a passing report for that evidence, unchanged
sources, and returns an impact report (tasks, outputs, transitive consumers, parameter changes, frozen and
live inputs, seeds); `apply_promotion` needs an explicit authorization for every baseline object. Retirement
drops only the evidence's isolated tables, then empty schemas (RESTRICT), and lists as retained: baseline
outputs, source data, declared model artifacts; evidence files are never deleted.

## Public contract

`bricksmith.experiments`: `Revision`, `load_revision`, `plan_isolation`, `IsolationPlan`, `Evidence`,
`run_revision`, `compare_revision`, `plan_promotion`, `apply_promotion`, `plan_retirement`,
`apply_retirement`, `ExperimentStore`, `InMemoryStore`, `ExperimentIO`; `experiments.spark.SparkExperimentStore`.
CLI: `bricksmith experiment plan|run|compare|promote|retire` (`run`, `promote`, `retire` mutate only with
`--apply`; promotion also needs `--authorize OBJECT`; `plan --freeze FILE` writes evidence).

## Deviations

No earlier module was changed. Pins on inputs that are not Delta tables are errors (`pin_unresolved`).

## Acceptance tests

`tests/unit/experiments/`: boundary and unpinned inputs, baseline untouched, pinning reproduces the result,
unseeded backfill rejected, collisions, stale sources and configuration, immutable evidence, promotion impact
and gate, retirement, CLI. `tests/remote/test_experiments.py`: see [evidence](../evidence/bs-10-free.md).
