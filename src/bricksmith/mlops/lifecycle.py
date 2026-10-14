"""Review, promotion, rollback, change propagation and retirement of registered models.

Everything mutating is a plan first (``to_dict``/``to_text``), then applied against the current registry state.
Consumers come from the task graph (``model_task`` metadata) and from the aliases; downstream impact follows
the graph's data edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bricksmith.errors import StalePlanError
from bricksmith.mlops.errors import ModelError, ModelInUseError, ModelNotFoundError
from bricksmith.mlops.store import RETIRED, TAG_STATE, ModelStore, check_selector
from bricksmith.mlops.tasks import MODEL_TASK_KEY, parse_model_task

if TYPE_CHECKING:
    from collections.abc import Collection

    from bricksmith.graph import TaskGraph

CONSUMER_ROLES = ("evaluate", "predict")


@dataclass(frozen=True)
class ModelUse:
    """One task that evaluates or predicts with a model, and how it selects the version."""

    task_id: str
    role: str
    model: str
    alias: str | None
    version: int | None

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {
            "alias": self.alias,
            "model": self.model,
            "role": self.role,
            "task": self.task_id,
            "version": self.version,
        }


def model_uses(graph: TaskGraph, model: str | None = None) -> list[ModelUse]:
    """Return the evaluate and predict tasks declaring a ``model_task``, optionally for one identity."""
    uses = []
    for node in graph.tasks:
        if MODEL_TASK_KEY not in node.metadata:
            continue
        declared = parse_model_task(node.metadata)
        if declared is None or declared.role not in CONSUMER_ROLES or (model and declared.model != model):
            continue
        uses.append(ModelUse(node.id, declared.role, declared.model, declared.alias, declared.version))
    return sorted(uses, key=lambda u: u.task_id)


def downstream(graph: TaskGraph, seeds: Collection[str]) -> list[str]:
    """Return every task that transitively reads outputs of ``seeds`` (the seeds themselves excluded)."""
    readers: dict[str, set[str]] = {}
    for edge in graph.edges:
        if edge.producer:
            readers.setdefault(edge.producer, set()).add(edge.consumer)
    seen: set[str] = set()
    todo = list(seeds)
    while todo:
        for nxt in readers.get(todo.pop(), ()):
            if nxt not in seen and nxt not in seeds:
                seen.add(nxt)
                todo.append(nxt)
    return sorted(seen)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Impact:
    """Tasks affected by a change of alias targets: alias-bound consumers, pinned ones (unaffected), downstream."""

    alias_bound: tuple[str, ...] = ()
    pinned: tuple[str, ...] = ()
    downstream: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, list[str]]:
        """Return the plain form."""
        return {"alias_bound": list(self.alias_bound), "downstream": list(self.downstream), "pinned": list(self.pinned)}


def alias_impact(graph: TaskGraph | None, model: str, alias: str) -> Impact:
    """Return which tasks follow ``alias`` (their results change), which are pinned, and what reads their outputs."""
    if graph is None:
        return Impact()
    uses = model_uses(graph, model)
    bound = tuple(u.task_id for u in uses if u.alias == alias)
    pinned = tuple(u.task_id for u in uses if u.alias is None)
    return Impact(bound, pinned, tuple(downstream(graph, bound)))


@dataclass(frozen=True)
class AliasPlan:
    """A reviewed alias move (promotion or rollback) with its evidence and impact."""

    action: str
    name: str
    model: str
    alias: str
    from_version: int | None
    to_version: int
    reviewer: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    impact: Impact = field(default_factory=Impact)
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {
            "action": self.action,
            "alias": self.alias,
            "evidence": self.evidence,
            "from_version": self.from_version,
            "impact": self.impact.to_dict(),
            "model": self.model,
            "name": self.name,
            "reason": self.reason,
            "reviewer": self.reviewer,
            "to_version": self.to_version,
            "warnings": list(self.warnings),
        }

    def to_text(self) -> str:
        """Return a short human-readable review."""
        lines = [f"{self.action} {self.name}: alias {self.alias} v{self.from_version} -> v{self.to_version}"]
        lines += [f"reviewer {self.reviewer}: {self.reason}"]
        lines += [f"evidence {k}: {v}" for k, v in sorted(self.evidence.items())]
        lines += [f"follows alias: {t}" for t in self.impact.alias_bound]
        lines += [f"pinned (unchanged): {t}" for t in self.impact.pinned]
        lines += [f"reads their outputs: {t}" for t in self.impact.downstream]
        lines += [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines)


def _evidence(store: ModelStore, version: int, run_id: str) -> tuple[dict[str, Any], list[str]]:
    run = store.client.get_run(run_id)
    metrics = {k: round(float(v), 6) for k, v in sorted(dict(run.data.metrics or {}).items())}
    tags = dict(run.data.tags or {})
    evidence = {
        "code_revision": tags.get("bricksmith_code_revision", "unknown"),
        "metrics": metrics,
        "run_id": run_id,
        "spec_config": tags.get("bricksmith_spec_config", ""),
        "version": version,
    }
    warnings = [] if any(k.startswith("evaluation_") for k in metrics) else ["the run has no evaluation metrics"]
    return evidence, warnings


def _review(reviewer: str, reason: str) -> None:
    if not reviewer.strip() or not reason.strip():
        msg = "a review needs a reviewer and a reason"
        raise ModelError(msg)


def plan_promotion(  # noqa: PLR0913 - explicit review inputs
    store: ModelStore,
    name: str,
    model: str,
    alias: str,
    version: int,
    *,
    reviewer: str,
    reason: str,
    graph: TaskGraph | None = None,
) -> AliasPlan:
    """Plan pointing ``alias`` at ``version`` (an active version of identity ``model``) with recorded evidence."""
    _review(reviewer, reason)
    check_selector(alias, None)
    target = store.resolve(name, model, version=version)
    evidence, warnings = _evidence(store, target.version, target.run_id)
    current = store.aliases(name).get(alias)
    if current == target.version:
        warnings.append(f"alias {alias} already points to v{current}")
    return AliasPlan(
        "promote",
        name,
        model,
        alias,
        current,
        target.version,
        reviewer,
        reason,
        evidence,
        alias_impact(graph, model, alias),
        tuple(warnings),
    )


def plan_rollback(  # noqa: PLR0913 - explicit review inputs
    store: ModelStore,
    name: str,
    model: str,
    alias: str,
    *,
    reviewer: str,
    reason: str,
    graph: TaskGraph | None = None,
) -> AliasPlan:
    """Plan returning ``alias`` to the version it pointed to before its last recorded change."""
    _review(reviewer, reason)
    current = store.aliases(name).get(alias)
    if current is None:
        msg = f"alias {alias!r} is not set on {name}; nothing to roll back"
        raise ModelNotFoundError(msg)
    previous = next(
        (e["from"] for e in reversed(store.history(name, alias)) if e.get("to") == current and e.get("from")), None
    )
    if previous is None:
        msg = f"no earlier version is recorded for alias {alias!r} on {name}"
        raise ModelNotFoundError(msg)
    target = store.resolve(name, model, version=int(previous))
    evidence, warnings = _evidence(store, target.version, target.run_id)
    return AliasPlan(
        "rollback",
        name,
        model,
        alias,
        current,
        target.version,
        reviewer,
        reason,
        evidence,
        alias_impact(graph, model, alias),
        tuple(warnings),
    )


def apply_alias_plan(store: ModelStore, plan: AliasPlan) -> None:
    """Move the alias after re-checking that it still points where the plan was made; record the review events."""
    current = store.aliases(plan.name).get(plan.alias)
    if current != plan.from_version:
        msg = f"alias {plan.alias} on {plan.name} moved to v{current} since the plan; plan again"
        raise StalePlanError(msg)
    store.resolve(plan.name, plan.model, version=plan.to_version)
    store.set_alias(plan.name, plan.alias, plan.to_version)
    event = {
        "action": plan.action,
        "alias": plan.alias,
        "at": _now(),
        "from": plan.from_version,
        "reason": plan.reason,
        "reviewer": plan.reviewer,
        "to": plan.to_version,
    }
    store.record_event(plan.name, plan.to_version, event)
    store.record_history(plan.name, plan.alias, event)


@dataclass(frozen=True)
class RetirementPlan:
    """What retiring versions removes (aliases), what blocks it (consumers) and what is protected."""

    name: str
    model: str
    versions: tuple[int, ...]
    remove_aliases: dict[str, int]
    consumers: tuple[ModelUse, ...] = ()
    downstream: tuple[str, ...] = ()
    retained: dict[str, list[str]] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        """Whether active consumers prevent retirement."""
        return bool(self.consumers)

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {
            "blocked": self.blocked,
            "consumers": [c.to_dict() for c in self.consumers],
            "downstream": list(self.downstream),
            "model": self.model,
            "name": self.name,
            "remove_aliases": dict(sorted(self.remove_aliases.items())),
            "retained": {k: list(v) for k, v in sorted(self.retained.items())},
            "versions": list(self.versions),
        }

    def to_text(self) -> str:
        """Return a short human-readable review."""
        lines = [f"retire {self.name}: versions {', '.join(f'v{v}' for v in self.versions) or 'none'}"]
        lines += [f"remove alias {a} (v{v})" for a, v in sorted(self.remove_aliases.items())]
        lines += [f"BLOCKED by active consumer {c.task_id} ({c.role})" for c in self.consumers]
        lines += [f"keep      {k}: {', '.join(v)}" for k, v in sorted(self.retained.items()) if v]
        return "\n".join(lines)


def plan_retirement(
    store: ModelStore,
    name: str,
    model: str,
    *,
    graph: TaskGraph | None = None,
    versions: Collection[int] | None = None,
) -> RetirementPlan:
    """Plan retiring ``versions`` (default: all active) of ``model``; training artifacts are always retained."""
    if store.registered(name) is None:
        msg = f"registered model {name} does not exist"
        raise ModelNotFoundError(msg)
    owned = [v for v in store.versions(name) if v.model == model and v.state != RETIRED]
    chosen = sorted(set(versions) if versions is not None else {v.version for v in owned})
    unknown = sorted(set(chosen) - {v.version for v in owned})
    if unknown:
        msg = f"versions {unknown} are not active versions of {model} on {name}"
        raise ModelNotFoundError(msg)
    aliases = store.aliases(name)
    remove = {a: v for a, v in aliases.items() if v in chosen}
    consumers = []
    for use in model_uses(graph, model) if graph is not None else []:
        target = use.version if use.version is not None else aliases.get(use.alias or "")
        if target in chosen:
            consumers.append(use)
    after = downstream(graph, [c.task_id for c in consumers]) if graph is not None else []
    kept = {v.version: v for v in owned if v.version in chosen}
    retained = {
        "model_versions": [f"v{v}" for v in chosen],
        "training_runs": sorted({kept[v].run_id for v in chosen}),
        "training_artifacts": [f"runs:/{kept[v].run_id}/model" for v in chosen],
    }
    return RetirementPlan(name, model, tuple(chosen), remove, tuple(consumers), tuple(after), retained)


def apply_retirement(store: ModelStore, plan: RetirementPlan) -> None:
    """Remove the aliases and mark the versions retired; refused while any consumer is active."""
    if plan.blocked:
        names = ", ".join(c.task_id for c in plan.consumers)
        msg = f"retirement of {plan.name} refused: active consumers {names}"
        raise ModelInUseError(msg)
    now = store.aliases(plan.name)
    for alias, version in plan.remove_aliases.items():
        if now.get(alias) == version:
            store.remove_alias(plan.name, alias)
    for version in plan.versions:
        store.tag_version(plan.name, version, TAG_STATE, RETIRED)
        store.record_event(plan.name, version, {"action": "retire", "at": _now()})
