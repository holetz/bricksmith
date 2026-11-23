"""`parallel` — prove a `[P]` group's `files:` sets are disjoint, mechanically."""
from __future__ import annotations

import json

from quenching.common.paths import escapes_repo, repo_path
from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter, read_one
from quenching.specs.parse.tasks import _files_bad_annotation


def _overlaps(a: str, b: str) -> bool:
    """Fail closed: the root covers every path, and a path outside the repository cannot be
    proven disjoint from anything, so both count as a clash."""
    a, b = repo_path(a), repo_path(b)
    if "." in (a, b) or escapes_repo(a) or escapes_repo(b):
        return True
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def parallel_groups(tasks: list[dict]) -> list[list[dict]]:
    """Consecutive `[P]` tasks within one `### N.` section form a group."""
    groups, cur, sec = [], [], None
    for t in tasks:
        if t["parallel"] and (sec is None or t["section"] == sec):
            cur.append(t)
            sec = t["section"]
            continue
        if len(cur) > 1:
            groups.append(cur)
        cur, sec = ([t], t["section"]) if t["parallel"] else ([], None)
    if len(cur) > 1:
        groups.append(cur)
    return groups


def cmd_parallel(args, root: str, out: Emitter) -> int:
    """Prove a `[P]` group's `files:` sets are disjoint — MECHANICALLY, never judged in
    prose. A group with an undeclared `files:` is ineligible: nothing can be proven about
    a task that never said what it touches. So is a group where a member waits `after:` a
    sibling: running both at once builds the dependent over a tree that lacks its dependency."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    findings = []
    for gi, group in enumerate(parallel_groups(info["tasks"]), 1):
        undeclared = [t["id"] for t in group if not t["files"]]
        annotations = []
        for t in group:
            for e in t["files"]:
                note = _files_bad_annotation(e)
                if note:
                    annotations.append({"task": t["id"], "entry": e, "note": note})
        clashes = []
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                for fa in a["files"]:
                    for fb in b["files"]:
                        if _overlaps(fa, fb):
                            clashes.append({"a": a["id"], "b": b["id"],
                                            "file": repo_path(fa)})
        ids = {t["id"] for t in group}
        dependent = [{"task": t["id"], "after": d} for t in group
                     for d in t.get("after") or [] if d in ids and d != t["id"]]
        eligible = not undeclared and not clashes and not annotations and not dependent
        findings.append({"group": gi, "tasks": [t["id"] for t in group],
                         "eligible": eligible, "undeclared": undeclared,
                         "annotations": annotations, "clashes": clashes,
                         "dependent": dependent})
    ok = all(f["eligible"] for f in findings)
    if args.json:
        print(json.dumps({"ok": ok, "id": info["id"], "groups": findings},
                         indent=2, ensure_ascii=False))
    else:
        if not findings:
            print(f"{info['id']}: no [P] groups — serial execution")
        for f in findings:
            print(f"group {f['group']}: {', '.join(x or '?' for x in f['tasks'])} — "
                  f"{'eligible' if f['eligible'] else 'NOT eligible'}")
            for c in f["clashes"]:
                print(f"    {c['a']} and {c['b']} both touch {c['file']}")
            for d in f["dependent"]:
                print(f"    {d['task']} waits after: {d['after']} in the same group")
            for a in f["annotations"]:
                print(f"    {a['task']} declares non-path files entry {a['entry']!r} — "
                      f"remove the comment; `(new)` is the only reserved annotation")
            if f["undeclared"]:
                print(f"    no files: declared by {', '.join(f['undeclared'])}")
    return 0 if ok else 1
