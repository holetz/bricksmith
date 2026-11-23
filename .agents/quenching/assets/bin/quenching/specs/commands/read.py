"""The report verbs — `list`, `status`, and the write-only `export` dump.

Three readers over the same derivation: the whole front by folder and derived stage, ONE spec's
sections/records/gates, and the canonical markdown written to disk for the day an external
backend is lost."""
from __future__ import annotations

import json
import os

from quenching.common.config import load_config
from quenching.common.io import write_text
from quenching.specs.backends import open_backend
from quenching.specs.commands.epic import cmd_epic_status, composition_view, pr_states
from quenching.specs.commands.output import Emitter, display_locator, front_fields, read_one
from quenching.specs.members import read_members
from quenching.specs.parse import PHASES, derive_info, spec_handle
from quenching.specs.parse.epics import (derive_composition, has_links, parse_items)
from quenching.specs.parse.records import spec_records
from quenching.specs.parse.sections import (gate_report, ready_report, section_state,
                                            stray_headings)
from quenching.specs.parse.spec import LEGACY_PHASES, PHASE_DIRS
from quenching.specs.parse.tasks import task_progress
from quenching.specs.schema import canonical_headings, load_schema


def epic_meta(row_id, fm: dict) -> dict:
    """`epic` and `workItemType` as stored in frontmatter; absent is `null`. `epic` takes the
    identifier type of the row's `id`."""
    epic = fm.get("epic")
    epic = None if epic in (None, "") else str(epic).strip().lstrip("#") or None
    if epic is not None and isinstance(row_id, int) and epic.isdigit():
        epic = int(epic)
    wit = fm.get("workItemType")
    return {"epic": epic, "workItemType": None if wit in (None, "") else str(wit)}


def cmd_list(args, root: str, out: Emitter) -> int:
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    phase = getattr(args, "phase", None)
    if getattr(args, "lean", False):
        rows = [{
            "id": s["id"], "title": s.get("title", ""), "phase": s["phase"],
            "state": s.get("state"), "path": display_locator(s["path"], root),
            "records": s.get("records", []),
            **epic_meta(s["id"], {"epic": s.get("epic"), "workItemType": s.get("workItemType")}),
        } for s in backend.list_specs(phase, lean=True)]
        unavailable = ["date", "stage", "outcome", "verification",
                       "ready", "sections", "tasks", "unreadable"]
        obj = {"ok": True, **front_fields(root), "lean": True, "count": len(rows),
               "specs": rows, "unavailable": unavailable}
        if args.json:
            print(json.dumps(obj, indent=2, ensure_ascii=False))
            return 0
        print(f"specs — {backend.name} lean front ({len(rows)})")
        for row in rows:
            records = ", ".join(row["records"]) or "—"
            print(f"  {row['id']}  {row['title']}  [{row['phase']} · {row['state']}]  "
                  f"records: {records}\n    {row['path']}")
        print(f"  unavailable: {', '.join(unavailable)}")
        return 0
    specs = backend.list_specs(phase)
    rows = []
    for s in specs:
        # ASKED OF THE BACKEND, never of the path. This was the last command reading
        # `read_text(s["path"])` directly, which worked only because the files backend's
        # locator happens to be a filesystem path — against GitHub it is an issue URL, and
        # `list` would have reported every spec as empty rather than failing.
        info, rerr = backend.read_spec(s["id"])
        if rerr or info is None:
            # NO REFUSAL IS REACHABLE HERE ANY MORE, and the row is kept anyway. The ID came
            # from the listing, so it cannot be unknown, and two specs cannot share a native
            # ID — which is what retired `sp-ambiguous-slug` by construction. What survives
            # is a transport failure between the listing and this read; the row is derived
            # from an empty document so every field still comes from the one shared
            # derivation, and `unreadable` says so rather than letting the spec look empty.
            info, unreadable = derive_info(s, ""), (rerr or {}).get("code")
        else:
            unreadable = None
        checked, blocked, total = task_progress(info["tasks"])
        rows.append({
            "id": s["id"], "phase": s["phase"], "folder": s["folder"],
            "legacy": s["legacy"], "date": info["date"],
            "title": info["frontmatter"].get("title", ""),
            "stage": info["stage"],
            "outcome": info["frontmatter"].get("outcome") or None,
            # The seven records, on every row. Without them a caller that wants the front's
            # rankings — `triage` reading `priority`, `status` narrating a spec's history —
            # has to open each file itself, which is a path read and so only works while the
            # backend happens to be `files`.
            "records": spec_records(info["frontmatter"]),
            "unreadable": unreadable,
            "tasks": {"checked": checked, "blocked": blocked, "total": total},
            "path": display_locator(s["path"], root),
            **epic_meta(s["id"], info["frontmatter"]),
        })
    if args.json:
        print(json.dumps({"ok": True, **front_fields(root), "count": len(rows), "specs": rows},
                         indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print(f"no specs on the {backend.name} front")
        return 0
    print(f"specs — {backend.name} front ({len(rows)})")
    for folder in PHASE_DIRS:
        group = [r for r in rows if r["folder"] == folder]
        if not group:
            continue
        legacy = " (v2 — `cq specs migrate` folds it into plans/)" \
            if folder in LEGACY_PHASES else ""
        print(f"\n  {folder}/{legacy}")
        for r in group:
            prog = (f"  {r['tasks']['checked']}/{r['tasks']['total']}"
                    if r["tasks"]["total"] else "")
            blk = f" · {r['tasks']['blocked']} blocked" if r["tasks"]["blocked"] else ""
            oc = f" · {r['outcome']}" if r["outcome"] else ""
            un = f" · {r['unreadable']} (nothing derived)" if r["unreadable"] else ""
            print(f"    {r['date']}  {str(r['id']):<8} {r['title'][:44]:<44} "
                  f"[{r['stage']}]{prog}{blk}{oc}{un}")
    return 0


def _next_phase(phase: str, schema: dict | None = None) -> str | None:
    seq = (schema or load_schema()).get("promote", {}).get("sequence", list(PHASES))
    return seq[seq.index(phase) + 1] if phase in seq and seq.index(phase) + 1 < len(seq) else None


def cmd_status(args, root: str, out: Emitter) -> int:
    if getattr(args, "epic", None):
        if args.spec:
            return out.emit_err(args.json, {"code": "sp-epic-conflict", "exit": 2,
                                            "message": "pass --epic or --spec, not both"})
        return cmd_epic_status(args, root, out)
    if not args.spec:
        return out.emit_err(args.json, {"code": "sp-no-target", "exit": 2,
                                        "message": "pass --spec <id>, or --epic <id>"})
    no_fetch = getattr(args, "no_fetch", False)
    if no_fetch:
        # Decided from the local config alone: opening a github backend resolves the repo through
        # the API and writes its cache, and `--no-fetch` promises neither.
        cfg = load_config(root)
        declared_git = cfg.get("data", {}).get("backend") == "git"
        if not declared_git and not cfg.get("unknownBackend") \
                and cfg.get("provider") in ("github", "azure-boards"):
            return out.emit_err(args.json, {
                "code": "sp-no-local-read", "exit": 2,
                "message": f"--no-fetch needs the git store; the '{cfg['provider']}' backend has no "
                           "local copy to read, so it would have to use the network"})
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    if no_fetch and not backend.has_local_ref():
        return out.emit_err(args.json, {
            "code": "sp-no-local-ref", "exit": 2,
            "message": f"--no-fetch reads the local ref '{backend.tracking_ref}', which does not exist "
                       "here; run a command without --no-fetch once to fetch the store"})
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    comp = None
    if has_links(info):
        # `status` answers the composition question when the document asks it, without
        # replacing the task/records half: a composite is also a spec that is developed,
        # approved and executed, and its consumers read those fields here.
        ids = [i["spec"] for i in parse_items(info) if i["spec"]]
        members_state, unreadable = read_members(backend, ids, root)
        comp = composition_view(info, derive_composition(
            info, members_state, pr_states(members_state, root), unreadable))
    checked, blocked, total = task_progress(info["tasks"])
    dest = _next_phase(info["phase"])
    gates = gate_report(info, dest) if dest else None
    # `gate_report` measures SECTIONS — the entry gate of the destination folder — and `promote`
    # applies a second condition on top of it that lives nowhere else: archiving with `done` refuses
    # while any task is unticked, blocked `[!]` included. Reported as sections alone, this block said
    # `ok: true` for a spec `promote --outcome done` then refused (exit 2), and this command's own
    # description calls itself "an honest dry run before a sweep is authorized" — so the dry run has
    # to carry the same condition the real verb does. `openTasks` is the same shape `promote`'s
    # refusal payload emits, so a caller reads one vocabulary for both.
    if gates and dest == "archive":
        gates["openTasks"] = [{"id": t["id"], "text": t["text"], "state": t["state"]}
                              for t in info["tasks"] if not t["checked"]]
        gates["ok"] = gates["ok"] and not gates["openTasks"]
    ready = ready_report(info) if info["phase"] == "plans" else None
    sections = [{"heading": h, "state": section_state(info["sections"], h)}
                for h in canonical_headings()]
    records = spec_records(info["frontmatter"])
    obj = {
        "ok": True, "id": info["id"], "title": info["frontmatter"].get("title", ""),
        # The one line that answers "what is this spec" without opening it.
        "phase": info["phase"], "folder": info["folder"], "legacy": info["legacy"],
        "stage": info["stage"],
        "date": info["date"], "verification": info["verification"],
        # every human-judgment record in one place and in schema order, so `conclude` and
        # `continue` read state instead of re-parsing the file
        "records": records,
        "ready": ready,
        "sections": sections,
        "strays": stray_headings(info["sections"]),
        "tasks": {"checked": checked, "blocked": blocked, "total": total,
                  "blockedTasks": [{"id": t["id"], "text": t["text"], "reason": t["reason"]}
                                   for t in info["tasks"] if t["blocked"]],
                  "subjects": [{"id": t["id"], "subject": t["subject"]}
                               for t in info["tasks"] if t["subject"]],
                  "commits": [{"id": t["id"], "commit": t["commit"]}
                              for t in info["tasks"] if t["commit"]]},
        "promote": gates,
        "path": display_locator(info["path"], root),
        "pending": _pending(backend),
    }
    if comp:
        obj["level"] = comp.pop("level")
        obj["composition"] = comp
    if args.json:
        print(json.dumps(out.announced(obj), indent=2, ensure_ascii=False))
        return 0
    print(out.receipt_line(), end="")
    print(f"{info['id']} — {obj['title']}")
    print(f"  {obj['path']}  [{info['stage']}]  "
          f"verification: {info['verification']}")
    if total:
        print(f"  tasks: {checked}/{total} complete" +
              (f" · {blocked} blocked" if blocked else ""))
    if comp:
        p = comp["progress"]
        print(f"  composition: {obj['level']} · {p['done']}/{p['total']} member(s) done"
              + (f" · {p['ready']} ready" if p["ready"] else "")
              + (f" · {p['blocked']} held" if p["blocked"] else ""))
        for g in comp["groups"]:
            print(f"    {g['title'] or '(no group)'}: {g['done']}/{g['total']} done")
        for b in comp["blocked"]:
            print(f"    [{b['status']}] {b['label']} spec {b['spec']}: {'; '.join(b['reasons'])}")
    for s in sections:
        mark = {"filled": "✓", "empty": "!", "absent": "·"}[s["state"]]
        print(f"    {mark} ## {s['heading']}")
    if obj["strays"]:
        print(f"  strays: {', '.join(obj['strays'])}")
    if ready:
        if not ready["ok"]:
            print(f"  ready gate: missing {', '.join(ready['missing'] + ready['malformed'])}")
        elif not records.get("approved"):
            print("  ready gate: met — not approved (execute stamps `approved:` inline)")
        else:
            print(f"  ready gate: met · approved {records['approved']}")
    held = [(k, v) for k, v in records.items() if v]
    if held:
        print("  records:")
        for k, v in held:
            if isinstance(v, dict):
                v = ", ".join(f"{kk}: {vv}" for kk, vv in v.items())
            print(f"    {k + ':':<10} {v}")
    if obj["pending"]:
        artifacts = len(obj["pending"]["artifacts"])
        counted = f"{len(obj['pending']['specs'])} spec(s)"
        if artifacts:
            counted += f" and {artifacts} artifact(s)"
        print(f"  pending: {counted} with unsent local writes since "
              f"{obj['pending']['since']} — `cq specs push` sends them")
    if obj["tasks"]["subjects"]:
        print(f"  subjects: {len(obj['tasks']['subjects'])} task(s) carry one")
    if obj["tasks"]["commits"]:
        print(f"  commits: {len(obj['tasks']['commits'])} task(s) carry an older sha")
    if gates:
        if gates["ok"]:
            print(f"  promote → {dest}/: ready")
        else:
            if gates["missing"]:
                print(f"  promote → {dest}/: missing {', '.join(gates['missing'])}")
            if gates["malformed"]:
                print(f"  promote → {dest}/: empty (malformed) {', '.join(gates['malformed'])}")
            if gates.get("openTasks"):
                print(f"  promote → {dest}/: {len(gates['openTasks'])} of {total} tasks still "
                      f"open — `--outcome done` refuses, `--outcome abandoned` does not")
    return 0


def _pending(backend) -> dict | None:
    """The git store's unsent local writes, `{specs, artifacts, since}`, or None (nothing
    pending, or a tracker backend)."""
    state = getattr(backend, "pending_state", lambda: None)()
    return ({"specs": state["specs"], "artifacts": state.get("artifacts") or [],
             "since": state["since"]} if state else None)


def cmd_export(args, root: str, out: Emitter) -> int:
    """Dump the canonical markdown of one spec, or every spec, to disk — write-only.

    THE MITIGATION `## Risks` NAMES FOR LOSING AN EXTERNAL BACKEND, AND NOTHING MORE. Nothing
    in this tool reads the dump back and nothing keeps it in sync with the backend, so it is
    never a second store — a stale copy on disk cannot silently outrank the backend the way a
    cache could. `info["text"]` is the same canonical document every other command derives
    from and shows under `show --full`; this command only adds the write to disk."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    ids = [s["id"] for s in backend.list_specs()] if args.all else [args.spec]
    written = []
    for spec_id in ids:
        info, rerr = backend.read_spec(spec_id)
        if rerr:
            return out.emit_err(args.json, rerr)
        # `<id>-<handle>.md`, the same cosmetic name `next` gives the branch. The ID leads so
        # the dump sorts and resolves the way the backend does; the handle rides along so a
        # directory of 157 files reads as titles rather than as numbers. Nothing reads this
        # name back — the dump is a mitigation, never a second store.
        name = spec_handle(info["id"], str(info["frontmatter"].get("title") or ""))
        dest = os.path.join(args.out, info["folder"], f"{name}.md")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        write_text(dest, info["text"])
        written.append(dest)
    obj = {"ok": True, "out": args.out, "count": len(written), "files": written}
    human = f"exported {len(written)} spec(s) to {args.out}/\n" + \
            "\n".join(f"  {w}" for w in written)
    out.emit(args.json, obj, human)
    return 0
