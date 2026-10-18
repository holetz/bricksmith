# bricksmith

A Python library, project template and optional Claude Code plugin for Databricks data projects.

A bricksmith project is a folder tree: catalogs, schemas and tasks are discovered from where they
live, task dependencies are inferred from the code that reads them, and Databricks bundle
resources (jobs, schemas, volumes) are built natively in Python — nothing is generated to YAML.
The same library grows to include a column registry with a naming grammar, a model development
flow and data and model monitoring, with agent skills that cover the work end to end.

> **Status:** planning. Public repository, nothing released yet. See the [roadmap](docs/roadmap.md)
> and [development epic](https://github.com/holetz/bricksmith/blob/quenching/specs/5.md).

## One library, growing capabilities

`bricksmith` ships as one Python distribution with one version and release. Internal modules
organize related code; capabilities share abstractions and integrate where useful.

| Capability | Purpose |
|---|---|
| Framework and bundle integration | Discovery, tables, tasks, jobs, the dependency runner and native bundle resources. |
| Registry | Column registry and naming grammar checks that run without Spark. |
| MLOps | Samples, slices, exclusions, the model chain and MLflow on Unity Catalog. |
| Monitoring | Binning, PSI/CSI, discrimination, calibration and alert rules. DataFrame in, DataFrame out. |

Data, MLOps and monitoring tasks use the same discovery, dependency graph and runner. A task's
type describes its behavior; its location defines its catalog and schema. A model training task
can live beside feature preparation and monitoring tasks in the same scope. A project may choose
dedicated scopes for these tasks, but the framework does not require them or derive a scope from
the task type.

For example, a schema may contain all of these tasks together:

```text
catalogs/analytics/customer/
  features.py
  train_model.py
  predictions.py
  monitor_predictions.py
```

This is the planned layout contract; task discovery is planned in slice BS-02. The template will
show both shared and dedicated scopes. Heavy external integrations may have optional dependency
extras within `bricksmith`; installing a capability will not require a separate bricksmith wheel.
See [decision 0009](docs/decisions/0009-one-library-shared-task-scopes.md).

Development uses generic behavioral requirements and independently designed examples and tests.
The [capability review](docs/planning/capability-review.md) feeds the epic without carrying private
source code, vocabulary or history into the library. See
[decision 0010](docs/decisions/0010-behavior-derived-independent-development.md).

## Layout

```
src/bricksmith/ the library and its internal modules
tests/          library tests and the leak gate
scripts/        setup and the leak gate
docs/           roadmap and decision records
```

Planned: `template/` (copier template for a new data project), `examples/` (the rendered template,
deployed by the end-to-end tests) and `plugins/bricksmith/` (the optional Claude Code plugin).
The three delivery contracts and installation/update proofs are specified in
[delivery contracts](docs/planning/delivery-contracts.md). Additional agent adapters are extensions.

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

For remote tests, configure [.env.example](.env.example) as an ignored `.env`. It can register
both a primary Free Edition profile and a supplementary corporate profile, with explicit test
destinations. Select one environment per run; the library will not load dotenv or authenticate
on import. The profile/context runner is still planned. See
[test setup and proof stages](docs/development/databricks-tests.md).

## License

[MIT](LICENSE).
