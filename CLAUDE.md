# CLAUDE.md — bricksmith

Language: en

bricksmith is a Python framework and project template for Databricks data projects, shipped as
one library with growing capabilities under `src/bricksmith`. The [README](README.md) has the
map; the [roadmap](docs/roadmap.md) summarizes delivery waves and
[Epic 0001](https://github.com/holetz/bricksmith/blob/quenching/specs/5.md) owns the tasks, dependencies and acceptance criteria.

## Operate the repo

```bash
scripts/setup.sh                      # once per clone: hooks, uv sync, denylist check
uv run ruff check && uv run ruff format
uv run pyright
uv run pytest
```

## Ground rules

- **English everywhere**: code, docs, skills, commit messages, specs.
- **Nothing organization-specific enters the repo.** The leak gate blocks commits and pushes that
  match its patterns. Never bypass it (`--no-verify`); when it fires, remove the content instead
  of weakening a pattern.
- **The historical reference review is not an implementation dependency.** Implement from the
  generic epic and public contracts, without requiring or reopening the private source. Historical
  pointers may remain in `CLAUDE.local.md`; ignored evidence is not part of the implementation handoff.
- **Independent development**: implement the generic requirements in the epic, not a translation
  of reference methods or directories. Copy no implementation bodies, fixtures, vocabulary or Git
  history. Keep private evidence in ignored local notes; use independent synthetic acceptance tests.
  See [decision 0010](docs/decisions/0010-behavior-derived-independent-development.md).
- **One library**: one distribution, version and release. Internal modules organize related code;
  they do not impose separate products or a fixed dependency direction between capabilities.
- **Shared task scopes**: data, MLOps and monitoring tasks use the same discovery, dependency
  graph and runner. A dedicated catalog, schema or folder is a project choice, never a requirement
  inferred from task type. See [decision 0009](docs/decisions/0009-one-library-shared-task-scopes.md).
- **Lightweight imports**: keep the root import light and load heavy integrations only where
  used. Optional external dependencies may use extras within the same distribution.
- **Three delivery surfaces**: one Python library, an updatable Copier template and an optional
  Claude Code plugin using public API/CLI contracts. See
  [delivery contracts](docs/planning/delivery-contracts.md).
- **Free-first tests**: `.env` can register free and corporate profiles, but execution selects one
  explicit context; never rely on `DEFAULT` or switch workspaces after a failure. Corporate proof
  supplements unavailable free features. See [test setup](docs/development/databricks-tests.md).
- **No generated YAML**: bundle resources come from Python — read
  [decision 0002](docs/decisions/0002-native-python-bundle-resources.md) before changing that.
- Read [docs/decisions/](docs/decisions/) before changing direction; record a new decision when
  you do.

## Canonical specifications

The `quenching` git branch is the sole specification store. `.agents/quenching.json` selects
`backend: git`; `.claude/quenching.json` shares that envelope. GitHub issue cards are disabled.
Use `cq specs` to read and update canonical plans, tasks and lifecycle records; do not recreate
editable planning bodies under `docs/specs` or `docs/epics`. See
[specification workflow](docs/development/spec-workflow.md) and
[migration provenance](docs/development/specs-migration.md). Product development remains paused
until explicitly resumed; this metadata conversion does not restart the autopilot.
