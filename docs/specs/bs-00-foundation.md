# BS-00 Packaging and delivery foundation

- Status: implemented and proven on the free environment (see [evidence](../evidence/bs-00-free.md))
- Epic: [Epic 0001](../epics/0001-bricksmith-library.md), slice BS-00
- Contract: [test setup](../development/databricks-tests.md)

## Decisions

- Heavy integrations stay out of the default environment. The dependency group `remote`
  (`databricks-sdk`, `databricks-connect>=17.3,<18`, `databricks-bundles`) is installed with
  `uv sync --group remote`; it is not part of the default groups. The Connect pin follows the
  serverless server line proven in the evidence (server Spark 4.2.0, serverless environment 3).
- The lock holds one resolution, so the `spark` extra also resolves to the pinned Connect line.
  The extra itself stays unpinned for users.
- Remote tests build their context only from the selected environment; the bundle CLI receives
  the selected profile through its subprocess environment and the fixture declares no host.
- There is no `databricks.yml` at the repository root. The fixture bundle lives in
  `tests/remote/fixtures/minimal_bundle/` and defines its job in Python (`databricks-bundles`).

## BS-00.1 audit findings

- Python `>=3.12` is right: Connect for current serverless needs 3.12 and the CI uses 3.12.
- `Private :: Do Not Upload` is intentional (the library is not published yet) and tested.
- Fixed: development status `Planning` became `Pre-Alpha`; added `Python :: 3 :: Only`,
  `Typing :: Typed` and the `py.typed` marker (the package ships annotations).
- Extras `spark`, `sdk`, `bundle`, `mlops` match the guardrails; the root dependency list is empty.
- Fixed in `clients.py`: Connect rejects an SDK config combined with builder compute parameters,
  so compute now rides on the config (`serverless_compute_id` or `cluster_id`).

## Public contract

- `bricksmith.preflight`: `run_preflight(context, environ=, clients=, offline_only=, run_id=)`
  returns a `PreflightReport` of `StageResult(stage, state, detail)`. Stages: `configured`,
  `authenticated`, `compute-tested`, `resources-tested`, `deployment-tested`; states `passed`,
  `failed`, `unsupported` (for example a missing extra), `not_run`. A stage that does not pass
  leaves later ones `not_run`; deployment is proven by the remote bundle test, never by preflight.
- Helpers `unique_schema_name`, `create_owned_schema` and `remove_owned_schema` (returns leftovers;
  an unverifiable cleanup counts as a leftover).
- CLI: `bricksmith env check [--offline] [--json]` exits 0, or 3 when any stage failed.
- Detail strings carry failure classes, never hosts, profile contents or identities.

## Acceptance tests

- Unit (`tests/unit/core/test_preflight.py`): all stages with fakes, injected volume failure still
  cleans up, stuck deletion reports leftovers, host mismatch stops later stages, missing extra is
  `unsupported`, missing selection fails offline with no fallback, CLI offline output.
- `tests/test_dist.py` (marker `slow`): runs `scripts/verify_dist.py`, which builds the wheel and
  sdist, installs each into a clean environment and imports every module with heavy packages absent.
  CI runs the same script on the built `dist/`.
- Remote (`tests/remote`, opt-in with `BRICKSMITH_RUN_REMOTE_TESTS=true` from the process or the
  explicitly loaded `.env`): preflight stages, owned-schema fixture teardown, injected-failure
  cleanup, and bundle validate, deploy, run and destroy under target `ci` (destroy runs on failure).

## Not covered

Corporate environment (not run), unattended CI authentication (not proven) and the remote CI
workflows (BS-00.6) remain open.
