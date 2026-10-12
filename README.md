# bricksmith

A Python framework and project template for Databricks data projects.

A bricksmith project is a folder tree: catalogs, schemas and tasks are discovered from where they
live, task dependencies are inferred from the code that reads them, and Databricks bundle
resources (jobs, schemas, volumes) are built natively in Python — nothing is generated to YAML.
On top of that sit a column registry with a naming grammar, a model development flow and data
and model monitoring, each with agent skills that cover the work end to end.

> **Status:** planning. Private repository, nothing released. See the [roadmap](docs/roadmap.md).

## Distributions

All four share the `bricksmith` namespace and are released as separate wheels.

| Distribution | Import | Depends on | Purpose |
|---|---|---|---|
| `bricksmith-registry` | `bricksmith.registry` | — | Column registry and naming grammar checks. Pure Python, no Spark. |
| `bricksmith` | `bricksmith.*` | registry | Core: discovery, tables, tasks, jobs, the dependency runner and native bundle resources. |
| `bricksmith-mlops` | `bricksmith.mlops` | core, monitor (optional) | Samples, slices, exclusions, the model chain and MLflow on Unity Catalog. |
| `bricksmith-monitor` | `bricksmith.monitor` | — | Binning, PSI/CSI, discrimination, calibration and alert rules. DataFrame in, DataFrame out. |

## Layout

```
packages/      the four distributions (uv workspace members)
tests/         workspace-level contracts (namespace, dependency direction, leak gate)
scripts/       setup and the leak gate
docs/          roadmap and decision records
```

Planned: `template/` (copier template for a new data project), `examples/` (the rendered template,
deployed by the end-to-end tests) and `plugins/` (agent skills for Claude Code and Codex).

## Development

```bash
scripts/setup.sh          # hooks, uv sync, denylist check — once per clone
uv run ruff check && uv run ruff format --check
uv run pyright
uv run pytest
```

Commits and pushes go through a leak gate (`scripts/leak_check.py`): generic patterns plus a
private denylist kept outside the repository. See
[decision 0003](docs/decisions/0003-private-first-with-a-leak-gate.md).

## License

[MIT](LICENSE).
