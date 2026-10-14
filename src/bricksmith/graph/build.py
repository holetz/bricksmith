"""Build and validate the dependency graph from discovered task definitions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from bricksmith.graph.model import Edge, Output, TaskGraph, TaskNode
from bricksmith.tasks.declaration import DATA, EXTERNAL, PLACEHOLDER, VariantValue, variant_key
from bricksmith.tasks.diagnostics import ERROR, EXEMPT, WARNING, Diagnostic
from bricksmith.tasks.discovery import Discovery, ReadSite, TaskDefinition, discover
from bricksmith.tasks.project import Exemption, Project, load_project

if TYPE_CHECKING:
    import re
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from bricksmith.context import ExecutionContext

_QUALIFIED_PARTS = 3


class _Builder:
    def __init__(self, catalogs: Mapping[str, str], schemas: Mapping[str, str]) -> None:
        self.catalogs = catalogs
        self.schemas = schemas
        self.diagnostics: list[Diagnostic] = []
        self.nodes: dict[str, TaskNode] = {}
        self.reads: dict[str, tuple[ReadSite, ...]] = {}
        self.producers: dict[str, list[str]] = defaultdict(list)
        self.edges: set[Edge] = set()

    def report(
        self, code: str, subject: str, message: str, node: TaskNode | None = None, severity: str = ERROR
    ) -> None:
        path, line = (node.path, node.line) if node else ("", 0)
        self.diagnostics.append(Diagnostic(code, severity, subject, message, path, line))

    # -- nodes and outputs ---------------------------------------------------------------------

    @staticmethod
    def _render(text: str, variant: Mapping[str, VariantValue], missing: set[str]) -> str:
        def substitute(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in variant:
                missing.add(name)
                return "{" + name + "}"
            return str(variant[name])

        return PLACEHOLDER.sub(substitute, text)

    def add(self, definition: TaskDefinition, variant: Mapping[str, VariantValue]) -> None:
        declaration = definition.declaration
        suffix = f"[{variant_key(variant)}]" if variant else ""
        task_id = definition.qualified_name + suffix
        scope = f"{definition.catalog}.{definition.schema}"
        missing: set[str] = set()
        node = TaskNode(
            id=task_id,
            catalog=definition.catalog,
            schema=definition.schema,
            name=declaration.name,
            kind=declaration.kind,
            function=definition.function,
            path=definition.path,
            line=definition.line,
            outputs=tuple(f"{scope}.{self._render(name, variant, missing)}" for name in declaration.outputs),
            variant=dict(variant),
            enabled=declaration.enabled,
            static=declaration.static,
            metadata=declaration.metadata,
        )
        if task_id in self.nodes:
            first = self.nodes[task_id]
            message = f"declared again by {node.path}:{node.line}; first declared at {first.path}:{first.line}"
            self.report("duplicate_identity", task_id, message, node)
            return
        self.nodes[task_id] = node
        self.reads[task_id] = tuple(
            replace(site, reference=self._render(site.reference, variant, missing)) for site in definition.reads
        )
        if missing:
            message = f"names use placeholders that no variant parameter supplies: {', '.join(sorted(missing))}"
            self.report("unknown_placeholder", task_id, message, node)

    def physical(self, logical: str) -> str:
        catalog, schema, name = logical.split(".")
        return f"{self.catalogs.get(catalog, catalog)}.{self.schemas.get(schema, schema)}.{name}"

    def outputs(self) -> tuple[Output, ...]:
        for task_id in sorted(self.nodes):
            for output in self.nodes[task_id].outputs:
                self.producers[output].append(task_id)
        result = [Output(name, self.physical(name), tuple(ids)) for name, ids in sorted(self.producers.items())]
        by_physical: dict[str, list[Output]] = defaultdict(list)
        for output in result:
            by_physical[output.physical.casefold()].append(output)
            if len(output.producers) > 1:
                message = f"produced by more than one task: {', '.join(output.producers)}"
                self.report("output_collision", output.name, message, self.nodes[output.producers[0]])
        for same in by_physical.values():
            if len(same) > 1:
                message = f"logical outputs {', '.join(item.name for item in same)} map to one physical object"
                self.report("physical_collision", same[0].physical, message, self.nodes[same[0].producers[0]])
        return tuple(result)

    # -- edges ---------------------------------------------------------------------------------

    def _target(self, node: TaskNode, reference: str) -> str:
        """Qualify a reference: a short name stays in the consumer's scope, never another one."""
        parts = reference.split(".")
        prefix = (node.catalog, node.schema)[: _QUALIFIED_PARTS - len(parts)]
        return ".".join((*prefix, *parts))

    def connect(self) -> None:
        for task_id in sorted(self.nodes):
            node = self.nodes[task_id]
            for site in self.reads[task_id]:
                self._connect(node, site)

    def _connect(self, node: TaskNode, site: ReadSite) -> None:
        subject = f"{node.id} -> {site.reference}"
        if site.kind == EXTERNAL:
            self.edges.add(
                Edge(node.id, site.reference, site.kind, site.provenance, site.reference, None, site.path, site.line)
            )
            if site.reference in self.producers or self._target(node, site.reference) in self.producers:
                message = "declared external but a task of this project produces it; read it as an ordinary dependency"
                self.report("external_shadows_output", subject, message, node, WARNING)
            return
        target = self._target(node, site.reference)
        producers = self.producers.get(target, [])
        if not producers:
            short = target.rsplit(".", 1)[-1]
            elsewhere = sorted(name for name in self.producers if name.rsplit(".", 1)[-1] == short)
            hint = (
                f"; the same short name exists as {', '.join(elsewhere)} (qualify the reference)" if elsewhere else ""
            )
            message = f"no task produces {target}{hint}; add the producer or declare the input as external"
            self.report("missing_producer", subject, message, node)
            self.edges.add(Edge(node.id, site.reference, site.kind, site.provenance, None, None, site.path, site.line))
            return
        if len(producers) > 1:
            message = f"{target} has several producers: {', '.join(producers)}"
            self.report("ambiguous_reference", subject, message, node)
        for producer in producers:
            self.edges.add(
                Edge(node.id, site.reference, site.kind, site.provenance, target, producer, site.path, site.line)
            )

    def cycles(self) -> None:
        graph: dict[str, set[str]] = {task_id: set() for task_id in self.nodes}
        for edge in self.edges:
            if edge.kind == DATA and edge.producer is not None:
                graph[edge.consumer].add(edge.producer)
        for members in _components(graph):
            if len(members) > 1 or members[0] in graph[members[0]]:
                message = (
                    f"ordinary reads form a cycle among: {', '.join(members)}; "
                    "an earlier window or version must be read as state"
                )
                self.report("cycle", members[0], message, self.nodes[members[0]])


def _components(graph: Mapping[str, set[str]]) -> list[list[str]]:  # noqa: C901 - one self-contained algorithm
    """Return strongly connected components (iterative Tarjan), each sorted, in stable order."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    result: list[list[str]] = []
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(sorted(graph[root])))]
        index[root] = low[root] = len(index)
        stack.append(root)
        on_stack.add(root)
        while work:
            current, successors = work[-1]
            advanced = False
            for successor in successors:
                if successor not in index:
                    index[successor] = low[successor] = len(index)
                    stack.append(successor)
                    on_stack.add(successor)
                    work.append((successor, iter(sorted(graph[successor]))))
                    advanced = True
                    break
                if successor in on_stack:
                    low[current] = min(low[current], index[successor])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[current])
            if low[current] == index[current]:
                members: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    members.append(member)
                    if member == current:
                        break
                result.append(sorted(members))
    return sorted(result)


def _exempt(diagnostics: Iterable[Diagnostic], exemptions: tuple[Exemption, ...], today: date) -> list[Diagnostic]:
    """Downgrade errors covered by a live exemption; expired exemptions always fail."""
    result: list[Diagnostic] = []
    used: set[Exemption] = set()
    for item in diagnostics:
        live = [
            e
            for e in exemptions
            if e.expires >= today and item.severity == ERROR and e.matches(item.code, item.subject)
        ]
        if live:
            used.add(live[0])
            note = f"{item.message} (exempt until {live[0].expires.isoformat()}: {live[0].reason})"
            result.append(replace(item, severity=EXEMPT, message=note))
        else:
            result.append(item)
    for exemption in exemptions:
        subject = f"{exemption.rule}:{exemption.subject}"
        if exemption.expires < today:
            message = f"exemption expired on {exemption.expires.isoformat()}; fix the finding or renew it deliberately"
            result.append(Diagnostic("expired_exemption", ERROR, subject, message))
        elif exemption not in used:
            result.append(Diagnostic("unused_exemption", WARNING, subject, "exemption matches no finding; remove it"))
    return result


def build_graph(
    discovery: Discovery,
    project: Project,
    *,
    context: ExecutionContext | None = None,
    today: date | None = None,
) -> TaskGraph:
    """Build the complete graph and validate it; findings are diagnostics, never exceptions.

    Physical names come from the project mappings, overridden by the context mappings when a
    context is given. ``today`` decides which exemptions are still live (default: current UTC date).
    """
    catalogs = {**project.catalogs, **(context.catalogs if context else {})}
    schemas = {**project.schemas, **(context.schemas if context else {})}
    builder = _Builder(catalogs, schemas)
    for definition in discovery.definitions:
        for variant in definition.declaration.variants or ({},):
            builder.add(definition, variant)
    outputs = builder.outputs()
    builder.connect()
    builder.cycles()
    found = sorted({*discovery.diagnostics, *builder.diagnostics})
    current = today or datetime.now(tz=UTC).date()
    return TaskGraph(
        tasks=tuple(builder.nodes[task_id] for task_id in sorted(builder.nodes)),
        outputs=outputs,
        edges=tuple(sorted(builder.edges, key=Edge.sort_key)),
        diagnostics=tuple(sorted(_exempt(found, project.exemptions, current))),
    )


def load_graph(
    root: Path | str = ".", *, context: ExecutionContext | None = None, today: date | None = None
) -> TaskGraph:
    """Load the project file under ``root``, discover its tasks offline and build the graph."""
    project = load_project(root)
    return build_graph(discover(project), project, context=context, today=today)
