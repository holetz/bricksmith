"""Spec dependencies — findings and a schedule derived from the caller's info map.

Pure derivation, no backend and no I/O: dates and dependency edges come only from the
frontmatter supplied by the caller. Unknown dates stay unknown.
"""
from __future__ import annotations

from datetime import date, timedelta
import heapq


def deps_of(fm: dict) -> list[str]:
    """Normalized dependency ids, deduplicated in declaration order."""
    raw = fm.get("dependsOn")
    values = raw if isinstance(raw, list) else str(raw or "").split(",")
    result = []
    for value in values:
        dep = str(value).strip().removeprefix("#").strip()
        if dep and dep not in result:
            result.append(dep)
    return result


def find_cycle(edges: dict[str, list[str]]) -> list[str] | None:
    """One dependency cycle as an id path, or `None`; unknown nodes are ignored.

    Iterative, so a long chain is bounded by memory and not by the recursion limit.
    """
    color: dict[str, int] = {}
    stack: list[str] = []
    for root in sorted(edges):
        if color.get(root, 0):
            continue
        color[root] = 1
        stack.append(root)
        frames = [iter(edges[root])]
        while frames:
            for dep in frames[-1]:
                if dep not in edges:
                    continue
                state = color.get(dep, 0)
                if state == 1:
                    return stack[stack.index(dep):] + [dep]
                if state == 0:
                    color[dep] = 1
                    stack.append(dep)
                    frames.append(iter(edges[dep]))
                    break
            else:
                color[stack.pop()] = 2
                frames.pop()
    return None


def find_cycle_through(edges: dict[str, list[str]], node: str) -> list[str] | None:
    """A cycle through `node` as an id path starting at its smallest id, or `None`.

    Self-edges and cycles that do not pass through `node` are ignored.
    """
    path = [node]
    visited = {node}
    frames = [iter(edges.get(node, []))]
    while frames:
        for dep in frames[-1]:
            if dep == node and len(path) > 1:
                loop = path[path.index(min(path)):] + path[:path.index(min(path))]
                return loop + [loop[0]]
            if dep in edges and dep not in visited:
                visited.add(dep)
                path.append(dep)
                frames.append(iter(edges[dep]))
                break
        else:
            path.pop()
            frames.pop()
    return None


def dep_findings(infos: dict[str, dict]) -> list[dict]:
    """Dependency errors and dropped-dependency warnings, followed by one graph cycle."""
    findings = []
    edges = {}
    for spec in sorted(infos):
        deps = deps_of(infos[spec]["frontmatter"])
        edges[spec] = [dep for dep in deps if dep != spec]
        for dep in deps:
            if dep == spec:
                findings.append({
                    "code": "sp-dep-self", "severity": "error", "spec": spec,
                    "message": f"spec {spec} depends on itself",
                })
            elif dep not in infos:
                findings.append({
                    "code": "sp-dep-missing", "severity": "error", "spec": spec,
                    "message": f"spec {spec} depends on {dep}, which is not in the store",
                })
            elif infos[dep]["phase"] == "archive":
                outcome = str(infos[dep]["frontmatter"].get("outcome") or "").strip()
                if outcome != "done":
                    findings.append({
                        "code": "sp-dep-dropped", "severity": "warn", "spec": spec,
                        "outcome": outcome,
                        "message": f"spec {spec} depends on {dep}, which was dropped",
                    })
    cycle = find_cycle(edges)
    if cycle:
        findings.append({
            "code": "sp-dep-cycle", "severity": "error", "spec": cycle[0],
            "cycle": cycle, "message": "dependency cycle: " + " -> ".join(cycle),
        })
    return findings


def _date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def schedule(infos: dict[str, dict], ids: list[str] | None = None) -> dict:
    """Schedule selected specs and their existing dependencies, without inventing dates.

    By default select open specs with dependencies and their transitive closure. An own
    start wins a date tie; tied dependencies keep their declaration order.
    """
    deps = {spec: deps_of(info["frontmatter"]) for spec, info in infos.items()}
    pending = list(ids) if ids is not None else [
        spec for spec, info in infos.items() if info["phase"] != "archive" and deps[spec]
    ]
    selected: set[str] = set()
    while pending:
        spec = pending.pop()
        if spec not in infos or spec in selected:
            continue
        selected.add(spec)
        pending.extend(deps[spec])
    edges = {spec: [dep for dep in deps[spec] if dep in selected and dep != spec]
             for spec in sorted(selected)}
    cycle = find_cycle(edges)
    if cycle:
        return {"cycle": cycle, "nodes": [], "criticalPath": []}

    remaining = {spec: len(after) for spec, after in edges.items()}
    successors: dict[str, list[str]] = {spec: [] for spec in edges}
    for spec, after in edges.items():
        for dep in after:
            successors[dep].append(spec)
    ready = [spec for spec in edges if remaining[spec] == 0]
    heapq.heapify(ready)
    nodes: dict[str, dict] = {}
    finishes: dict[str, date | None] = {}
    while ready:
        spec = heapq.heappop(ready)
        info = infos[spec]
        fm = info["frontmatter"]
        start, target = _date(fm.get("start")), _date(fm.get("target"))
        end = _date(fm.get("end"))
        duration = (target - start).days if start and target and target >= start else None
        earliest, driven_by = start, None
        blocked = False
        archived_done = info["phase"] == "archive" and str(fm.get("outcome") or "").strip() == "done"
        for dep in edges[spec]:
            finish = finishes[dep]
            if finish is None and infos[dep]["phase"] != "archive":
                blocked = True
            elif finish is not None and (earliest is None or finish > earliest):
                earliest, driven_by = finish, dep
        if archived_done:
            finish = end or target
        elif blocked:
            earliest, driven_by, finish = None, None, None
        elif end is not None:
            # The finish the spec declares beats a derived one, but never precedes the start.
            finish = max(end, earliest) if earliest is not None else end
        else:
            finish = earliest + timedelta(days=duration) \
                if earliest is not None and duration is not None else target
        nodes[spec] = {
            "id": spec, "title": fm.get("title") or "", "phase": info["phase"],
            "start": _iso(start), "target": _iso(target), "end": _iso(end), "dependsOn": deps[spec],
            "earliestStart": _iso(earliest), "earliestFinish": _iso(finish),
            "slipDays": max(0, (finish - target).days) if finish and target else 0,
            "critical": False, "drivenBy": driven_by,
        }
        if info["phase"] == "archive":
            outcome = str(fm.get("outcome") or "").strip()
            finishes[spec] = (end or target) if outcome == "done" else None
        else:
            finishes[spec] = finish
        for successor in successors[spec]:
            remaining[successor] -= 1
            if remaining[successor] == 0:
                heapq.heappush(ready, successor)

    known = [spec for spec in nodes if nodes[spec]["earliestFinish"] is not None
             and not (infos[spec]["phase"] == "archive"
                      and str(infos[spec]["frontmatter"].get("outcome") or "").strip() != "done")]
    critical_path = []
    if known:
        latest = max(nodes[spec]["earliestFinish"] for spec in known)
        current = min(spec for spec in known if nodes[spec]["earliestFinish"] == latest)
        while current is not None:
            critical_path.append(current)
            nodes[current]["critical"] = True
            current = nodes[current]["drivenBy"]
        critical_path.reverse()
    return {
        "cycle": None,
        "nodes": sorted(nodes.values(), key=lambda node: (node["earliestStart"] or "9999", node["id"])),
        "criticalPath": critical_path,
    }
