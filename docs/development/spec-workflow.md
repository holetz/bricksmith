# Specification workflow

Specifications, epics, tasks, outcomes and handoffs live on the `quenching` git branch.
Use `cq specs`; both agent hosts share the same git backend and cards are disabled.
The [canonical development epic](https://github.com/holetz/bricksmith/blob/quenching/specs/5.md) preserves the original acceptance
contracts. The [human-decision epic](https://github.com/holetz/bricksmith/blob/quenching/specs/6.md) retains never-forced choices.
Product development remains paused; only the authorized metadata conversion was resumed.
The source-to-ID/hash mapping is [recorded here](specs-migration.json).

## Shared architecture guardrails

These guardrails keep independently developed slices coherent. A slice specification may refine
them and must record any deviation.

- **Root dependencies stay empty.** The CLI uses `argparse`; data formats use the standard library
  (`tomllib`, `json`). Heavy integrations are extras of the one distribution and are imported
  inside the functions that use them: `spark` (Databricks Connect), `sdk` (Databricks SDK),
  `bundle` (`databricks-bundles`), `mlops` (MLflow and a small model library).
- **Module map** under `src/bricksmith`: `errors`, `context`, `params`, `env`, `clients`, `cli/`,
  `tasks/` (declaration and discovery), `graph/`, `plan/`, `runner/`, `storage/`, `evolution/`,
  `dab/`, `registry/`, `quality/`, `io/`, `experiments/`, `mlops/`, `monitor/`, `ops/`,
  `governance/`, `catalogdocs/`. Capabilities import each other freely; no fixed direction.
- **Project layout** is `catalogs/<catalog>/<schema>/**/<module>.py` by default, with a
  `bricksmith.toml` project file for mappings, exclusions and policies. Location gives the logical
  scope; task kind never does.
- **Offline metadata.** Discovery reads task declarations statically, without importing task
  modules or any heavy runtime. Whatever cannot be inferred must be declared explicitly, and the
  graph reports inference limits instead of guessing.
- **Explicit context.** Every remote operation receives an immutable `ExecutionContext`; nothing
  reads a process-global profile, session or `DEFAULT`. Clients are created lazily through
  injectable providers so unit tests use in-memory fakes.
- **Plans before effects.** Mutating capabilities return a serializable plan or report first
  (plain dataclasses with `to_dict`/`from_dict`), then apply it. JSON output is stable and sorted.
- **Errors** derive from `bricksmith.errors.BricksmithError` with a stable `code`; the CLI maps
  them to documented exit codes.
- **CLI** is `bricksmith <group> <command>`; each capability registers its own group from its own
  module so slices do not edit one shared command file.

## Test conventions

- `tests/unit/<capability>/` runs offline with fakes and needs no credentials.
- `tests/remote/` is marked `databricks`, skipped unless `BRICKSMITH_RUN_REMOTE_TESTS=true`, and
  uses only the selected environment from `.env`, a unique test-owned schema and guaranteed
  teardown. A skip is recorded as "not run", never as passing evidence.
- Fixtures are synthetic and authored for bricksmith with hand-derived expected values.
- Evidence of remote runs goes to `docs/evidence/` with environment, versions, date and result.

## Slice delivery checklist

Every slice delivers, in this order:

1. One canonical branch specification closing its open decisions (see the epic's decision table).
2. Code under its modules in `src/bricksmith`, with a CLI group in its own module exposing
   `register(subparsers)` and appended to `GROUPS` in `src/bricksmith/cli/groups.py`.
3. Offline tests under `tests/unit/<capability>/` covering the slice's acceptance paragraph, and
   remote tests under `tests/remote/` (reusing `tests/remote/conftest.py`) when the behavior
   depends on the platform. Remote tests are run for real on the selected environment
   (`BRICKSMITH_RUN_REMOTE_TESTS=true uv run --group remote pytest tests/remote/<file> -q`), kept
   tiny, and leave nothing behind in the workspace.
4. `docs/evidence/bs-NN-<env>.md` for remote proof: date, versions, per-scenario state (passed,
   failed, unsupported, not run) and cleanup result, with no hosts, profile names or identities.
5. A short user guide under `docs/guide/`, and ticks in the epic only for work done and proven.

Existing product gates before a slice is finished: `uv run ruff check && uv run ruff format && uv run pyright &&
uv run pytest -q`. Ruff selects every rule: write docstrings and full type hints and add only
narrow, justified ignores. Extend earlier slices' public contracts rather than replacing them, and
record any change in the slice specification.

## Current writer limitation

Stock CQ 6.4.0 git-store writes add `--no-verify`; use the explicitly documented temporary
process adapter to preserve normal hooks until the upstream fix is available. Read-only CQ
commands remain safe. See [migration provenance](specs-migration.md). No tool catalog or
execution grant was expanded during this conversion.
