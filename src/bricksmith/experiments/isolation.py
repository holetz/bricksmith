"""Dependency-aware isolation plan: boundary, live versus frozen inputs, destination mapping and collisions."""

from __future__ import annotations

import dataclasses
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bricksmith.experiments.fingerprint import configuration_digest, task_fingerprints
from bricksmith.graph import load_graph
from bricksmith.plan import Issue, Plan, ReadBinding, build_plan
from bricksmith.plan.model import ERROR, RUN, WARNING
from bricksmith.tasks.declaration import DATA, TEMPORAL

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from bricksmith.context import ExecutionContext
    from bricksmith.experiments.revision import Revision
    from bricksmith.experiments.store import ExperimentStore
    from bricksmith.graph import TaskGraph

INFO = "info"
FROZEN = "frozen"
LIVE = "live"
CONTENT_VERSION = 1


@dataclass(frozen=True)
class InputRecord:
    """One input read from outside the boundary: frozen at a Delta version, or read live (unpinned)."""

    physical: str
    kinds: tuple[str, ...]
    consumers: tuple[str, ...]
    mode: str = LIVE
    version: int | None = None
    requested: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "consumers": list(self.consumers),
            "kinds": list(self.kinds),
            "mode": self.mode,
            "physical": self.physical,
            "requested": self.requested,
            "version": self.version,
        }


@dataclass(frozen=True)
class IsolationPlan:
    """What a revision rebuilds, what it reads, where it writes and what stops it; nothing is created by planning."""

    revision: Revision
    rebuilt: tuple[str, ...] = ()
    outside_consumers: tuple[str, ...] = ()
    inputs: tuple[InputRecord, ...] = ()
    targets: Mapping[str, str] = field(default_factory=dict)
    schemas: Mapping[str, str] = field(default_factory=dict)
    seeds: tuple[tuple[str, str, int | None], ...] = ()
    parameters: Mapping[str, object] = field(default_factory=dict)
    tasks: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    configuration: str = ""
    issues: tuple[Issue, ...] = ()
    runner_plan: Plan | None = field(default=None, compare=False, repr=False)

    @property
    def errors(self) -> tuple[Issue, ...]:
        """Return the findings that forbid creating anything."""
        return tuple(i for i in self.issues if i.severity == ERROR)

    @property
    def ok(self) -> bool:
        """Whether the plan can be frozen and run."""
        return not self.errors and self.runner_plan is not None

    @property
    def unpinned(self) -> tuple[str, ...]:
        """Return the inputs that are read live, recorded as such."""
        return tuple(i.physical for i in self.inputs if i.mode == LIVE)

    @property
    def isolated_schemas(self) -> tuple[tuple[str, str], ...]:
        """Return the ``(catalog, schema)`` pairs of the destination."""
        pairs = {tuple(t.split(".")[:2]) for t in self.targets.values()}
        return tuple(sorted((c, s) for c, s in pairs))

    def pins(self) -> dict[str, int]:
        """Return ``{baseline physical: version}`` for the frozen inputs."""
        return {i.physical: i.version for i in self.inputs if i.mode == FROZEN and i.version is not None}

    def content(self) -> dict[str, object]:
        """Return the deterministic description frozen into evidence (no issues, no timestamps)."""
        return {
            "configuration": self.configuration,
            "content_version": CONTENT_VERSION,
            "inputs": [i.to_dict() for i in self.inputs],
            "outside_consumers": list(self.outside_consumers),
            "parameters": dict(self.parameters),
            "rebuilt": list(self.rebuilt),
            "revision": self.revision.to_dict(),
            "schemas": dict(self.schemas),
            "seeds": [[b, i, v] for b, i, v in self.seeds],
            "targets": dict(sorted(self.targets.items())),
            "tasks": {k: dict(v) for k, v in sorted(self.tasks.items())},
        }

    def to_dict(self) -> dict[str, object]:
        """Return the serializable review form: content plus findings."""
        return {**self.content(), "issues": [i.to_dict() for i in self.issues], "ok": self.ok}

    def to_text(self) -> str:
        """Return a short human-readable review."""
        lines = [f"revision {self.revision.name}: {'ready' if self.ok else 'BLOCKED'}"]
        lines += [f"rebuild   {t}" for t in self.rebuilt]
        lines += [f"consumer  {t}  (outside the boundary, not rebuilt)" for t in self.outside_consumers]
        for item in self.inputs:
            tail = f"frozen at version {item.version}" if item.mode == FROZEN else "LIVE (unpinned)"
            lines.append(f"input     {item.physical}  {tail}")
        lines += [f"write     {base} -> {iso}" for base, iso in sorted(self.targets.items())]
        lines += [
            f"seed      {base} -> {iso}  version {v if v is not None else 'current'}" for base, iso, v in self.seeds
        ]
        lines += [f"{i.severity}[{i.code}]: {i.message}" for i in self.issues]
        return "\n".join(lines)


def _parts(physical: str) -> tuple[str, str]:
    catalog, schema, *_ = physical.split(".")
    return catalog, schema


def _early(revision: Revision, issues: list[Issue]) -> IsolationPlan:
    return IsolationPlan(revision, issues=tuple(issues))


def plan_isolation(
    root: Path | str,
    revision: Revision,
    *,
    context: ExecutionContext,
    store: ExperimentStore | None = None,
    resolved: Mapping[str, int] | None = None,
) -> IsolationPlan:
    """Plan the isolated rebuild of ``revision`` over the project at ``root``; reads metadata, creates nothing.

    ``store`` resolves timestamp/current pins and checks existing objects; ``resolved`` supplies the versions
    already frozen in evidence (they win over the revision's own pins, which makes a re-plan reproducible).
    """
    baseline = load_graph(root, context=context)
    if not baseline.ok:
        codes = ", ".join(sorted({d.code for d in baseline.errors}))
        return _early(revision, [Issue("invalid_graph", ERROR, f"the baseline graph has errors ({codes})")])
    seen = build_plan(baseline, roots=revision.roots, upstream=False, downstream=revision.downstream, partial=True)
    issues: list[Issue] = [i for i in seen.issues if i.severity == ERROR]
    rebuilt = tuple(e.task_id for e in seen.entries if e.action == RUN)
    if not rebuilt:
        issues.append(Issue("nothing_to_rebuild", ERROR, "the scope selects no runnable task"))
    if issues:
        return _early(revision, issues)

    base_out = {o.name: o.physical for o in baseline.outputs}
    iso_schemas, issues = _destination(baseline, rebuilt, revision, base_out)
    if issues:
        return _early(revision, issues)
    parameters = {**context.parameters, **revision.parameters}
    candidate = dataclasses.replace(context, schemas={**context.schemas, **iso_schemas}, parameters=parameters)
    isolated = load_graph(root, context=candidate)
    if not isolated.ok:
        codes = ", ".join(sorted({d.code for d in isolated.errors}))
        return _early(
            revision, [Issue("invalid_isolated_graph", ERROR, f"the isolated destination is invalid ({codes})")]
        )

    produced = {o.name for o in baseline.outputs if set(o.producers) & set(rebuilt)}
    iso_out = {o.name: o.physical for o in isolated.outputs}
    targets = {base_out[n]: iso_out[n] for n in sorted(produced)}
    inputs, state_outputs, consumers = _inputs(baseline, rebuilt, base_out)
    issues += _collisions(baseline, targets, store)
    records, pin_issues = _pins(revision, inputs, base_out, store, resolved)
    seeds, seed_issues = _seeds(revision, baseline, targets, base_out, state_outputs, store)
    issues += pin_issues + seed_issues
    runner = _runner_plan(isolated, baseline, revision, rebuilt, produced, iso_out, base_out)
    errors_only = [i for i in runner.issues if i.severity == ERROR]
    issues += errors_only
    source = task_fingerprints(isolated, root, rebuilt)
    pinned = sorted((r.physical, r.version) for r in records if r.mode == FROZEN)
    configuration = configuration_digest(parameters, iso_schemas, pinned)
    outside = tuple(sorted(consumers - set(rebuilt)))
    return IsolationPlan(
        revision=revision,
        rebuilt=rebuilt,
        outside_consumers=outside,
        inputs=tuple(records),
        targets=targets,
        schemas=iso_schemas,
        seeds=tuple(seeds),
        parameters=dict(sorted(parameters.items())),
        tasks=source,
        configuration=configuration,
        issues=tuple(sorted(issues, key=lambda i: (i.severity != ERROR, i.code, i.subject))),
        runner_plan=runner,
    )


def _destination(
    baseline: TaskGraph, rebuilt: tuple[str, ...], revision: Revision, base_out: Mapping[str, str]
) -> tuple[dict[str, str], list[Issue]]:
    """Map each rebuilt task's logical schema to its revision-suffixed physical schema."""
    chosen: dict[str, set[str]] = defaultdict(set)
    for task_id in rebuilt:
        node = baseline.task(task_id)
        for name in node.outputs:
            chosen[node.schema].add(base_out[name].split(".")[1])
    issues = [
        Issue("ambiguous_schema", ERROR, f"schema {logical} maps to several physical schemas {sorted(phys)}", logical)
        for logical, phys in sorted(chosen.items())
        if len(phys) > 1
    ]
    mapping = {
        logical: next(iter(phys)) + revision.schema_suffix for logical, phys in sorted(chosen.items()) if len(phys) == 1
    }
    return mapping, issues


def _inputs(
    baseline: TaskGraph, rebuilt: tuple[str, ...], base_out: Mapping[str, str]
) -> tuple[dict[str, tuple[set[str], set[str], str]], set[str], set[str]]:
    """Return inputs keyed by physical, the rebuilt outputs read as state, and every consumer of a rebuilt output."""
    inside = set(rebuilt)
    inputs: dict[str, tuple[set[str], set[str], str]] = {}
    state: set[str] = set()
    for edge in baseline.edges:
        if edge.target is None:
            continue
        if edge.consumer in inside:
            if edge.producer in inside:
                if edge.kind == TEMPORAL:
                    state.add(edge.target)
                continue
            physical = base_out.get(edge.target, edge.target)
            kinds, who, _ = inputs.setdefault(physical, (set(), set(), edge.target))
            kinds.add(edge.kind)
            who.add(edge.consumer)
    consumers: set[str] = set()
    for edge in baseline.edges:
        if edge.kind == DATA and edge.producer in inside and edge.consumer not in inside:
            consumers.add(edge.consumer)
    return inputs, state, consumers


def _collisions(baseline: TaskGraph, targets: Mapping[str, str], store: ExperimentStore | None) -> list[Issue]:
    issues: list[Issue] = []
    live = {o.physical.casefold() for o in baseline.outputs}
    counted: dict[str, int] = defaultdict(int)
    base_schemas = {_parts(o.physical) for o in baseline.outputs}
    for base, iso in sorted(targets.items()):
        counted[iso.casefold()] += 1
        if iso.casefold() in live or iso.casefold() == base.casefold():
            issues.append(Issue("baseline_collision", ERROR, f"isolated target {iso} is a baseline object", iso))
        if _parts(iso) in base_schemas:
            issues.append(Issue("schema_collision", ERROR, f"isolated schema of {iso} is a baseline schema", iso))
    issues += [
        Issue("duplicate_target", ERROR, f"several outputs map to the isolated object {name}", name)
        for name, n in sorted(counted.items())
        if n > 1
    ]
    if store is not None:
        issues += [
            Issue("target_exists", ERROR, f"isolated target {iso} already exists; nothing was created", iso)
            for iso in sorted(set(targets.values()))
            if store.exists(iso)
        ]
    return issues


def _pins(
    revision: Revision,
    inputs: Mapping[str, tuple[set[str], set[str], str]],
    base_out: Mapping[str, str],
    store: ExperimentStore | None,
    resolved: Mapping[str, int] | None,
) -> tuple[list[InputRecord], list[Issue]]:
    alias = {physical: physical for physical in inputs} | {
        name: physical for name, physical in base_out.items() if physical in inputs
    }
    alias |= {target: physical for physical, (_, _, target) in inputs.items()}
    chosen: dict[str, tuple[int | None, str]] = {}
    issues: list[Issue] = []
    for pin in revision.pins:
        physical = alias.get(pin.input)
        if physical is None:
            issues.append(Issue("pin_unused", ERROR, f"pin {pin.input!r} names no input read by the scope", pin.input))
            continue
        if physical in chosen:
            issues.append(Issue("duplicate_pin", ERROR, f"input {physical} is pinned twice", physical))
            continue
        label = (
            f"version {pin.version}"
            if pin.version is not None
            else (f"timestamp {pin.timestamp}" if pin.timestamp else "current")
        )
        version, problem = _resolve(pin.version, pin.timestamp, physical, store, resolved)
        if problem:
            issues.append(Issue("pin_unresolved", ERROR, problem, physical))
        chosen[physical] = (version, label)
    records = [
        InputRecord(
            physical,
            tuple(sorted(kinds)),
            tuple(sorted(who)),
            FROZEN if physical in chosen else LIVE,
            chosen[physical][0] if physical in chosen else None,
            chosen[physical][1] if physical in chosen else "",
        )
        for physical, (kinds, who, _) in sorted(inputs.items())
    ]
    issues += [
        Issue("unpinned_input", INFO, f"{r.physical} is read live and not reproducible", r.physical)
        for r in records
        if r.mode == LIVE
    ]
    return records, issues


def _resolve(  # noqa: PLR0911 - one return per distinct resolution outcome
    version: int | None,
    timestamp: str | None,
    physical: str,
    store: ExperimentStore | None,
    resolved: Mapping[str, int] | None,
) -> tuple[int | None, str]:
    if resolved is not None and physical in resolved:
        return resolved[physical], ""
    if store is None:
        if version is not None:
            return version, ""
        return None, f"pin of {physical} needs a store to resolve its version"
    latest = store.version(physical)
    if latest is None:
        return None, f"{physical} has no Delta version to pin (missing table or not a Delta table)"
    if version is not None:
        if version > latest:
            return None, f"{physical} has no version {version} (latest is {latest})"
        return version, ""
    if timestamp is not None:
        found = store.version_at(physical, timestamp)
        if found is None:
            return None, f"{physical} has no version at or before {timestamp}"
        return found, ""
    return latest, ""


def _seeds(  # noqa: PLR0913, PLR0917 - the planning inputs
    revision: Revision,
    baseline: TaskGraph,
    targets: Mapping[str, str],
    base_out: Mapping[str, str],
    state_outputs: set[str],
    store: ExperimentStore | None,
) -> tuple[list[tuple[str, str, int | None]], list[Issue]]:
    del baseline
    alias = {**{p: p for p in targets}, **{n: base_out[n] for n in base_out if base_out[n] in targets}}
    seeds: list[tuple[str, str, int | None]] = []
    issues: list[Issue] = []
    for seed in revision.seeds:
        physical = alias.get(seed.output)
        if physical is None:
            issues.append(
                Issue(
                    "seed_not_isolated",
                    ERROR,
                    f"seed {seed.output!r} is not an output rebuilt by this revision",
                    seed.output,
                )
            )
            continue
        if store is not None and not store.exists(physical):
            issues.append(Issue("seed_missing_source", ERROR, f"seed source {physical} does not exist", physical))
        elif store is not None and seed.version is not None and (store.version(physical) or 0) < seed.version:
            issues.append(
                Issue("seed_missing_source", ERROR, f"seed source {physical} has no version {seed.version}", physical)
            )
        seeds.append((physical, targets[physical], seed.version))
    seeded = {b for b, _, _ in seeds}
    severity = ERROR if revision.backfill else WARNING
    for name in sorted(state_outputs):
        physical = base_out.get(name, name)
        if physical in targets and physical not in seeded:
            what = "a historical backfill" if revision.backfill else "a stateful run"
            text = f"{what} reads earlier periods of {physical} (read_state) but the revision does not seed its history"
            issues.append(Issue("unseeded_state", severity, text, physical))
    return sorted(seeds), issues


def _runner_plan(  # noqa: PLR0913, PLR0917 - the planning inputs
    isolated: TaskGraph,
    baseline: TaskGraph,
    revision: Revision,
    rebuilt: tuple[str, ...],
    produced: set[str],
    iso_out: Mapping[str, str],
    base_out: Mapping[str, str],
) -> Plan:
    """Build the runner plan on the isolated graph, then bind reads outside the boundary to baseline objects."""
    del baseline, rebuilt
    plan = build_plan(isolated, roots=revision.roots, upstream=False, downstream=revision.downstream, partial=True)
    to_name = {physical: name for name, physical in iso_out.items()}

    def rebind(binding: ReadBinding) -> ReadBinding:
        name = to_name.get(binding.physical)
        if name is None or name in produced:
            return binding
        return dataclasses.replace(binding, physical=base_out[name])

    entries = tuple(
        dataclasses.replace(
            e, reads=tuple(sorted(map(rebind, e.reads), key=lambda r: (r.kind, r.reference, r.physical)))
        )
        for e in plan.entries
    )
    issues = tuple(i for i in plan.issues if i.code != "missing_upstream")
    return dataclasses.replace(plan, entries=entries, issues=issues)
