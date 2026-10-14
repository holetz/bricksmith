"""Promotion and retirement plans from immutable evidence, with an impact report and separated retention."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bricksmith.errors import AuthorizationError
from bricksmith.experiments.errors import ExperimentError, IsolationError
from bricksmith.experiments.run import replan, report_name
from bricksmith.graph import load_graph
from bricksmith.tasks.declaration import DATA

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from pathlib import Path

    from bricksmith.context import ExecutionContext
    from bricksmith.experiments.evidence import Evidence
    from bricksmith.experiments.store import ExperimentStore
    from bricksmith.quality import RegressionReport


@dataclass(frozen=True)
class Change:
    """One changed dependency in an impact report."""

    kind: str
    subject: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        """Return the serializable form."""
        return {"detail": self.detail, "kind": self.kind, "subject": self.subject}


@dataclass(frozen=True)
class PromotionStep:
    """Replace one baseline object with its validated candidate."""

    baseline: str
    candidate: str
    protected: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"baseline": self.baseline, "candidate": self.candidate, "protected": self.protected}


@dataclass(frozen=True)
class PromotionPlan:
    """What promoting a revision would change; applying needs an explicit authorization per baseline object."""

    name: str
    evidence: str
    steps: tuple[PromotionStep, ...] = ()
    impact: tuple[Change, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "evidence": self.evidence,
            "impact": [c.to_dict() for c in self.impact],
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
        }

    def to_text(self) -> str:
        """Return a short human-readable review."""
        lines = [f"promotion of {self.name} (evidence {self.evidence[:12]})"]
        lines += [f"replace   {s.baseline} <- {s.candidate}{' (protected)' if s.protected else ''}" for s in self.steps]
        lines += [f"impact    {c.kind:9} {c.subject}  {c.detail}" for c in self.impact]
        return "\n".join(lines)


def _downstream(root: Path | str, context: ExecutionContext, seeds: Collection[str]) -> list[str]:
    graph = load_graph(root, context=context)
    seen: set[str] = set(seeds)
    stack = list(seeds)
    while stack:
        current = stack.pop()
        for edge in graph.edges:
            if edge.kind == DATA and edge.producer == current and edge.consumer not in seen:
                seen.add(edge.consumer)
                stack.append(edge.consumer)
    return sorted(seen - set(seeds))


def plan_promotion(
    evidence: Evidence,
    report: RegressionReport,
    store: ExperimentStore,
    *,
    project_root: Path | str,
    context: ExecutionContext,
) -> PromotionPlan:
    """Plan promotion from evidence whose sources are unchanged and whose comparison gate passed."""
    if report.name != report_name(evidence):
        msg = "the regression report belongs to another revision or another evidence record"
        raise ExperimentError(msg)
    if report.verdict != "pass":
        msg = f"the regression gate did not pass ({report.verdict}); promotion refused"
        raise ExperimentError(msg)
    plan = replan(evidence, project_root, context, store)
    missing = sorted(iso for iso in evidence.targets.values() if not store.exists(iso))
    if missing:
        msg = f"candidate outputs do not exist: {', '.join(missing)}"
        raise ExperimentError(msg)
    protected = {
        t.physical for e in (plan.runner_plan.entries if plan.runner_plan else ()) for t in e.targets if t.protected
    }
    steps = tuple(PromotionStep(b, i, i in protected) for b, i in sorted(evidence.targets.items()))
    changes: list[Change] = []
    changes += [Change("task", t, f"rebuilt; source {plan.tasks[t]['source'][:12]}") for t in plan.rebuilt]
    changes += [Change("output", b, f"replaced by {i}") for b, i in sorted(evidence.targets.items())]
    changes += [
        Change("consumer", c, "reads a promoted output; not rebuilt by the revision")
        for c in _downstream(project_root, context, plan.rebuilt)
    ]
    for key, value in sorted(plan.revision.parameters.items()):
        before = context.parameters.get(key)
        if before != value:
            changes.append(Change("parameter", key, f"{before!r} -> {value!r}"))
    for item in plan.inputs:
        detail = f"frozen at version {item.version}" if item.mode == "frozen" else "read live (unpinned)"
        changes.append(Change("input", item.physical, detail))
    changes += [Change("seed", i, f"history copied from {b}") for b, i, _ in plan.seeds]
    return PromotionPlan(evidence.name, evidence.fingerprint, steps, tuple(changes))


def apply_promotion(plan: PromotionPlan, store: ExperimentStore, *, authorized: Collection[str]) -> tuple[str, ...]:
    """Replace each baseline object with its candidate; every baseline object needs its own explicit authorization."""
    missing = sorted(s.baseline for s in plan.steps if s.baseline not in authorized)
    if missing:
        msg = f"promotion needs an explicit authorization for: {', '.join(missing)}"
        raise AuthorizationError(msg)
    for step in plan.steps:
        store.copy(step.candidate, step.baseline)
    return tuple(s.baseline for s in plan.steps)


@dataclass(frozen=True)
class RetirementPlan:
    """What retiring a revision removes (experimental outputs only) and what it explicitly keeps."""

    name: str
    drop_tables: tuple[str, ...] = ()
    drop_schemas: tuple[str, ...] = ()
    retained: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "drop_schemas": list(self.drop_schemas),
            "drop_tables": list(self.drop_tables),
            "name": self.name,
            "retained": {k: list(v) for k, v in sorted(self.retained.items())},
        }

    def to_text(self) -> str:
        """Return a short human-readable review."""
        lines = [f"retirement of {self.name}"]
        lines += [f"drop      {t}" for t in self.drop_tables]
        lines += [f"drop      schema {s} (only when empty)" for s in self.drop_schemas]
        lines += [f"keep      {k}: {', '.join(v)}" for k, v in sorted(self.retained.items()) if v]
        return "\n".join(lines)


def plan_retirement(evidence: Evidence, store: ExperimentStore | None = None) -> RetirementPlan:
    """Plan removal of the revision's experimental outputs; sources, models and the evidence are retained."""
    data = evidence.data
    baseline = set(evidence.targets) | {i["physical"] for i in data["inputs"]}
    isolated = sorted(set(evidence.targets.values()))
    if baseline & set(isolated):
        msg = "evidence maps an isolated object onto a baseline object; retirement refused"
        raise IsolationError(msg)
    tables = tuple(t for t in isolated if store is None or store.exists(t))
    schemas = tuple(sorted({".".join(t.split(".")[:2]) for t in isolated}))
    retained = {
        "baseline outputs": tuple(sorted(evidence.targets)),
        "source data": tuple(sorted(i["physical"] for i in data["inputs"])),
        "model artifacts": tuple(evidence.revision.retain),
    }
    return RetirementPlan(evidence.name, tables, schemas, retained)


def apply_retirement(plan: RetirementPlan, evidence: Evidence, store: ExperimentStore) -> tuple[list[str], list[str]]:
    """Drop the planned experimental tables, then empty schemas; return ``(dropped, left behind)``."""
    allowed = set(evidence.targets.values())
    unknown = sorted(set(plan.drop_tables) - allowed)
    if unknown:
        msg = f"refusing to drop objects the evidence does not own: {', '.join(unknown)}"
        raise IsolationError(msg)
    dropped: list[str] = []
    left: list[str] = []
    for table in plan.drop_tables:
        store.drop(table)
        dropped.append(table)
    for schema in plan.drop_schemas:
        catalog, name = schema.split(".")
        try:
            store.drop_schema(catalog, name)
            dropped.append(schema)
        except Exception as error:  # noqa: BLE001 - a non-empty or missing schema is reported, never forced
            left.append(f"{schema}: {error}")
    return dropped, left
