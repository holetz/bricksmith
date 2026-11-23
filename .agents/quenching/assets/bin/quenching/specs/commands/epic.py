"""The epic verbs — `epic add`, `status --epic` and `next --epic`.

An epic is a spec whose `## Tasks` items reference member specs (`parse/epics.py` owns the
grammar and the derivation, which is pure). This module is only the backend plumbing: read the
epic and its members in ONE `read_specs` call, hand them to the derivation, print the answer.
Nothing here is written back on a read, so a member's state is never stored twice."""
from __future__ import annotations

import json
import re
import os
import subprocess

from quenching.common.config import detect_provider
from quenching.common.tools import spawn_argv
from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter, front_fields
from quenching.specs.members import origin_url, own_repo, read_members
from quenching.specs.parse.edit import upsert_section
from quenching.specs.parse.epics import (derive_epic, group_titles, local_key, member_key,
                                         next_label, parse_items, presentation_level,
                                         pr_number, render_item, split_ref)
from quenching.specs.parse.fields import scalar_break
from quenching.specs.parse.sections import parse_sections
from quenching.specs.parse.text import HEADING_RE, mask_comments, split_lines

LABEL_RE = re.compile(r"^[A-Za-z0-9][\w.-]*$")
GROUP_NUMBER_RE = re.compile(r"^\d+\.\s*")
# The item states `status --epic` lists as blocked, each with its reasons.
HELD = ("blocked", "dropped", "missing", "unmerged", "unverified", "unreadable")
# `az repos pr` `status` in `gh pr view`'s `state` vocabulary, so the derivation reads one.
AZURE_PR_STATE = {"completed": "MERGED", "active": "OPEN", "abandoned": "CLOSED"}


def _refuse(out: Emitter, args, code: str, message: str, **extra) -> int:
    return out.emit_err(args.json, {"code": code, "exit": 2, "message": message, **extra})


def _pr_target(member: dict) -> str:
    """What `gh pr view` is given: the recorded `pr.url` when it is a URL (it names the repository
    itself), else the bare number, which `gh` resolves against the working directory."""
    rec = member["frontmatter"].get("pr")
    url = str(rec.get("url", "")).strip() if isinstance(rec, dict) else ""
    return url if url.startswith(("http://", "https://")) else pr_number(member)


def _provider(root: str | None) -> str | None:
    """`github`, `azure-boards`, or None when the origin names neither host."""
    try:
        return detect_provider(root or os.getcwd())[0]
    except Exception:  # an unreadable origin keeps the GitHub route
        return None


def _pr_view(member: dict, provider: str | None, root: str | None,
             timeout: int, repo: str | None = None) -> tuple[str, str] | None:
    """`(state, head branch)` of the member's PR as its provider reports it, or None. Azure is read
    with `az repos pr show --detect`, which resolves the organization from `root`'s origin; a
    member of another repository's store is read with `gh pr view --repo <owner>/<repo>`."""
    if repo:
        argv = ["gh", "pr", "view", _pr_target(member), "--repo", repo,
                "--json", "state,headRefName"]
    elif provider == "azure-boards":
        argv = ["az", "repos", "pr", "show", f"--id={pr_number(member)}", "--detect=true",
                "--output=json"]
    else:
        argv = ["gh", "pr", "view", _pr_target(member), "--json", "state,headRefName"]
    try:
        r = subprocess.run(spawn_argv(argv), capture_output=True, text=True, timeout=timeout, cwd=root)
        got = json.loads(r.stdout) if r.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if not isinstance(got, dict):
        return None
    if provider == "azure-boards":
        state = AZURE_PR_STATE.get(str(got.get("status") or "").strip().lower(), "")
        head = str(got.get("sourceRefName") or "").removeprefix("refs/heads/")
    else:
        state, head = str(got.get("state") or "").strip(), got.get("headRefName")
    return (state, head) if state else None


def _merge_record_explains_the_head(member: dict, state: str) -> bool:
    """Whether a `branch.work` that no longer matches the PR's head is explained by the member's own
    `merge:` record: the PR is the one the record names and it is merged. `branch:` is write-once, so
    a branch renamed after the stamp keeps the old name on record; refusing the state there would
    hide a delivery the merge record itself proves."""
    pr = pr_number(member) or ""
    rec = member["frontmatter"].get("merge")
    named = str(rec.get("pr", "")).strip() if isinstance(rec, dict) else ""
    return bool(pr) and state == "MERGED" and named == pr


def pr_states(members: dict, root: str | None = None, timeout: int = 15) -> dict[str, str]:
    """`member id -> PR state` for every archived member carrying a `pr:` record, read from `root`
    (the repository `--root` names, not the process cwd) with `gh pr view`, or `az repos pr show`
    when the origin is Azure DevOps. A lookup that fails, or whose head is not the member's
    recorded `branch.work`, is left out, and the derivation then marks it `unverified` — unless the
    member's `merge:` record names that same merged PR, which is what a branch renamed after the
    write-once `branch:` stamp looks like. A member with no `branch.work` has nothing to
    contradict."""
    out: dict[str, str] = {}
    provider = None
    for sid, m in members.items():
        if not m or m["phase"] != "archive" or not pr_number(m):
            continue
        repo = split_ref(sid)[0]
        if repo is None:
            provider = provider or _provider(root) or "github"
        got = _pr_view(m, provider, root, timeout, repo)
        if got is None:
            continue
        rec = m["frontmatter"].get("branch")
        work = str(rec.get("work", "")).strip() if isinstance(rec, dict) else ""
        if work and got[1] != work and not _merge_record_explains_the_head(m, got[0]):
            continue
        out[str(sid)] = got[0]
    return out


def load_epic(backend, epic_id, args, out: Emitter, root: str | None = None):
    """`(info, derived, None)` for a readable spec, or `(None, None, exit_code)` after a
    refusal. Any spec may be a composite; a spec with no member derives an empty composition."""
    got = backend.read_specs([epic_id])
    info = got.get(str(epic_id))
    if info is None:
        return None, None, _refuse(out, args, "sp-unknown-id", f"no spec with id '{epic_id}'",
                                   id=epic_id)
    ids = [i["spec"] for i in parse_items(info) if i["spec"]]
    members, unreadable = read_members(backend, ids, root)
    return info, derive_epic(info, members, pr_states(members, root), unreadable), None


def _find_group(titles: list[str], wanted: str) -> int | None:
    want = wanted.strip().casefold()
    for n, title in enumerate(titles, 1):
        bare = GROUP_NUMBER_RE.sub("", title).casefold()
        if want in (title.casefold(), bare) or bare.startswith(want + " —"):
            return n
    return None


def _group_heading(group: str, number: int) -> str:
    """A group label that already opens with "N." is used as is; any other gets `number`."""
    return group if GROUP_NUMBER_RE.match(group) else f"{number}. {group}"


def insert_item(info: dict, line: str, group: str | None) -> str:
    """The epic's document with `line` added under `## Tasks` — after the last item of `group`
    (a new `### N.` heading when the group is new), else after the last item overall."""
    text = info["text"]
    sec = parse_sections(mask_comments(text)).get("Tasks")
    if sec is None:
        body = "## Tasks\n\n" + (f"### {_group_heading(group, 1)}\n" if group else "") + line + "\n"
        return upsert_section(info, "Tasks", body)[0]
    lines = split_lines(text, keepends=True)
    base = sec["lineno"] + 1
    end = base + len(sec["lines"])
    while end > base and not lines[end - 1].strip():
        end -= 1
    items, titles = parse_items(info), group_titles(info)
    add = [line + "\n"]
    pos = end
    if group is None:
        pos = items[-1]["blockEndLineno"] if items else end
    else:
        n = _find_group(titles, group)
        if n is None:
            heading = _group_heading(group, len(titles) + 1)
            add = [] if not lines[end - 1].strip() else ["\n"]
            add += [f"### {heading}\n", line + "\n"]
        else:
            mine = [i for i in items if i["group"] == n]
            if mine:
                pos = mine[-1]["blockEndLineno"]
            else:
                heads = [i for i in range(base, end)
                         if (m := HEADING_RE.match(lines[i])) and len(m.group(1)) == 3]
                pos = heads[n - 1] + 1
    if pos > 0 and not lines[pos - 1].endswith("\n"):
        lines[pos - 1] += "\n"
    lines[pos:pos] = add
    return "".join(lines)


def _without_item(old: dict, spec_id: str, args, out: Emitter, self_repo: str | None = None):
    """`(text, None)`: `old` without the item that lists `spec_id` (`text` is `None` when it lists
    none), or `(None, exit_code)` when another item waits on that item."""
    items = parse_items(old)
    mine = next((i for i in items if i["spec"] and local_key(i["spec"], self_repo) == spec_id),
                None)
    if mine is None:
        return None, None
    waiting = [i["label"] for i in items if i is not mine and mine["label"] in i["after"]]
    if waiting:
        return None, _refuse(out, args, "sp-epic-move-dependents",
                             f"{', '.join(waiting)} in epic {old['id']} wait on {mine['label']} — "
                             "resolve the dependency before moving it", waiting=waiting)
    lines = split_lines(old["text"], keepends=True)
    del lines[mine["lineno"]:mine["blockEndLineno"]]
    return "".join(lines), None


def cmd_epic(args, root: str, out: Emitter) -> int:
    """`link add|remove <composite> <spec>` — list a spec as a member, or take it out.

    `epic add` is the historical spelling of `link add`. Composition is a property of the
    document, not a type: the composite needs no `workItemType: epic`, a member may belong to
    several composites, and a composite may itself be a member. `--move` is accepted and
    ignored — there is no single parent to move out of."""
    action = getattr(args, "link_cmd", None) or getattr(args, "epic_cmd", None)
    if action not in ("add", "remove"):
        return _refuse(out, args, "sp-no-command",
                       "choose a link command: add or remove")
    if action == "remove":
        return _link_remove(args, root, out)
    return _link_add(args, root, out)


def _link_add(args, root: str, out: Emitter) -> int:
    # The group becomes a `### N.` heading line: a break would forge the lines after it.
    bad = scalar_break(args.group or "")
    if bad:
        return _refuse(out, args, "sp-bad-scalar",
                       f"--group is one line with no control character — got {bad}",
                       field="group", char=bad)
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    composite_id, spec_id = str(args.epic), member_key(str(args.spec))
    if split_ref(spec_id)[0]:
        spec_id = local_key(spec_id, own_repo(origin_url(root)))
    if composite_id == spec_id:
        return _refuse(out, args, "sp-link-self", "a spec cannot be its own member")
    composite, _, code = load_epic(backend, composite_id, args, out, root)
    if code is not None:
        return code
    got, unreadable = read_members(backend, [spec_id], root)
    if unreadable:
        return _refuse(out, args, "sp-link-member-unreadable",
                       f"the store holding {spec_id} could not be read", id=spec_id)
    member = got.get(spec_id)
    if member is None:
        return _refuse(out, args, "sp-unknown-id", f"no spec with id '{spec_id}'", id=spec_id)
    items = parse_items(composite)
    mine = own_repo(origin_url(root))
    existing = next((i for i in items
                     if i["spec"] and local_key(i["spec"], mine) == spec_id), None)
    if existing:
        out.emit(args.json, {"ok": True, "composite": composite_id, "spec": spec_id,
                             "label": existing["label"], "changed": False},
                 f"spec {composite_id}: {existing['label']} -> spec {spec_id} (already linked)")
        return 0
    after = [a.strip() for a in (args.after or "").split(",") if a.strip()]
    known = {i["label"] for i in items}
    label = args.label or next_label(items)
    if not LABEL_RE.match(label) or label in known:
        return _refuse(out, args, "sp-link-bad-label",
                       f"label '{label}' is used or not a plain token")
    unknown = [a for a in after if a not in known]
    if unknown:
        return _refuse(out, args, "sp-link-unknown-dependency",
                       f"--after names no item of spec {composite_id}: "
                       f"{', '.join(unknown)}", unknown=unknown)
    title = str(member["frontmatter"].get("title") or "").strip() or f"Spec {spec_id}"
    text = insert_item(composite, render_item(label, title, spec_id, after), args.group)
    backend.write_spec(composite, text)
    out.emit(args.json, {"ok": True, "composite": composite_id, "spec": spec_id,
                         "label": label, "changed": True},
             f"spec {composite_id}: {label} -> spec {spec_id}")
    return 0


def _link_remove(args, root: str, out: Emitter) -> int:
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    composite_id, spec_id = str(args.epic), member_key(str(args.spec))
    if split_ref(spec_id)[0]:
        spec_id = local_key(spec_id, own_repo(origin_url(root)))
    composite, _, code = load_epic(backend, composite_id, args, out, root)
    if code is not None:
        return code
    text, code = _without_item(composite, spec_id, args, out, own_repo(origin_url(root)))
    if code is not None:
        return code
    if text is None:
        out.emit(args.json, {"ok": True, "composite": composite_id, "spec": spec_id,
                             "changed": False},
                 f"spec {composite_id}: spec {spec_id} is not listed")
        return 0
    backend.write_spec(composite, text)
    out.emit(args.json, {"ok": True, "composite": composite_id, "spec": spec_id,
                         "changed": True},
             f"spec {composite_id}: removed spec {spec_id}")
    return 0


def _row(i: dict) -> dict:
    m = i["member"]
    return {"label": i["label"], "title": i["title"], "spec": i["spec"], "group": i["group"],
            "status": i["status"], "after": i["after"], "waitingOn": i["waitingOn"],
            "stage": m["stage"] if m else None}


def composition_view(info: dict, derived: dict) -> dict:
    """The derived block a composite's status carries: level, progress, waves, members, the
    held items with their reasons, and the critical path. Pure projection of `derived`."""
    items = derived["items"]
    done = sum(1 for i in items if i["status"] == "done")
    blocked = [{**_row(i), "reasons": [t["reason"] or t["text"] for t in i["member"]["tasks"]
                                       if t["blocked"]] if i["member"] else []}
               for i in items if i["status"] in HELD]
    for b, i in zip(blocked, [i for i in items if i["status"] in HELD]):
        if i["status"] == "unreadable":
            b["reasons"] = [f"the store of {split_ref(i['spec'])[0]} could not be read"]
        elif i["status"] == "unmerged":
            b["reasons"] = [f"PR #{pr_number(i['member'])} is {i['prState']}"]
        elif i["status"] == "unverified":
            b["reasons"] = [f"PR #{pr_number(i['member'])} state could not be read"]
    return {"level": presentation_level(info),
            "progress": {"done": done, "total": len(items), "blocked": len(blocked),
                         "ready": len(derived["ready"])},
            "groups": derived["groups"], "items": [_row(i) for i in items],
            "blocked": blocked, "criticalPath": derived["criticalPath"],
            "cycle": derived["cycle"]}


def cmd_epic_status(args, root: str, out: Emitter) -> int:
    """`status --epic <id>` — progress per group, what is blocked, and the critical path."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, d, code = load_epic(backend, args.epic, args, out, root)
    if code is not None:
        return code
    view = composition_view(info, d)
    blocked = view["blocked"]
    obj = {"ok": True, **front_fields(root), "id": info["id"],
           "title": info["frontmatter"].get("title", ""), "phase": info["phase"], **view}
    if args.json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
        return 0
    p = obj["progress"]
    print(f"spec {info['id']} ({obj['level']}) — {obj['title']}: {p['done']}/{p['total']} "
          f"done, {p['blocked']} blocked, {p['ready']} ready")
    for g in d["groups"]:
        name = g["title"] or "(no group)"
        print(f"  {name}: {g['done']}/{g['total']} done"
              + (f", {g['blocked']} blocked" if g["blocked"] else ""))
    for b in blocked:
        print(f"  [{b['status']}] {b['label']} spec {b['spec']}: {'; '.join(b['reasons'])}")
    if d["cycle"]:
        print(f"  cycle: {' -> '.join(d['cycle'])}")
    elif d["criticalPath"]:
        print(f"  critical path: {' -> '.join(d['criticalPath'])}")
    return 0


def cmd_next_epic(args, root: str, out: Emitter) -> int:
    """`next --epic <id> [--limit N]` — the ready set: dependencies done, member approved or
    ready, not blocked. In-flight members first, then the human's priority, then wave order."""
    from quenching.specs.commands.next import _priority_rank   # next imports this module
    limit = getattr(args, "limit", None)
    if limit is not None and limit < 1:
        return _refuse(out, args, "sp-bad-limit", "--limit takes a positive number")
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, d, code = load_epic(backend, args.epic, args, out, root)
    if code is not None:
        return code
    order = {i["label"]: n for n, i in enumerate(d["items"])}
    ready = sorted(d["ready"], key=lambda i: (
        i["status"] != "in-progress", _priority_rank(i["member"]["frontmatter"].get("priority"))[0],
        i["group"], order[i["label"]]))
    shown = ready[:limit] if limit else ready
    rows = [{**_row(i), "executing": i["status"] == "in-progress",
             "priority": i["member"]["frontmatter"].get("priority") or None} for i in shown]
    left = [i for i in d["items"] if i["status"] != "done"]
    obj = {"ok": True, "spec": info["id"], "level": presentation_level(info),
           "count": len(rows), "readyTotal": len(ready),
           "remaining": len(left), "finished": not left and bool(d["items"]),
           "cycle": d["cycle"], "ready": rows}
    if args.json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print(f"spec {info['id']}: nothing ready — {len(left)} item(s) remaining"
              if left else f"spec {info['id']}: every item is done")
        return 0
    print(f"spec {info['id']}: {len(rows)} of {len(ready)} ready to run")
    for r in rows:
        print(f"  {r['label']:<5} spec {r['spec']:<8} {r['title']}"
              + ("  (in progress)" if r["executing"] else ""))
    return 0
