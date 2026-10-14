"""The retained dependency graph: plain, serializable records with stable ordering."""

from __future__ import annotations

import heapq
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.errors import ValidationError
from bricksmith.tasks.declaration import DATA, VariantValue
from bricksmith.tasks.diagnostics import ERROR, Diagnostic

if TYPE_CHECKING:
    from collections.abc import Mapping

GRAPH_VERSION = 1


@dataclass(frozen=True)
class TaskNode:
    """One runnable task: a declaration expanded for one parameter variant.

    ``id`` is ``catalog.schema.name`` plus ``[param=value,...]`` for a variant. ``outputs`` are
    qualified logical names; physical names live on :class:`Output`.
    """

    id: str
    catalog: str
    schema: str
    name: str
    kind: str
    function: str
    path: str
    line: int
    outputs: tuple[str, ...] = ()
    variant: Mapping[str, VariantValue] = field(default_factory=dict)
    enabled: bool = True
    static: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def scope(self) -> str:
        """Return the logical scope, ``catalog.schema``."""
        return f"{self.catalog}.{self.schema}"

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "catalog": self.catalog,
            "enabled": self.enabled,
            "function": self.function,
            "id": self.id,
            "kind": self.kind,
            "line": self.line,
            "metadata": dict(self.metadata),
            "name": self.name,
            "outputs": list(self.outputs),
            "path": self.path,
            "schema": self.schema,
            "static": self.static,
            "variant": dict(self.variant),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskNode:
        """Rebuild a node from :meth:`to_dict` output."""
        return cls(
            id=data["id"],
            catalog=data["catalog"],
            schema=data["schema"],
            name=data["name"],
            kind=data["kind"],
            function=data["function"],
            path=data["path"],
            line=int(data["line"]),
            outputs=tuple(data["outputs"]),
            variant=dict(data["variant"]),
            enabled=bool(data["enabled"]),
            static=bool(data["static"]),
            metadata=dict(data["metadata"]),
        )


@dataclass(frozen=True)
class Output:
    """A logical output, the physical name the mapping gives it, and the tasks that produce it."""

    name: str
    physical: str
    producers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"name": self.name, "physical": self.physical, "producers": list(self.producers)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Output:
        """Rebuild an output from :meth:`to_dict` output."""
        return cls(name=data["name"], physical=data["physical"], producers=tuple(data["producers"]))


@dataclass(frozen=True)
class Edge:
    """One read of ``consumer``.

    ``kind`` is ``data``, ``external`` or ``temporal``; ``provenance`` is ``declared`` or
    ``inferred``. ``target`` is the resolved logical name (or the external resource) and
    ``producer`` the task that owns it; both are ``None`` when the reference did not resolve.
    """

    consumer: str
    reference: str
    kind: str
    provenance: str
    target: str | None
    producer: str | None
    path: str
    line: int

    def sort_key(self) -> tuple[str, str, str, str, str, str, int]:
        """Return a total ordering key that tolerates unresolved edges."""
        return (self.consumer, self.target or "", self.producer or "", self.kind, self.provenance, self.path, self.line)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "consumer": self.consumer,
            "kind": self.kind,
            "line": self.line,
            "path": self.path,
            "producer": self.producer,
            "provenance": self.provenance,
            "reference": self.reference,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Edge:
        """Rebuild an edge from :meth:`to_dict` output."""
        return cls(
            consumer=data["consumer"],
            reference=data["reference"],
            kind=data["kind"],
            provenance=data["provenance"],
            target=data["target"],
            producer=data["producer"],
            path=data["path"],
            line=int(data["line"]),
        )


@dataclass(frozen=True)
class TaskGraph:
    """The complete graph: every task, output, read and diagnostic, valid or not."""

    tasks: tuple[TaskNode, ...] = ()
    outputs: tuple[Output, ...] = ()
    edges: tuple[Edge, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        """Return the diagnostics that make the graph invalid."""
        return tuple(item for item in self.diagnostics if item.severity == ERROR)

    @property
    def ok(self) -> bool:
        """Tell whether the graph has no error."""
        return not self.errors

    def task(self, task_id: str) -> TaskNode:
        """Return the task with this identity."""
        for node in self.tasks:
            if node.id == task_id:
                return node
        msg = f"no task {task_id!r} in the graph"
        raise ValidationError(msg)

    def dependencies(self, task_id: str, *, kind: str = DATA) -> tuple[str, ...]:
        """Return the producers ``task_id`` reads through edges of ``kind``, sorted."""
        found = {e.producer for e in self.edges if e.consumer == task_id and e.kind == kind and e.producer}
        return tuple(sorted(found))

    def order(self) -> tuple[str, ...]:
        """Return task identities in a deterministic topological order over ``data`` edges.

        Temporal edges read an earlier window or version, so they never constrain the order.
        """
        waiting = {node.id: set(self.dependencies(node.id)) for node in self.tasks}
        ready = [task_id for task_id, needs in waiting.items() if not needs]
        heapq.heapify(ready)
        ordered: list[str] = []
        while ready:
            current = heapq.heappop(ready)
            ordered.append(current)
            del waiting[current]
            for task_id, needs in waiting.items():
                if current in needs:
                    needs.discard(current)
                    if not needs:
                        heapq.heappush(ready, task_id)
        if waiting:
            msg = f"the graph has a dependency cycle among: {', '.join(sorted(waiting))}"
            raise ValidationError(msg)
        return tuple(ordered)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form; lists are already in stable order."""
        return {
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "edges": [item.to_dict() for item in self.edges],
            "outputs": [item.to_dict() for item in self.outputs],
            "tasks": [item.to_dict() for item in self.tasks],
            "version": GRAPH_VERSION,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskGraph:
        """Rebuild a graph from :meth:`to_dict` output."""
        if data.get("version") != GRAPH_VERSION:
            msg = f"unsupported graph version {data.get('version')!r}; expected {GRAPH_VERSION}"
            raise ValidationError(msg)
        return cls(
            tasks=tuple(TaskNode.from_dict(item) for item in data["tasks"]),
            outputs=tuple(Output.from_dict(item) for item in data["outputs"]),
            edges=tuple(Edge.from_dict(item) for item in data["edges"]),
            diagnostics=tuple(Diagnostic.from_dict(item) for item in data["diagnostics"]),
        )

    def to_json(self) -> str:
        """Return sorted, indented JSON that is identical for identical graphs."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> TaskGraph:
        """Rebuild a graph from :meth:`to_json` output."""
        return cls.from_dict(json.loads(text))
