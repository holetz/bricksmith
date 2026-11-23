"""Composition — a spec whose `## Tasks` items may name member specs, derived and never ticked.

Pure derivation, no backend and no I/O: the caller hands over the composite's `info` and the
members' `info` rows it read, and gets the state back. That is what keeps one answer across the
memory fake, the git store and the tracker backends.

A spec item reads `- [ ] S2 Title — spec: #1202 — after: S1,S3`; a member kept in another
repository's store reads `— spec: <owner>/<repo>#<id>`, and its key keeps that form (owner and
repo lower-cased) so it never collides with a local id. The `### N.` headings above the items
are its waves. The item's state is DERIVED from the member document: done when the member is
archived with `outcome: done` (and, when its PR is known, merged: `unmerged` otherwise),
blocked when one of its tasks is `[!]`. The box written in the composite is never read as
state, which is why `task` refuses a spec item.

Composition is a PROPERTY, not a type: any spec with at least one spec item is a composite,
may nest, and may belong to several composites. `workItemType: epic` no longer defines it."""
from __future__ import annotations

import re

from quenching.specs.parse.refs import is_remote, local_key, member_key, split_ref
from quenching.specs.parse.sections import ready_report
from quenching.specs.parse.tasks import SPEC_REF_RE, TAIL_RE, task_progress
from quenching.specs.parse.text import HEADING_RE

# The 100-sub-issue ceiling a GitHub parent card carries (to be confirmed); `validate` warns.
MAX_ITEMS = 100

_LABEL_NUM_RE = re.compile(r"(\d+)$")


def has_links(info: dict) -> bool:
    """Whether this spec names at least one member — i.e. it is a composite."""
    return bool(parse_items(info))


def epic_ref(fm: dict) -> str | None:
    """The `epic:` key a member carries, as a bare id string. DEPRECATED and ignored: the
    link lives in the composite's `## Tasks`, read from both ends by derivation."""
    v = str(fm.get("epic") or "").strip().lstrip("#").strip()
    return v or None


def group_titles(info: dict) -> list[str]:
    """The `### ` headings under `## Tasks`, in order; the first one is group 1."""
    sections = info.get("sections")
    sec = sections.get("Tasks") if isinstance(sections, dict) else None
    heads = [HEADING_RE.match(line) for line in (sec["lines"] if sec else [])]
    return [m.group(2).strip() for m in heads if m and len(m.group(1)) == 3]


def _link_rows(info: dict) -> list[dict]:
    """The rows that are spec items. `info["links"]` is the derivation's split; a hand-built
    info falls back to the rows that carry a spec reference."""
    rows = info.get("links")
    if rows is not None:
        return rows
    return [t for t in info.get("tasks", []) if t.get("kind") == "link" or t.get("spec")
            or SPEC_REF_RE.search(str(t.get("text") or ""))]


def parse_items(info: dict) -> list[dict]:
    """Every spec item of a composite: label, title, member id, `after` labels, wave."""
    titles = group_titles(info)
    items = []
    for t in _link_rows(info):
        body = str(t.get("text") or "")
        label = t.get("label")
        title = t.get("title")
        if label is None:
            head = TAIL_RE.sub("", body).strip()
            label, _, title = head.partition(" ")
        spec = t.get("spec")
        if spec is None:
            m = SPEC_REF_RE.search(body)
            spec = member_key(m.group(1)) if m else None
        group = t.get("section", 0)
        items.append({
            "label": label, "title": (title or "").strip(), "spec": spec,
            "after": list(t.get("after") or []),
            "group": group, "groupTitle": titles[group - 1] if 0 < group <= len(titles) else None,
            "writtenState": t.get("state", " "), "lineno": t.get("lineno"),
            "blockEndLineno": t.get("blockEndLineno"),
        })
    return items


def reverse_index(infos: dict) -> dict[str, list[dict]]:
    """Where each spec sits: member id → the composites that name it, by label and wave.

    The other end of `parse_items`. The link lives in the composite's `## Tasks`, so only a
    store-wide read can answer it — that is why this rides the bulk `schedule --all` instead of a
    per-spec verb. An id the store does not hold is skipped (a stale reference is `validate`'s
    finding, not a row that points nowhere), and so is a composite naming itself.
    """
    index: dict[str, list[dict]] = {}
    for key in sorted(infos, key=str):
        for item in parse_items(infos[key]):
            member = str(item.get("spec") or "")
            if not member or member == str(key) or member not in infos:
                continue
            index.setdefault(member, []).append({
                "epic": str(key),
                "label": str(item.get("label") or ""),
                "group": int(item.get("group") or 0),
            })
    return index


def pr_number(member: dict) -> str | None:
    """The PR number a member's `pr:` record carries, or `None` when it has none."""
    rec = member["frontmatter"].get("pr")
    n = str(rec.get("number", "")).strip() if isinstance(rec, dict) else ""
    return n or None


def member_state(member: dict | None, pr_state: str | None = None,
                 looked_up: bool = False) -> str:
    """`missing`, `done`, `unmerged` (archived `done` whose PR is explicitly not `MERGED`),
    `unverified` (archived `done` with a `pr:` record whose state the caller tried and failed to
    read), `dropped` (archived without `done`), `blocked` or `open`. `pr_state` is the member's
    PR state when the caller could read it; `looked_up` says the caller tried at all. A caller
    that never looked keeps `done`; one that looked and got nothing never claims a delivery."""
    if member is None:
        return "missing"
    if member["phase"] == "archive":
        if str(member["frontmatter"].get("outcome", "")).strip() != "done":
            return "dropped"
        if pr_number(member) and pr_state and pr_state.upper() != "MERGED":
            return "unmerged"
        if pr_number(member) and looked_up and not pr_state:
            return "unverified"
        return "done"
    return "blocked" if task_progress(member["tasks"])[1] else "open"


def find_cycle(items: list[dict]) -> list[str] | None:
    """Iteratively find one dependency cycle as a label path (`A, B, A`), or `None`.

    Unknown labels are ignored.
    """
    graph = {i["label"]: list(i["after"]) for i in items}
    color: dict[str, int] = {}
    stack: list[str] = []

    for n in graph:
        if color.get(n, 0):
            continue
        color[n] = 1
        stack.append(n)
        frames = [iter(graph[n])]
        while frames:
            for d in frames[-1]:
                if d not in graph:
                    continue
                state = color.get(d, 0)
                if state == 1:
                    return stack[stack.index(d):] + [d]
                if state == 0:
                    color[d] = 1
                    stack.append(d)
                    frames.append(iter(graph[d]))
                    break
            else:
                color[stack.pop()] = 2
                frames.pop()
    return None


def task_after_problems(info: dict) -> dict:
    """The task `after:` labels that can never be satisfied, for one document.

    `unknown` lists `{task, label, line}` for every label naming neither a task id nor a spec
    item of this document — the set `next` counts as done. `cycle` is one task cycle as a label
    path, or `None`. A spec item's own `after:` is `composition_findings`'s, not this one's."""
    tasks = info.get("tasks") or []
    known = {t["id"] for t in tasks if t["id"]}
    known |= {i["label"] for i in info.get("links") or [] if i.get("label")}
    unknown = [{"task": t["id"] or t["index"], "label": d, "line": t["lineno"]}
               for t in tasks for d in t.get("after") or [] if d not in known]
    cycle = find_cycle([{"label": t["id"], "after": t.get("after") or []}
                        for t in tasks if t["id"]])
    return {"unknown": unknown, "cycle": cycle}


def composition_cycle(infos: dict[str, dict]) -> list[str] | None:
    """One member cycle across composites (`A -> B -> A`), or `None`.

    The graph is composite id -> the local ids its spec items name. A cross-repository key is
    not followed: its store is read by `read_members`, not by this sweep. `find_cycle` catches
    the `after:` cycle inside one document; this one catches the document-level loop."""
    graph: dict[str, list[str]] = {}
    for sid, info in infos.items():
        if info is None:
            continue
        graph[str(sid)] = [str(i["spec"]) for i in parse_items(info)
                           if i["spec"] and not is_remote(str(i["spec"]))]
    color: dict[str, int] = {}
    stack: list[str] = []
    for n in graph:
        if color.get(n, 0):
            continue
        color[n] = 1
        stack.append(n)
        frames = [iter(graph[n])]
        while frames:
            for d in frames[-1]:
                if d not in graph:
                    continue
                state = color.get(d, 0)
                if state == 1:
                    return stack[stack.index(d):] + [d]
                if state == 0:
                    color[d] = 1
                    stack.append(d)
                    frames.append(iter(graph[d]))
                    break
            else:
                color[stack.pop()] = 2
                frames.pop()
    return None


def derive_epic(info: dict, members: dict[str, dict | None],
                pr_state: dict[str, str] | None = None,
                unreadable: set[str] | None = None) -> dict:
    """The composite's whole derived state. `members` maps a member key to its `info`, or
    `None`; `unreadable` names the keys whose store could not be read, which derive
    `unreadable` and never satisfy a dependency.
    `pr_state` maps a member id to its PR's state (`MERGED`, `OPEN`, `CLOSED`); the derivation
    stays pure, the caller does the lookup. `None` means no lookup was made; a dict (even empty)
    means it was, and a member with a `pr:` record absent from it is `unverified`."""
    items = parse_items(info)
    by_label = {i["label"]: i for i in items}
    for i in items:
        m = members.get(str(i["spec"])) if i["spec"] else None
        i["member"] = m
        if i["spec"] and str(i["spec"]) in (unreadable or ()):
            i["memberState"] = "unreadable"
        else:
            i["memberState"] = member_state(m, (pr_state or {}).get(str(i["spec"])),
                                           looked_up=pr_state is not None) \
                if i["spec"] else "missing"
        i["prState"] = (pr_state or {}).get(str(i["spec"]))
    for i in items:
        m, st = i["member"], i["memberState"]
        waiting = [d for d in i["after"]
                   if d not in by_label or by_label[d]["memberState"] != "done"]
        i["waitingOn"] = waiting
        if st != "open":
            i["status"] = st
        elif waiting:
            i["status"] = "waiting"
        elif m["frontmatter"].get("approved") or ready_report(m)["ok"]:
            i["status"] = "in-progress" if m["stage"] == "executing" else "ready"
        else:
            i["status"] = "not-ready"
        i["checked"] = st == "done"
    cycle = find_cycle(items)
    return {"items": items, "groups": _groups(info, items), "cycle": cycle,
            "criticalPath": [] if cycle else _critical_path(items, by_label),
            "ready": [i for i in items if i["status"] in ("ready", "in-progress")]}


# The old name stays the public one for backends and the portal; composition is the concept.
derive_composition = derive_epic


def _groups(info: dict, items: list[dict]) -> list[dict]:
    titles = group_titles(info)
    out = []
    for n in sorted({i["group"] for i in items}):
        its = [i for i in items if i["group"] == n]
        out.append({"group": n, "title": titles[n - 1] if 0 < n <= len(titles) else None,
                    "done": sum(1 for i in its if i["status"] == "done"),
                    "blocked": sum(1 for i in its if i["status"] == "blocked"),
                    "total": len(its)})
    return out


def _critical_path(items: list[dict], by_label: dict[str, dict]) -> list[str]:
    """The longest dependency chain of items not yet done, in execution order."""
    # The caller has ruled out cycles. Finish dependencies before their dependents,
    # keeping only each chain's length and predecessor rather than copying lists.
    memo: dict[str, tuple[int, str | None]] = {}
    longest: str | None = None
    longest_length = 0
    for i in items:
        if i["status"] == "done":
            continue
        label = i["label"]
        if label not in memo:
            stack = [label]
            frames = [iter(by_label[label]["after"])]
            while frames:
                for d in frames[-1]:
                    if d in by_label and by_label[d]["status"] != "done" and d not in memo:
                        stack.append(d)
                        frames.append(iter(by_label[d]["after"]))
                        break
                else:
                    current = stack.pop()
                    frames.pop()
                    best_length, predecessor = 0, None
                    for d in by_label[current]["after"]:
                        if d in by_label and by_label[d]["status"] != "done":
                            length = memo[d][0]
                            if length > best_length:
                                best_length, predecessor = length, d
                    memo[current] = (best_length + 1, predecessor)
        if memo[label][0] > longest_length:
            longest, longest_length = label, memo[label][0]

    path = []
    while longest is not None:
        path.append(longest)
        longest = memo[longest][1]
    path.reverse()
    return path


def presentation_level(info: dict) -> str:
    """`story`, `feature` or `epic` — a projection of the document's structure.

    A spec with no member is a story. A composite whose spec items sit in one wave (or in no
    `###` group at all) is a feature: the middle level, a compact group of stories and tasks.
    A composite whose spec items span two or more waves is an epic: each wave is a feature, and
    an epic is more than one of them. Never stored, never a work-item type."""
    items = parse_items(info)
    if not items:
        return "story"
    return "epic" if len({i["group"] for i in items}) > 1 else "feature"


def next_label(items: list[dict]) -> str:
    nums = [int(m.group(1)) for i in items if (m := _LABEL_NUM_RE.search(i["label"]))]
    return f"S{max(nums, default=0) + 1}"


def render_item(label: str, title: str, spec_id: str, after: list[str]) -> str:
    ref = spec_id if is_remote(spec_id) else f"#{spec_id}"
    line = f"- [ ] {label} {title} — spec: {ref}"
    return line + (f" — after: {','.join(after)}" if after else "")


def open_items(info: dict, members: dict[str, dict | None],
               pr_state: dict[str, str] | None = None,
               unreadable: set[str] | None = None) -> list[dict]:
    """Items that keep a composite from being archived as `done`. With `pr_state` (the
    caller's PR lookup) a member whose PR is not merged, or could not be read, stays open."""
    return [i for i in derive_epic(info, members, pr_state, unreadable)["items"]
            if i["status"] != "done"]


def composition_findings(info: dict, members: dict[str, dict | None],
                         unreadable: set[str] | None = None,
                         self_repo: str | None = None) -> list[dict]:
    """Finding rows for ONE composite: missing members, divergence, unknown dependencies,
    cycles, and the retired `epic:` key. A cross-repository member carries no back-reference,
    and a store that could not be read is a warning, never a missing member."""
    eid = str(info["id"])
    items = parse_items(info)
    rows: list[dict] = []

    def add(code, severity, message, **extra):
        rows.append({"code": code, "severity": severity, "message": message, "spec": eid,
                     **extra})

    if len(items) > MAX_ITEMS:
        add("sp-link-too-large", "warn",
            f"spec {eid} has {len(items)} items; a GitHub parent card carries at most "
            f"{MAX_ITEMS} sub-issues — split the composition")
    labels: set[str] = set()
    for i in items:
        if i["label"] in labels:
            add("sp-link-duplicate-label", "error",
                f"spec {eid}: label `{i['label']}` is used twice", label=i["label"])
        labels.add(i["label"])
        if i["writtenState"] != " ":
            add("sp-link-manual-tick", "warn",
                f"spec {eid}: item {i['label']} is written `[{i['writtenState']}]` — item "
                f"state is derived from the member; leave the box open", label=i["label"])
    seen: dict[str, str] = {}
    for i in items:
        if not i["spec"]:
            add("sp-link-item-unlinked", "error",
                f"spec {eid}: item {i['label']} has no `— spec: #<id>`", label=i["label"])
            continue
        if i["spec"] in seen:
            add("sp-link-duplicate-member", "error",
                f"spec {eid}: spec {i['spec']} is listed by {seen[i['spec']]} and "
                f"{i['label']}", member=i["spec"])
        seen[i["spec"]] = i["label"]
        for d in i["after"]:
            if d not in labels:
                add("sp-link-unknown-dependency", "error",
                    f"spec {eid}: item {i['label']} comes after `{d}`, which is not an item",
                    label=i["label"], dependency=d)
        if i["spec"] in (unreadable or ()):
            add("sp-link-member-unreadable", "warn",
                f"spec {eid}: item {i['label']} references {i['spec']}, whose store could not "
                f"be read", label=i["label"], member=i["spec"])
            continue
        m = members.get(i["spec"])
        if m is None:
            add("sp-link-missing-member", "error",
                f"spec {eid}: item {i['label']} references spec {i['spec']}, which does not "
                f"exist", label=i["label"], member=i["spec"])
            continue
    cycle = find_cycle(items)
    if cycle:
        add("sp-link-cycle", "error",
            f"spec {eid}: dependency cycle {' -> '.join(cycle)}", cycle=cycle)
    return rows


# The old name stays callable for the portal and any third-party caller.
epic_findings = composition_findings


def deprecation_findings(info: dict) -> list[dict]:
    """The retired back-reference: a `epic:` key on a member is ignored by derivation and
    reported once, as a warning, so the document is not left carrying a second truth."""
    ref = epic_ref(info["frontmatter"])
    if not ref:
        return []
    sid = str(info["id"])
    return [{"code": "sp-epic-key-deprecated", "severity": "warn",
             "message": f"spec {sid} carries `epic: {ref}` — composition is read from the "
                        f"composite's ## Tasks; the back-reference is ignored",
             "spec": sid, "remedy": "remove the `epic:` key"}]


# The old name stays callable for the sweep of an older caller.
claim_findings = deprecation_findings
