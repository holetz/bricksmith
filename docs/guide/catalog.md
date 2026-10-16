# Offline catalog

`bricksmith catalog render` documents a project's tables, models and lineage without Spark, a workspace or any data scan. It reads the task graph (discovered statically, so task modules are never imported or executed) and, optionally, the [column registry](registry.md).

```bash
bricksmith catalog render --project . --registry registry/columns.toml            # Markdown to stdout
bricksmith catalog render --project . --format json --output catalog.json         # JSON to a file
```

Options: `--project` (default `.`), `--registry` (optional), `--format markdown|json` (default `markdown`; the global `--json` also selects JSON) and `--output PATH`.

## What it reports

- **Tables**: each logical output with its physical name, scope, producing tasks and, when the registry knows it, description, declared grain and resolved columns (type, description, sensitivity).
- **Models**: grouped by the explicit `metadata.model_task.model` of a task, with each task's id, role and alias. A model is never inferred from an output name, and no other metadata is dumped.
- **Lineage**: every read with its kind (`data`, `external`, `temporal`), provenance and producer.
- **Sensitivity coverage**: columns that are classified, unclassified or unknown (used but undefined).
- **Diagnostics** of the graph, and registry tables that no task produces.

## Incomplete metadata

Without `--registry`, or for a table the registry does not describe, the table is reported with status `incomplete`; nothing is guessed. An explicitly given registry that cannot be read fails with a registry error instead of being ignored.

## Determinism

Every collection is sorted and nothing is time dependent, so identical inputs produce byte-identical output regardless of discovery order.

## Library use

```python
from bricksmith.catalogdocs import build_catalog, to_json, to_markdown
from bricksmith.graph import load_graph
from bricksmith.registry import load_registry

report = build_catalog(load_graph("."), load_registry("registry/columns.toml"))
print(to_markdown(report))
```

## Scope

Live enrichment from a workspace is out of scope: this catalog is derived from project files only.
