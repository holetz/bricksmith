"""Join the task graph with the optional column registry into a plain, sorted report."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bricksmith.registry.metadata import sensitivity_coverage

if TYPE_CHECKING:
    from bricksmith.graph.model import TaskGraph
    from bricksmith.registry.model import Registry

CATALOG_VERSION = 1
DOCUMENTED = "documented"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class ColumnDoc:
    """One registry column as resolved for a table."""

    name: str
    type: str
    description: str
    sensitivity: str


@dataclass(frozen=True)
class TableDoc:
    """One logical output, its physical name, producers and registry metadata."""

    name: str
    physical: str
    scope: str
    producers: tuple[str, ...]
    status: str
    description: str = ""
    grain: tuple[str, ...] = ()
    columns: tuple[ColumnDoc, ...] = ()


@dataclass(frozen=True)
class ModelDoc:
    """A model named by explicit ``metadata.model_task.model`` and its tasks as (id, role, alias)."""

    name: str
    tasks: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class LineageDoc:
    """One read: who consumed what, from which producer."""

    consumer: str
    target: str
    producer: str
    kind: str
    provenance: str


@dataclass(frozen=True)
class CatalogReport:
    """The whole catalog; every collection is sorted and nothing is time dependent."""

    tables: tuple[TableDoc, ...]
    models: tuple[ModelDoc, ...]
    lineage: tuple[LineageDoc, ...]
    registry_loaded: bool
    registry_only: tuple[str, ...]
    classified: tuple[str, ...]
    unclassified: tuple[str, ...]
    unknown: tuple[str, ...]
    diagnostics: tuple[tuple[str, str, str, str], ...]

    @property
    def incomplete(self) -> tuple[str, ...]:
        """Return the tables lacking registry metadata."""
        return tuple(t.name for t in self.tables if t.status == INCOMPLETE)


def _models(graph: TaskGraph) -> tuple[ModelDoc, ...]:
    found: dict[str, set[tuple[str, str, str]]] = {}
    for node in graph.tasks:
        declared = node.metadata.get("model_task")
        if not isinstance(declared, dict) or not isinstance(declared.get("model"), str):
            continue
        entry = (node.id, str(declared.get("role", "")), str(declared.get("alias", "")))
        found.setdefault(declared["model"], set()).add(entry)
    return tuple(ModelDoc(name, tuple(sorted(found[name]))) for name in sorted(found))


def _table(name: str, physical: str, producers: tuple[str, ...], registry: Registry | None) -> TableDoc:
    scope = name.rpartition(".")[0]
    key = next((k for k in (name, physical) if registry is not None and k in registry.tables), None)
    if registry is None or key is None:
        return TableDoc(name, physical, scope, tuple(sorted(producers)), INCOMPLETE)
    spec = registry.tables[key]
    columns = []
    for col in sorted(spec.columns):
        resolved = registry.resolve(key, col)
        if resolved is not None:
            columns.append(ColumnDoc(col, resolved.type, resolved.description, resolved.sensitivity))
    return TableDoc(
        name, physical, scope, tuple(sorted(producers)), DOCUMENTED, spec.description, spec.grain, tuple(columns)
    )


def build_catalog(graph: TaskGraph, registry: Registry | None = None) -> CatalogReport:
    """Build the report from the graph and, when given, the registry; no I/O, no data access."""
    tables = tuple(sorted((_table(o.name, o.physical, o.producers, registry) for o in graph.outputs), key=_name))
    keys = {k for t in tables if t.status == DOCUMENTED for k in (t.name, t.physical)}
    coverage = None
    if registry is not None:
        coverage = sensitivity_coverage(registry, sorted(keys & set(registry.tables)))
    lineage = tuple(
        sorted(
            (
                LineageDoc(e.consumer, e.target or e.reference, e.producer or "", e.kind, e.provenance)
                for e in graph.edges
            ),
            key=lambda x: (x.consumer, x.target, x.producer, x.kind, x.provenance),
        )
    )
    only = tuple(sorted(set(registry.tables) - keys)) if registry is not None else ()
    return CatalogReport(
        tables=tables,
        models=_models(graph),
        lineage=lineage,
        registry_loaded=registry is not None,
        registry_only=only,
        classified=coverage.classified if coverage else (),
        unclassified=coverage.unclassified if coverage else (),
        unknown=coverage.unknown if coverage else (),
        diagnostics=tuple(sorted((d.severity, d.code, d.subject, d.message) for d in graph.diagnostics)),
    )


def _name(table: TableDoc) -> str:
    return table.name
