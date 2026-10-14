"""Offline project scan: which tasks build which model's datasets, and whether the graph shows every input."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.graph import load_graph
from bricksmith.mlops.errors import DatasetError
from bricksmith.mlops.spec import ModelSpec, parse_declaration
from bricksmith.mlops.validate import Finding

if TYPE_CHECKING:
    from bricksmith.graph import TaskGraph

REQUIRED_UPSTREAM = {
    "training": ("population", "features", "labels"),
    "prediction": ("population", "features"),
}


@dataclass(frozen=True)
class DatasetTask:
    """A task declaring ``model_dataset`` metadata. The model id comes from the declaration only."""

    task_id: str
    model: str
    role: str
    scope: str
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class ProjectScan:
    """Model specs, dataset tasks and findings of a project."""

    specs: dict[str, ModelSpec]
    tasks: tuple[DatasetTask, ...]
    findings: tuple[Finding, ...]

    @property
    def ok(self) -> bool:
        """Whether there is no finding."""
        return not self.findings

    def to_dict(self) -> dict[str, Any]:
        """Return the plain, sorted form."""
        models = {
            model: {
                "config": spec.config_hash(),
                "features": [f.name for f in spec.features],
                "tasks": {
                    t.role: {"outputs": list(t.outputs), "task": t.task_id} for t in self.tasks if t.model == model
                },
            }
            for model, spec in sorted(self.specs.items())
        }
        return {"findings": [f.to_dict() for f in self.findings], "models": models, "ok": self.ok}


def _upstream(graph: TaskGraph, task_id: str) -> set[str]:
    seen: set[str] = set()
    todo = [task_id]
    while todo:
        for dep in graph.dependencies(todo.pop()):
            if dep not in seen:
                seen.add(dep)
                todo.append(dep)
    return seen


def scan_project(root: Path | str) -> ProjectScan:
    """Discover dataset tasks and validate identity, specs and graph-visible dependencies without any remote call."""
    base = Path(root)
    graph = load_graph(base)
    findings = [Finding(f"graph_{d.code}", d.message) for d in graph.errors]
    specs: dict[str, ModelSpec] = {}
    tasks: list[DatasetTask] = []
    for node in graph.tasks:
        try:
            declaration = parse_declaration(node.metadata)
            if declaration is None:
                continue
            spec = declaration.resolve(base)
        except DatasetError as error:
            findings.append(Finding("dataset_declaration", f"{node.id}: {error.message}", 1, (node.id,)))
            continue
        known = specs.setdefault(spec.model, spec)
        if known.config_hash() != spec.config_hash():
            findings.append(
                Finding("conflicting_spec", f"{node.id}: model {spec.model!r} has two different specs", 1, (node.id,))
            )
        tasks.append(DatasetTask(node.id, spec.model, declaration.role, node.scope, node.outputs))
    seen: dict[tuple[str, str], str] = {}
    for item in tasks:
        other = seen.setdefault((item.model, item.role), item.task_id)
        if other != item.task_id:
            findings.append(
                Finding("duplicate_role", f"model {item.model!r} has two {item.role} tasks", 2, (other, item.task_id))
            )
    by_id = {t.task_id: t for t in tasks}
    for item in tasks:
        upstream = _upstream(graph, item.task_id)
        have = {by_id[u].role for u in upstream if u in by_id and by_id[u].model == item.model}
        missing = [r for r in REQUIRED_UPSTREAM.get(item.role, ()) if r not in have]
        if missing:
            text = f"{item.task_id} ({item.model} {item.role}) does not read the {', '.join(missing)} task in the graph"
            findings.append(Finding("missing_dependency", text, len(missing), (item.task_id,)))
    return ProjectScan(specs, tuple(tasks), tuple(findings))
