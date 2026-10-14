# BS-06. Native bundles and orchestration

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/bundles-and-jobs.md);
[decision 0002](../decisions/0002-native-python-bundle-resources.md).

## Decisions closed

**Two layers.** `bricksmith.dab.model` is a pure, serializable resource model (frozen dataclasses with
`to_dict`/`from_dict`, sorted JSON, `ResourceSet.fingerprint`); it needs neither Spark nor
`databricks-bundles`. `bricksmith.dab.adapter` converts it to `databricks.bundles` objects (through each
class's `from_dict`, imported lazily). `load_resources(bundle)` builds the set for `bundle.target` and raises
`ValidationError` with every error before anything is returned, so an invalid project never deploys.

**Declarations.** Jobs and bundle settings live in the `[bundle]` and `[jobs.<key>]` tables of
`bricksmith.toml`, or are built as `JobSpec`/`BundleSettings` in Python and passed to `assemble`. Unknown keys
are a `ConfigError`. Catalogs are referenced by default (`manage_catalogs = false`; a catalog is rarely the
project's to create); schemas are managed by default; volumes are declared explicitly. `managed = false`
keeps the entry in the snapshot but emits no bundle resource. `owner` becomes an `ALL_PRIVILEGES` grant.
`targets` scopes any resource or job to named bundle targets; validation always covers every target.

**Common entrypoint.** Every graph task becomes a `python_wheel_task` calling the console script
`bricksmith-run` (a new `[project.scripts]` entry for `bricksmith.runner.entrypoint:run_entrypoint`) with
`--project ${workspace.file_path} --target ${bundle.target} [--env E] --task <literal id> --no-upstream
--partial`. Task ids are escaped so `fnmatch` metacharacters (variants) select exactly one task. The job
environment is serverless, `environment_version` from `[bundle]` (verified: `3`), with the bricksmith wheel as
dependency; every non-condition, non-run-job task uses it.

**Dependencies.** Edges inside a job mirror the graph's data edges. A producer outside the job is acceptable
only if it is static (outputs assumed present, as in BS-03) or covered by a contract on the consuming job:
`run_job:<job>` (auto-creates a `run_<job>` step and makes consumers depend on it; the producer must belong
to that job and jobs may not start each other), `trigger` (the job's table trigger must watch one of the
producer's physical outputs) or `external` (acknowledged input; rejected when the producer belongs to another
bricksmith job). Without a contract: `missing_producer` (nobody runs it, so a filter removed it),
`cross_job_edge`, or `disabled_producer`. A task may belong to one job only (`duplicate_task`).

**Other task kinds.** Steps of kind `script` (`spark_python_task`), `notebook`, `condition` and `run_job` order
with `after` (task id or step key, `key:true|false` for a branch) and `before`. Any other kind, a compute
other than serverless, a schedule together with a trigger, both run identities, a non-Quartz schedule, a missing
wheel or a step cycle fails with a stable code in `bricksmith bundle validate` and in `load_resources`.

**Collisions and hand-written resources.** Job-name, resource-key and physical-schema collisions are errors.
`[bundle] preserved = ["jobs.legacy"]` names hand-written resources: they are listed in the snapshot, never
read, changed or removed, and a Python resource with the same `type.key` is `preserved_collision`.

**Job parameters** are validated scalars exposed as job parameters. The entrypoint has no option to feed them
to tasks yet (deferred to the slice that adds task parameters).

## Public contract

`bricksmith.dab`: `build_resources`, `assemble`, `Assembly`, `ResourceSet` and its resource dataclasses,
`BundleSettings`, `JobSpec`, `StepSpec`, `VolumeSpec`, `ScopeOptions`, `load_settings`, `task_key`,
`job_dict`, `to_resources`, `load_resources`. CLI: `bricksmith bundle resources|validate` (offline, `--json`,
`--target` scopes; exit 4 on errors).

## Deviations

- New console script `bricksmith-run` in `pyproject.toml`; the wheel task needs an entry point name.
- Table-update triggers, run-as identities and notifications are validated and converted but not deployed
  remotely (see the evidence file). Python-defined resources carry a `Location` pointing at `bricksmith.toml`,
  because the bundle resolves relative notebook paths against the file that defined the resource; notebook
  paths include their extension (`src/note.py`).

## Acceptance tests

`tests/unit/dab/test_dab.py` (no Spark; the pure model runs with `databricks` blocked): mixed kinds ordered,
filter and exclude rejected, disabled/static producers, cross-job contracts and cycles, trigger contract,
collisions and preserved resources, unsupported options, target scoping, stable snapshot, CLI, adapter.
`tests/remote/test_jobs.py`: deploy, run in order, destroy ([evidence](../evidence/bs-06-free.md)).
