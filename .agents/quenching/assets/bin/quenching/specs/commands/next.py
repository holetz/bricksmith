"""`next` — THE single next action, and the ranking behind `--front`.

The ranking lives here and nowhere else: four lexicographic factors, plus a live `plan/<id>-<handle>`
ref that outranks all four in both directions.

`--spec` has three live consumers — `/quenching-specs execute`, the gate bank of
`/quenching-specs develop`, and the conclude text tests. `--front` was kept
deliberately through a period with none, because the front's ordering logic exists nowhere else;
`--table` gave it two — `/quenching-specs status` prints it as its whole spec block, and
`/quenching-specs triage` prints it under `--order priority` as its closing ranking. Both quote the
rendering; neither re-sorts or re-tallies it."""
from __future__ import annotations

import datetime
import json

from quenching.common.dates import today
from quenching.common.git import _git
from quenching.specs.backends import open_backend
from quenching.specs.backends.base import SpecBackend
from quenching.specs.commands.epic import cmd_next_epic, pr_states
from quenching.specs.commands.output import Emitter, display_locator, front_fields, read_one
from quenching.specs.commands import promote
from quenching.specs.members import read_members
from quenching.specs.parse import derive_info, spec_handle
from quenching.specs.parse.derive import derive_stage
from quenching.specs.parse.epics import (derive_composition, has_links, parse_items,
                                         task_after_problems)
from quenching.specs.parse.records import spec_records
from quenching.specs.parse.sections import ready_report
from quenching.specs.parse.tasks import (_files_bad_annotation, operational_entries,
                                         task_progress)
from quenching.specs.schema import load_schema


CRITICALITY_RANK = {"critical": 0, "high": 1, "medium": 2, "normal": 2, "low": 3}


def _composition_row(i: dict) -> dict:
    """One member as `next` reports it: the label the document uses, the spec it names, the
    derived state and the labels it waits for."""
    return {"label": i["label"], "spec": i["spec"], "title": i["title"],
            "status": i["status"], "after": i["after"]}

# `sp-spec-stale`'s own threshold, per specs-align/conformance.md. Read here so the table
# reports the same age the sweep would raise a finding at.
STALE_DAYS = 90


def _state(tasks: dict, ready: bool, branch: dict, age: int) -> str | None:
    """The one thing worth saying about this spec beyond its stage — in the sweep's own
    vocabulary where a `sp-*` code covers it, and as the branch fact where none does.

    Ordered as `spec-driven.md` §The spec table declares the column: a blocked task or a
    finished checklist outranks where the work is sitting, because they are what a human
    acts on. `None` is a not-yet, never a defect."""
    if tasks["blocked"]:
        return "sp-spec-blocked"
    if tasks["total"] and tasks["checked"] == tasks["total"]:
        return "sp-spec-complete"
    if branch["current"]:
        return "on this branch"
    if branch["live"]:
        return f"in flight on {branch['work']}"
    # Staleness needs open work to mean anything: a spec nobody has started is not rotting,
    # it is waiting, and the ready gate says which of the two it is.
    if age >= STALE_DAYS and (ready or tasks["total"]):
        return f"sp-spec-stale ({age}d)"
    return None


def _priority_rank(rec) -> tuple[float, str]:
    """A spec's human-assigned urgency as one comparable number, plus how it was read.

    `level` is the field triage writes and the one that ranks; `criticality` is a coarse
    fallback so a partially-filled record still ranks instead of silently sorting last.
    No record at all sorts after every record — never before."""
    if not isinstance(rec, dict):
        return (float("inf"), "")
    lvl = str(rec.get("level", "")).strip()
    if lvl:
        try:
            return (float(lvl), f"priority level {lvl}")
        except ValueError:
            pass
    crit = str(rec.get("criticality", "")).strip().lower()
    if crit in CRITICALITY_RANK:
        return (float(CRITICALITY_RANK[crit]), f"criticality {crit}")
    return (float("inf"), "")


def _days_since(date: str) -> int:
    try:
        d = datetime.date.fromisoformat(date)
    except ValueError:
        return 0
    return max(0, (datetime.date.fromisoformat(today()) - d).days)


def _git_refs(root: str) -> tuple[set[str], str | None]:
    """Every local branch, and the one checked out. Two calls for the WHOLE front, never
    one per spec — ranking twenty specs must not cost forty subprocesses.

    No git, or no repo → an empty set and no current branch, which ranks exactly as today."""
    heads = {l.strip() for l in
             _git(root, "for-each-ref", "--format=%(refname:short)",
                  "refs/heads").splitlines() if l.strip()}
    current = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip() or None
    return heads, (current if current and current != "HEAD" else None)


def _work_ref(fm: dict, spec_id: str | int, title: str) -> str | None:
    """The branch this spec's work would live on, or `None` when it never left the base.

    The `branch:` record when one was stamped, else the default `plan/<id>-<handle>` — because a
    human may have cut the branch by hand, with no record at all. The record alone is NEVER
    the signal: what counts is whether the ref is alive.

    `work == base` is the ONE case where the record is the whole signal, and it is why this
    returns `None` rather than the base's own name: `execute` stamps that pair when the human
    declined isolation (`git.md` §Where a branch comes from), and the base branch is always
    alive. Read as a work ref it would make every spec built in place rank as permanently in
    flight — `live` and `current` both true forever — on a ref it never took."""
    default = f"plan/{spec_handle(spec_id, title)}"
    rec = fm.get("branch")
    if not isinstance(rec, dict):
        return default
    work = str(rec.get("work", "")).strip()
    if work and work == str(rec.get("base", "")).strip():
        return None
    return work or default


def _candidate(backend: SpecBackend, s: dict, schema: dict, heads: set[str],
               current: str | None, root: str) -> dict:
    # ASKED OF THE BACKEND, never of the path. Against GitHub the locator is an issue URL, so
    # every candidate derived from an EMPTY document — the whole front ranked as `captured`
    # with no title, no tasks and nothing executing, and `--front` handed out its
    # single next action from exactly that.
    info, rerr = backend.read_spec(s["id"])
    unreadable = (rerr or {}).get("code")
    if info is None:
        # An unreadable native ID still survives, derived from an
        # empty document exactly as `list` keeps its row: dropping it would hide a spec from
        # the ranking, and `unreadable` says why it ranks as an empty one.
        info = derive_info(s, "")
    fm, sections, tasks = info["frontmatter"], info["sections"], info["tasks"]
    checked, blocked, total = task_progress(tasks)
    stage = derive_stage(s, sections, fm, tasks, schema)
    ready = ready_report({"sections": sections}, schema)
    prank, pwhy = _priority_rank(fm.get("priority"))
    progress = (checked / total) if total else 0.0
    executing = stage == "executing"
    title = str(fm.get("title") or s.get("title") or "").strip()
    work = _work_ref(fm, s["id"], title)
    live = work is not None and work in heads
    # A record whose ref is gone stops counting: the branch was merged or deleted, so the
    # spec is no more "in flight" than one that never had a branch at all. A spec built in
    # place (`work` is `None` above) never counts either, for the same reason read the other
    # way round — its ref cannot go, so nothing would ever stop it counting.
    on_it = live and work == current
    branch_rank = 0 if on_it else (2 if live else 1)
    tasks = {"checked": checked, "blocked": blocked, "total": total}
    branch = {"work": work, "live": live, "current": on_it}
    age = _days_since(info["date"])
    return {
        "id": s["id"], "folder": s.get("folder", s.get("phase", "")),
        "date": info["date"],
        "title": title, "stage": stage,
        "tasks": tasks,
        "progress": round(progress, 3),
        "readyGateMet": ready["ok"],
        "approved": fm.get("approved") or None,
        "priority": fm.get("priority") or None,
        # `spec_records` rather than a second enumeration, so this listing and `cq specs list`
        # cannot disagree about which records a spec carries.
        "records": spec_records(fm),
        "ageDays": age,
        "state": _state(tasks, ready["ok"], branch, age),
        "unreadable": unreadable,
        "branch": branch,
        "path": display_locator(s["path"], root),
        "_key": (branch_rank, 0 if executing else 1, -progress, prank,
                 info["date"], str(s["id"])),
        "_why": pwhy,
        "_executing": executing,
        "_info": info,
    }


def _rank_reason(c: dict) -> str:
    """Why this candidate sits where it does — the ONE dominant factor, not a formula."""
    t = c["tasks"]
    if c["branch"]["current"]:
        return f"you are on this branch ({c['branch']['work']})"
    if c["branch"]["live"]:
        return f"in flight on `{c['branch']['work']}` — check it out to continue"
    if c["_executing"]:
        r = f"executing — {t['checked']}/{t['total']} tasks done"
        if t["blocked"]:
            r += f", {t['blocked']} blocked"
        return r
    if c["_why"]:
        return f"{c['_why']} — {c['stage']}"
    if c["readyGateMet"]:
        return f"ready to build, untouched for {c['ageDays']}d"
    return f"{c['stage']}, {c['ageDays']}d old"


# `spec-driven.md` §The spec table's ordered column set.
# A caller OMITS columns with `--columns`; it never reorders them and never invents one, which
# is why the order lives here and not in the flag.
# The `Summary` column is retained as the table's established label, but reads the native title
# directly now that title is the sole short description a spec carries.
TABLE_COLUMNS = ["spec", "summary", "stage", "tasks", "priority", "complexity",
                 "records", "age", "state"]
SUMMARY_WIDTH = 120


def _cell(value: str | None) -> str:
    """One markdown cell. `—` is a not-yet, never a defect."""
    if not value:
        return "—"
    return str(value).replace("\n", " ").replace("|", r"\|").strip()


def _table_row(c: dict, recommended: bool) -> dict:
    """A candidate as the table's cells, with the Summary column sourced from `title`.

    THE COLUMN KEPT ITS NAME AND CHANGED ITS SOURCE. `summary:` was written on 15 of 154
    specs and the column already fell back to the title on the other 91%; the title is
    native, comes free in every listing, and is the one string a human already reads the
    spec by. There is no fallback left to report, which is why this returns the cells alone
    where it used to return `(cells, fellback)` — `optional-payload-fields.md` §Retired
    fields stay retired owns the rule that keeps `summary` out of the payload beside it."""
    t, p = c["tasks"], (c["priority"] or {})
    tasks = f"{t['checked']}/{t['total']}" if t["total"] else ""
    if t["blocked"]:
        tasks += f" · {t['blocked']} blocked"
    level, crit = str(p.get("level", "")).strip(), str(p.get("criticality", "")).strip()
    summary = c["title"]
    if len(summary) > SUMMARY_WIDTH:
        summary = summary[:SUMMARY_WIDTH - 1].rstrip() + "…"
    return {
        "spec": ("→ " if recommended else "") + str(c["id"]),
        "summary": summary,
        "stage": c["stage"],
        "tasks": tasks,
        "priority": " · ".join(x for x in (level, crit) if x),
        "complexity": str(p.get("complexity", "")).strip(),
        "records": ", ".join(k for k, v in (c["records"] or {}).items() if v),
        "age": f"{c['ageDays']}d",
        "state": c["state"],
    }


def _print_table(ranked: list[dict], columns: list[str]) -> None:
    rows = [_table_row(c, i == 0) for i, c in enumerate(ranked)]
    heads = [col.capitalize() for col in columns]
    print("| " + " | ".join(heads) + " |")
    print("| " + " | ".join("---" for _ in columns) + " |")
    for cells in rows:
        print("| " + " | ".join(_cell(cells[col]) for col in columns) + " |")


def _resolve_columns(requested: str | None) -> tuple[list[str], list[str]]:
    """The columns to print, and any name that is not one — `spec-driven.md` §The spec table
    says a command OMITS columns and never reorders them, so the declared order always wins
    over the order they were typed."""
    if not requested:
        return list(TABLE_COLUMNS), []
    asked = [c.strip().lower() for c in requested.split(",") if c.strip()]
    unknown = [c for c in asked if c not in TABLE_COLUMNS]
    if unknown:
        return [], unknown
    return [c for c in TABLE_COLUMNS if c in asked], []


def _order_key(order: str):
    """`rank` is the four-factor ordering this module owns; `priority` is the human's ranking
    alone, which is what a triage table proposes against. Nothing else ranks a front."""
    if order == "priority":
        return lambda c: (_priority_rank(c["priority"])[0], c["date"], str(c["id"]))
    return lambda c: c["_key"]


def _next_front(args, root: str, out: Emitter) -> int:
    """The ranked candidate list — THE only place ranking logic lives.

    Four factors, lexicographic and in this order:
      1. executing first     finish what is already started before opening something new
      2. closest to done     among those, the one nearest the end
      3. priority            the human's ranking, when triage has written one
      4. age                 oldest first, so nothing rots quietly

    Factor 2 is harmless for everything else: a spec with no ticked task scores 0, so the
    whole non-executing set ties there and falls through to priority — which is exactly the
    intent, without a special case.

    A LIVE `plan/<id>-<handle>` ref outranks all four, in both directions: the branch you are
    standing on goes to the top, and one alive but not checked out is demoted below the
    untouched specs — offering it would send a second run at work already under way
    somewhere else. The signal is the ref, never the `branch:` record: a human may cut a
    branch with no record, and a record outlives the branch it names."""
    table = getattr(args, "table", False)
    if table and args.json:
        # A table IS the human rendering. Emitting both would put the same ranking on two
        # surfaces that can drift, which is the defect `--table` exists to remove.
        return out.emit_err(args.json, {
            "code": "sp-table-not-json", "exit": 2,
            "message": "--table is the human rendering; drop --json for the table, "
                       "or drop --table for the payload"})
    columns, unknown = _resolve_columns(getattr(args, "columns", None) if table else None)
    if unknown:
        return out.emit_err(args.json, {
            "code": "sp-unknown-column", "exit": 2,
            "message": f"unknown column(s) {', '.join(unknown)} — the set is "
                       f"{', '.join(TABLE_COLUMNS)}"})

    schema = load_schema()
    heads, current = _git_refs(root)
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    cands = [_candidate(backend, s, schema, heads, current, root)
             for s in backend.list_specs("plans")]
    cands.sort(key=_order_key(getattr(args, "order", "rank")))
    ranked = []
    merged_not_archived = []
    for c in cands:
        c = dict(c)
        info = c.pop("_info")
        if promote.isolated(info):
            merged, reason = promote.merge_evidence(info, root, fetch=False, ask_pr=False)
            if merged:
                merged_not_archived.append({"id": c["id"], "title": c["title"],
                                            "reason": reason})
        c["reason"] = _rank_reason(c)
        for k in ("_key", "_why", "_executing"):
            c.pop(k)
        ranked.append(c)

    # When nothing carries a priority record and nothing is in flight, the order is age
    # alone — which is an ordering, not a judgment. Say so rather than implying a ranking
    # that was never made.
    prioritized = [c for c in ranked if c["priority"]]
    # A live branch IS in flight, whether or not any task has been ticked yet — and it has
    # already reordered the list, so claiming the order is age alone would be false.
    in_flight = [c for c in ranked if c["stage"] == "executing" or c["branch"]["live"]]
    needs_triage = bool(ranked) and not prioritized and not in_flight and len(ranked) > 1

    obj = {"ok": True, **front_fields(root), "count": len(ranked),
           "top": ranked[0]["id"] if ranked else None,
           "needsTriage": needs_triage,
           "mergedNotArchived": merged_not_archived,
           "candidates": ranked}
    if args.json:
        print(json.dumps(obj, indent=2, ensure_ascii=False))
        return 0
    if not ranked:
        print(f"no active specs under {root} — nothing to continue")
        return 0
    order = getattr(args, "order", "rank")
    print(f"specs front — {len(ranked)} active, ranked by {order}")
    if table:
        _print_table(ranked, columns)
    else:
        for i, c in enumerate(ranked, 1):
            print(f"  {i}. {str(c['id']):<32} {c['reason']}")
    if needs_triage:
        print("\n  nothing is in flight and nothing carries a priority record — this order "
              "is age alone.\n  `cq specs`-driven triage would give it something to stand on.")
    if merged_not_archived:
        print(f"\n  {len(merged_not_archived)} merged but not archived — run "
              "`cq specs reconcile --write`")
    return 0


def cmd_next(args, root: str, out: Emitter) -> int:
    """THE single next action, so a skill never infers state from prose.

    In `plans/` the ladder is one chain, because the folder no longer splits it: fill the
    ready gate, then work the tasks, then close out. A `[!]` task is SKIPPED — it already
    has an honest reason recorded and re-offering it forever is what the attempt budget
    was clumsily trying to prevent.

    Reaching the ready gate does NOT stop the ladder to demand approval. Refusing here
    would rebuild the `promote` this fold removed; `execute` asks for the stamp inline,
    and `approved` rides along in the payload so it can.

    With `--front` the question is the other one — WHICH spec — and that is answered by
    `_next_front`."""
    if getattr(args, "epic", None):
        if args.front or args.spec:
            return out.emit_err(args.json, {
                "code": "sp-epic-conflict", "exit": 2,
                "message": "--epic is its own question; drop --spec/--front"})
        return cmd_next_epic(args, root, out)
    if args.front:
        return _next_front(args, root, out)
    if not args.spec:
        out.emit(args.json, {"ok": False, "code": "sp-no-target",
                             "message": "pass --spec <id> for one spec's next action, "
                                        "or --front for the ranked candidate list"},
                 "error: pass --spec <id>, or --front")
        return 1
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    base = {"id": info["id"], "phase": info["phase"], "folder": info["folder"],
            "stage": info["stage"], "verification": info["verification"],
            "approved": info["frontmatter"].get("approved") or None,
            "blocked": [{"id": t["id"], "text": t["text"], "reason": t["reason"]}
                        for t in info["tasks"] if t["blocked"]]}

    if info["phase"] == "plans":
        ready = ready_report(info)
        if not ready["ok"]:
            want = (ready["missing"] + ready["malformed"])[0]
            remaining = len(ready["missing"]) + len(ready["malformed"]) - 1
            obj = {"ok": True, "action": "write_section", "heading": want, **base,
                   "missing": ready["missing"], "malformed": ready["malformed"],
                   "message": f"write ## {want}"
                              + (f" (then {remaining} more)" if remaining else "")
                              + " to reach the ready gate"}
            out.emit(args.json, obj, obj["message"])
            return 0
        # A composite needs its member states before deciding which tasks are ready: a task
        # may wait `after:` a spec item (`- [ ] 2.1 Wire — after: S2`).
        derived = None
        if has_links(info):
            ids = [i["spec"] for i in parse_items(info) if i["spec"]]
            members, unreadable = read_members(backend, ids, root)
            derived = derive_composition(info, members, pr_states(members, root), unreadable)
        done = {t["id"] for t in info["tasks"] if t["checked"]}
        done |= {i["label"] for i in (derived["items"] if derived else [])
                 if i["status"] == "done"}
        openable = [t for t in info["tasks"]
                    if not t["checked"] and not t["blocked"]
                    and all(d in done for d in t.get("after") or [])]
        if openable:
            t = openable[0]
            bad = [e for e in t["files"] if _files_bad_annotation(e)]
            if bad:
                # The consumer of `files:` is an executor who cannot tell an invented
                # piece from a path the task will create — refuse HERE, at the door,
                # never hand the list over.
                return out.emit_err(args.json, {
                    "code": "sp-files-annotation",
                    "message": f"task {t['id']} declares files entries that are not "
                               f"paths: {', '.join(repr(b) for b in bad)} — remove the "
                               f"comment; `(new)` and `(operational)` are the only "
                               f"reserved `files:` annotations"})
            mixed = [] if t["operational"] else operational_entries(t["files"])
            if mixed:
                # `(operational)` says the task writes no repository file. Beside a path it
                # would reach the executor as an entry that is not one — the same silence
                # the guard above closes, refused at the same door.
                return out.emit_err(args.json, {
                    "code": "sp-files-operational",
                    "message": f"task {t['id']} declares `(operational)` beside "
                               f"{len(t['files']) - len(mixed)} path(s) — the annotation is "
                               f"exclusive; keep it as the whole `files:`"})
            obj = {"ok": True, "action": "implement_task", "task": t["id"], "text": t["text"],
                   "verify": t["verify"], "files": t["files"], "pattern": t["pattern"],
                   "cwd": t["cwd"], "operational": t["operational"],
                   "parallel": t["parallel"], **base,
                   "message": f"implement task {t['id']}: {t['text']}"}
            out.emit(args.json, obj, obj["message"])
            return 0
        if derived is not None:
            # The document's own tasks are done (or there were none), so the composition is
            # the next question: a ready member goes to the orchestrator, everything else is
            # reported with the member state that holds it.
            ready = derived["ready"]
            remaining = [i for i in derived["items"] if i["status"] != "done"]
            if ready:
                obj = {"ok": True, "action": "orchestrate", **base,
                       "ready": [_composition_row(i) for i in ready],
                       "remaining": len(remaining),
                       "message": f"composition — {len(ready)} member spec(s) ready to run"}
                out.emit(args.json, obj, obj["message"])
                return 0
            if remaining:
                obj = {"ok": True, "action": "blocked", **base,
                       "members": [_composition_row(i) for i in remaining],
                       "message": f"composition — no member ready; {len(remaining)} still "
                                  f"open"}
                out.emit(args.json, obj, obj["message"] + "".join(
                    f"\n  [{i['status']}] {i['label']} spec {i['spec']}" for i in remaining))
                return 0
            obj = {"ok": True, "action": "promote", "to": "archive", **base,
                   "message": "every task and every member is done — write ## Outcome and "
                              "open the PR; after the merge, `cq specs reconcile --write` "
                              "archives it"}
            out.emit(args.json, obj, obj["message"])
            return 0
        waiting = [t for t in info["tasks"]
                   if not t["checked"] and not t["blocked"] and t not in openable]
        if base["blocked"] or waiting:
            # A label nothing carries, or a loop, is never satisfied: name it beside the row so
            # the wait points at the bad line instead of looking like work in flight.
            bad = task_after_problems(info)
            cyclic = set(bad["cycle"] or [])
            rows = [{"id": t["id"], "text": t["text"], "after": t["after"],
                     "unknown": [u["label"] for u in bad["unknown"]
                                 if u["task"] == (t["id"] or t["index"])],
                     "cycle": t["id"] in cyclic}
                    for t in waiting]
            hint = (f" — an `after:` can never be met; run `cq specs validate --spec "
                    f"{info['id']}` (sp-after-unknown, sp-after-cycle)"
                    if any(r["unknown"] or r["cycle"] for r in rows) else "")
            obj = {"ok": True, "action": "blocked", **base, "waiting": rows,
                   "message": f"every remaining task is blocked or waiting "
                              f"({len(base['blocked'])} blocked, {len(waiting)} waiting)"
                              + hint}
            out.emit(args.json, obj, obj["message"] + "".join(
                f"\n  [{t['state']}] {t['text']}" for t in waiting)
                + "".join(f"\n  [!] {b['text']}" for b in base["blocked"]))
            return 0
        obj = {"ok": True, "action": "promote", "to": "archive", **base,
               "message": "all tasks complete — write ## Outcome and open the PR; after "
                          "the merge, `cq specs reconcile --write` archives it"}
        out.emit(args.json, obj, obj["message"])
        return 0

    obj = {"ok": True, "action": "done", **base,
           "message": f"'{info['id']}' is archived ("
                      f"{info['frontmatter'].get('outcome', 'done')})"}
    out.emit(args.json, obj, obj["message"])
    return 0
