# CLAUDE.md — bricksmith

Language: en

bricksmith is a Python framework and project template for Databricks data projects, built as a uv
workspace of four distributions under one `bricksmith` namespace. The [README](README.md) has the
map; the [roadmap](docs/roadmap.md) has the phases and their exit criteria.

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
- **The reference implementation is read-only and lives outside this repo.** Rewrite from it,
  never copy files verbatim, never carry over its names. Local pointers may live in
  `CLAUDE.local.md` (gitignored).
- **One-way dependencies**: `registry` and `monitor` stand alone; the core never imports `mlops`
  or `monitor`. Enforced by `tests/test_workspace_layout.py`.
- **`bricksmith` is a PEP 420 namespace**: never add `packages/*/src/bricksmith/__init__.py`.
- **No generated YAML**: bundle resources come from Python — read
  [decision 0002](docs/decisions/0002-native-python-bundle-resources.md) before changing that.
- Read [docs/decisions/](docs/decisions/) before changing direction; record a new decision when
  you do.
