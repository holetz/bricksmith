"""Code, declaration and configuration fingerprints of a revision's scope."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from bricksmith.experiments.errors import ExperimentError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from bricksmith.graph import TaskGraph, TaskNode


def digest(value: object) -> str:
    """Return the SHA-256 of ``value`` rendered as canonical JSON."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def source_digest(root: Path | str, node: TaskNode) -> str:
    """Hash the bytes of the module that defines ``node`` (conservative: any edit to the file counts)."""
    path = Path(root) / node.path
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        msg = f"cannot read the source of {node.id} at {node.path}: {error}"
        raise ExperimentError(msg) from error


def declaration_digest(graph: TaskGraph, node: TaskNode) -> str:
    """Hash the resolved declaration of ``node``: identity, flags, outputs, metadata and its edges (no line numbers)."""
    edges = sorted(
        [e.kind, e.reference, e.target or "", e.producer or ""] for e in graph.edges if e.consumer == node.id
    )
    physical = {o.name: o.physical for o in graph.outputs}
    outputs = [[name, physical.get(name, "")] for name in node.outputs]
    return digest(
        [node.id, node.kind, node.function, node.path, outputs, node.enabled, node.static, dict(node.variant),
         dict(node.metadata), edges]
    )  # fmt: skip


def task_fingerprints(graph: TaskGraph, root: Path | str, task_ids: Iterable[str]) -> dict[str, dict[str, str]]:
    """Return ``{task: {"source": ..., "declaration": ...}}`` for ``task_ids``."""
    result: dict[str, dict[str, str]] = {}
    for task_id in sorted(task_ids):
        node = graph.task(task_id)
        result[task_id] = {"declaration": declaration_digest(graph, node), "source": source_digest(root, node)}
    return result


def configuration_digest(parameters: Mapping[str, object], isolation: Mapping[str, object], pins: object) -> str:
    """Hash the effective parameters, the isolation mapping and the resolved pins."""
    return digest({"isolation": dict(isolation), "parameters": dict(parameters), "pins": pins})
