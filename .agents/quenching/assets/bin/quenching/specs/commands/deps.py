"""Dependency edits and derived schedules, through the spec backend only."""
from __future__ import annotations

import json

from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter, read_one
from quenching.specs.parse.deps import dep_findings, deps_of, find_cycle_through, schedule
from quenching.specs.parse.epics import reverse_index
from quenching.specs.parse.fields import set_frontmatter_key


def _infos(backend) -> dict[str, dict]:
    rows = backend.list_specs()
    return {str(key): info for key, info in
            backend.read_specs([row["id"] for row in rows]).items() if info is not None}


def _flow(ids: list[str]) -> str:
    # Native numeric ids stay numeric in YAML; other ids are safely quoted.
    return "[" + ", ".join(dep if dep.isascii() and dep.isdecimal()
                           and str(int(dep)) == dep else json.dumps(dep)
                           for dep in ids) + "]"


def cmd_depends(args, root: str, out: Emitter) -> int:
    """Read or edit ONE spec's dependencies, validating before any write."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    current = deps_of(info["frontmatter"])
    desired = current
    if args.add is not None or args.remove is not None:
        infos = _infos(backend)
        spec = str(info["id"])
        added = deps_of({"dependsOn": args.add})
        removed = deps_of({"dependsOn": args.remove})
        # Only ids to add are validated: `--remove` is the repair for a missing or own id.
        findings = dep_findings({**infos, spec: {
            **info, "frontmatter": {**info["frontmatter"], "dependsOn": added},
        }})
        refusal = next((row for row in findings if row["spec"] == spec
                        and row["code"] in ("sp-dep-self", "sp-dep-missing")), None)
        if refusal:
            return out.emit_err(args.json, {**refusal, "exit": 2})
        desired = [dep for dep in dict.fromkeys(current + added) if dep not in removed]
        edges = {key: [d for d in deps_of(value["frontmatter"]) if d != key]
                 for key, value in infos.items()}
        edges[spec] = [d for d in desired if d != spec]
        cycle = find_cycle_through(edges, spec)
        if cycle:
            return out.emit_err(args.json, {
                "code": "sp-dep-cycle", "exit": 2, "cycle": cycle,
                "message": "dependency cycle: " + " -> ".join(cycle),
            })
        if desired != current:
            backend.write_spec(info, set_frontmatter_key(info["text"], "dependsOn", _flow(desired)))
    out.emit(args.json, {"ok": True, "id": info["id"], "dependsOn": desired},
             f"{info['id']} — dependsOn: {_flow(desired)}")
    return 0


def cmd_schedule(args, root: str, out: Emitter) -> int:
    """Read the store once in bulk and emit the pure schedule derivation."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    infos = _infos(backend)
    spec = str(args.spec) if args.spec is not None else None
    every = getattr(args, "all", False)
    if spec is not None and every:
        return out.emit_err(args.json, {
            "code": "sp-conflicting-selection", "exit": 2,
            "message": "pass --spec or --all, not both"})
    if spec is not None and spec not in infos:
        return out.emit_err(args.json, {"code": "sp-unknown-id", "exit": 2, "id": spec,
                                       "message": f"no spec with id '{spec}'"})
    # `--all` is the reader that needs both directions of the graph: the specs waiting for a
    # node are the nodes that name it, and an archived dependent or a spec with no edge of
    # its own is outside the default selection.
    selected = list(infos) if every else ([spec] if spec is not None else None)
    result = schedule(infos, selected)
    cycle = result["cycle"]
    human = ("dependency cycle: " + " -> ".join(cycle)) if cycle else "\n".join(
        f"{node['id']} — start: {node['start']}, target: {node['target']}, end: {node['end']}; "
        f"earliestStart: {node['earliestStart']}, earliestFinish: {node['earliestFinish']}; "
        f"slipDays: {node['slipDays']}, critical: {node['critical']}"
        for node in result["nodes"])
    if not cycle:
        human += "\ncriticalPath: " + " -> ".join(result["criticalPath"])
    payload = {"ok": not bool(cycle), **result}
    # The reverse of composition, over the same bulk read: a member asking which composites name it
    # has no cheaper answer, and an index over a subset of the store would lie about the whole.
    if every:
        payload["composites"] = reverse_index(infos)
    out.emit(args.json, payload, human)
    return 1 if cycle else 0
