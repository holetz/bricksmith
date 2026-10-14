"""Plan construction: root selection, closure, filters and validation against a current graph."""

from __future__ import annotations

import fnmatch
import hashlib
import json
from typing import TYPE_CHECKING

from bricksmith.errors import AuthorizationError, PlanError, StalePlanError
from bricksmith.plan.model import (
    ERROR,
    RUN,
    SKIP,
    WARNING,
    Issue,
    Plan,
    PlanEntry,
    ReadBinding,
    Selection,
    Target,
)
from bricksmith.tasks.declaration import DATA

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from bricksmith.graph import TaskGraph, TaskNode

DEFAULT_OPERATION = "write"


def graph_fingerprint(graph: TaskGraph) -> str:
    """Hash what execution depends on (identities, flags, outputs, edges); line numbers are ignored."""
    content = {
        "edges": sorted(
            [e.consumer, e.kind, e.reference, e.target, e.producer] for e in graph.edges if e.target is not None
        ),
        "errors": sorted(d.code for d in graph.errors),
        "outputs": [[o.name, o.physical, list(o.producers)] for o in graph.outputs],
        "tasks": [
            [t.id, t.kind, t.function, t.path, list(t.outputs), t.enabled, t.static, dict(t.variant), dict(t.metadata)]
            for t in graph.tasks
        ],
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def declared_operations(node: TaskNode) -> tuple[str, ...]:
    """Return the operations a task declares: ``metadata.operations``, else its ``write`` strategy, else ``write``."""
    listed = node.metadata.get("operations")
    if isinstance(listed, list) and listed and all(isinstance(item, str) for item in listed):
        return tuple(sorted(set(listed)))
    strategy = node.metadata.get("write")
    return (strategy,) if isinstance(strategy, str) and strategy else (DEFAULT_OPERATION,)


def _matches(task_id: str, patterns: Iterable[str]) -> bool:
    bare = task_id.split("[", 1)[0]
    return any(fnmatch.fnmatchcase(task_id, p) or fnmatch.fnmatchcase(bare, p) for p in patterns)


def _closure(seeds: set[str], links: dict[str, set[str]]) -> set[str]:
    seen = set(seeds)
    stack = list(seeds)
    while stack:
        for nxt in links.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _entry(graph: TaskGraph, node: TaskNode, action: str, reason: str, by: str) -> PlanEntry:
    physical = {o.name: o.physical for o in graph.outputs}
    reads = {
        ReadBinding(e.reference, e.kind, physical.get(e.target, e.target))
        for e in graph.edges
        if e.consumer == node.id and e.target is not None
    }
    operations = declared_operations(node)
    protected = node.metadata.get("protected") is True
    return PlanEntry(
        task_id=node.id,
        action=action,
        reason=reason,
        selected_by=by,
        kind=node.kind,
        path=node.path,
        function=node.function,
        variant=dict(node.variant),
        depends_on=graph.dependencies(node.id, kind=DATA),
        reads=tuple(sorted(reads, key=lambda r: (r.kind, r.reference, r.physical))),
        targets=tuple(Target(name, physical[name], operations, protected) for name in node.outputs),
    )


def build_plan(  # noqa: PLR0913 - one keyword per documented selection field
    graph: TaskGraph,
    *,
    roots: Sequence[str] = (),
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    upstream: bool = True,
    downstream: bool = False,
    include_static: bool = False,
    partial: bool = False,
    done: Sequence[str] = (),
) -> Plan:
    """Select ``roots`` (all tasks when empty), close them over data edges and decide each task's action.

    Tasks are identified by id or ``fnmatch`` pattern; a pattern without ``[...]`` also matches every
    variant. ``include``/``exclude`` filter the closure (roots too). Disabled tasks, static tasks that
    are not roots (unless ``include_static``) and ``done`` tasks are listed as ``skip`` with a reason.
    An incomplete closure is an error issue unless ``partial`` downgrades it to a warning. The plan is
    always returned, never raised for project findings; ``plan.errors`` tells whether it can be applied.
    """
    selection = Selection(
        tuple(roots),
        tuple(include),
        tuple(exclude),
        upstream,
        downstream,
        include_static,
        partial,
        tuple(sorted(done)),
    )
    fingerprint = graph_fingerprint(graph)
    if not graph.ok:
        codes = ", ".join(sorted({d.code for d in graph.errors}))
        issue = Issue("invalid_graph", ERROR, f"the dependency graph has errors ({codes}); no plan can start", "")
        return Plan(selection, fingerprint, (), (issue,))

    ids = [node.id for node in graph.tasks]
    root_ids = set(ids) if not roots else {i for i in ids if _matches(i, roots)}
    issues = [
        Issue("unknown_root", ERROR, f"no task matches {pattern!r}", pattern)
        for pattern in roots
        if not any(_matches(i, [pattern]) for i in ids)
    ]
    up: dict[str, set[str]] = {i: set(graph.dependencies(i)) for i in ids}
    down: dict[str, set[str]] = {i: set() for i in ids}
    for consumer, producers in up.items():
        for producer in producers:
            down[producer].add(consumer)
    upstream_of_roots = _closure(root_ids, up)
    selected = set(root_ids)
    if upstream:
        selected |= upstream_of_roots
    if downstream:
        selected |= _closure(root_ids, down)
    if include:
        selected = {i for i in selected if _matches(i, include)}
    selected = {i for i in selected if not _matches(i, exclude)}
    if not selected and not issues:
        issues.append(Issue("empty_selection", ERROR, "the selection matches no task", ""))

    entries: list[PlanEntry] = []
    for task_id in graph.order():
        if task_id not in selected:
            continue
        node = graph.task(task_id)
        by = "root" if task_id in root_ids else ("upstream" if task_id in upstream_of_roots else "downstream")
        action, reason = _action(node, is_root=task_id in root_ids, selection=selection)
        entries.append(_entry(graph, node, action, reason, by))
    issues.extend(_closure_issues(entries, WARNING if partial else ERROR))
    return Plan(selection, fingerprint, tuple(entries), tuple(issues))


def _action(node: TaskNode, *, is_root: bool, selection: Selection) -> tuple[str, str]:
    if not node.enabled:
        return SKIP, "disabled"
    if node.id in selection.done:
        return SKIP, "done in a previous run"
    if node.static and not is_root and not selection.include_static:
        return SKIP, "static output; not recomputed"
    return RUN, "selected"


def _closure_issues(entries: list[PlanEntry], severity: str) -> list[Issue]:
    planned = {e.task_id: e for e in entries}
    issues: list[Issue] = []
    for item in entries:
        if item.action != RUN:
            continue
        for producer in item.depends_on:
            found = planned.get(producer)
            if found is None:
                text = f"{item.task_id} reads {producer}, which is not in the plan (not selected or filtered out)"
                issues.append(Issue("missing_upstream", severity, text, producer))
            elif found.reason == "disabled":
                text = f"{item.task_id} reads {producer}, which is disabled"
                issues.append(Issue("disabled_upstream", severity, text, producer))
    if entries and not any(e.action == RUN for e in entries):
        issues.append(Issue("nothing_to_run", WARNING, "every selected task is skipped", ""))
    return issues


def plan_again(plan: Plan, graph: TaskGraph) -> Plan:
    """Rebuild a plan from ``graph`` with the selection recorded in ``plan``."""
    s = plan.selection
    return build_plan(
        graph,
        roots=s.roots,
        include=s.include,
        exclude=s.exclude,
        upstream=s.upstream,
        downstream=s.downstream,
        include_static=s.include_static,
        partial=s.partial,
        done=s.done,
    )


def validate_plan(plan: Plan, graph: TaskGraph) -> None:
    """Reject a plan that has errors or no longer matches ``graph`` (graph, tasks, order or targets changed)."""
    if plan.errors:
        listed = "; ".join(f"{i.code}: {i.message}" for i in plan.errors)
        msg = f"the plan has errors and cannot start: {listed}"
        raise PlanError(msg)
    current = plan_again(plan, graph)
    if current.graph_fingerprint != plan.graph_fingerprint:
        msg = "stale plan: the task graph changed since the plan was made; plan again"
        raise StalePlanError(msg)
    if current.fingerprint != plan.fingerprint:
        before = {e.task_id: e for e in plan.entries}
        after = {e.task_id: e for e in current.entries}
        changed = sorted(t for t in {*before, *after} if before.get(t) != after.get(t))
        msg = f"stale plan: targets, order or scope changed for {', '.join(changed) or 'the selection'}; plan again"
        raise StalePlanError(msg)


def check_authorization(plan: Plan) -> None:
    """Require a grant for every operation on every protected target that runs; reject grants beyond the plan.

    Grants are exact (object, operation) pairs: authorizing one protected output never covers the
    protected outputs of its upstream tasks, which need their own grants.
    """
    if plan.grants and plan.granted_for != plan.fingerprint:
        msg = "the authorizations were granted for a different plan; review and authorize again"
        raise AuthorizationError(msg)
    targets = {t.physical: t for e in plan.to_run for t in e.targets}
    for grant in plan.grants:
        target = targets.get(grant.object)
        if target is None or not set(grant.operations) <= set(target.operations):
            msg = f"authorization for {grant.object!r} {list(grant.operations)} is outside the reviewed plan"
            raise AuthorizationError(msg)
    granted = {(g.object, op) for g in plan.grants for op in g.operations}
    missing = sorted(
        f"{t.physical}:{op}"
        for e in plan.to_run
        for t in e.targets
        if t.protected
        for op in t.operations
        if (t.physical, op) not in granted
    )
    if missing:
        msg = f"protected targets are not authorized: {', '.join(missing)}"
        raise AuthorizationError(msg)
