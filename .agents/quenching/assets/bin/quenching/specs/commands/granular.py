"""Granular reading — `section` and `show`.

The two verbs that exist so an executor is handed a task line and `## Handoff` rather than the
whole document. `section` slices N sections deterministically (and writes ONE); `show` answers
what `section` cannot — which headings and task ids exist, one task's line and metadata, and the
whole document only under a name nobody types by accident."""
from __future__ import annotations

import json
import sys

from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter, display_locator, read_one
from quenching.specs.commands.task import _find_task
from quenching.specs.parse import derive_info
from quenching.specs.parse.edit import (MOJIBAKE_THRESHOLD, _match_heading, fold_stray_heading,
                                        mojibake_words, resolve_heading_name, split_section_stream,
                                        unclosed_fence, upsert_section, whole_body,
                                        write_handoff_block)
from quenching.specs.parse.handoff import current_handoff_section, parse_handoff
from quenching.specs.parse.sections import section_state, stray_headings
from quenching.specs.parse.tasks import TAIL_RE
from quenching.specs.parse.text import body_after_frontmatter
from quenching.specs.schema import canonical_headings, headings_for_moment, section_guidance


# One message for both sides of `--scope`, because one flag has two contracts: a READ narrows the
# `## Handoff` slice and merely needs the heading among the ones asked for, while a WRITE replaces
# ONE block and is singular by what it means.
SCOPE_REFUSAL = ("--scope only applies to ## Handoff — a read must ask for it among its headings; "
                 "a write replaces ONE block and takes it alone")


def _refuse_scope(args, out: Emitter, headings: list[str]) -> int:
    out.emit(args.json,
             {"ok": False, "code": "sp-scope-not-handoff", "heading": headings[0],
              "declared": headings, "message": SCOPE_REFUSAL},
             f"error: {SCOPE_REFUSAL}")
    return 2


def _scoped_handoff(info: dict, scope: str) -> str:
    """`## Handoff` cut to the block(s) a reader at THIS point in the plan still needs — the
    evergreen one under `global`, plus the `### N.` of the next actionable task's section under
    `current`.

    The cut is `write_handoff_block`'s own, read backwards: the same `parse_handoff` split, the
    same `current_handoff_section` match by leading numeral, and the same `\\n\\n` assembly — so a
    block written by one comes back byte-identical from the other. No section already closed is
    ever handed to a reader, which is what turns `artifacts.md` §`## Handoff`'s rule from agent
    discipline into a tool guarantee.

    No current section (a spec with no `## Tasks` yet) degrades to the global block alone — the
    same "nothing to scope to" the write side already treats as a no-op."""
    parsed = parse_handoff(body_after_frontmatter(info["text"]))
    parts = [parsed["global"]]
    if scope == "current":
        n = current_handoff_section(info["tasks"])
        block = next((b for b in parsed["blocks"] if b["section"] == n), None)
        if block:
            parts.append(f"### {block['title']}\n\n{block['body'].strip(chr(10))}")
    return "\n\n".join(p.strip("\n") for p in parts if p.strip())


def _fold_stray(args, backend, info: dict, root: str, out: Emitter) -> int:
    """`--fold` — the ONE path on this surface that admits a heading outside the thirteen.

    `validate` emits `sp-stray-heading` with a remedy ("fold it into a canonical one") that
    nothing could apply: `cmd_section` resolves every requested heading against the canonical
    list and exits 2 on the first that misses, *before* looking at `--write`, so a stray could
    not even be NAMED. This is that exit, opened exactly once and exactly here.

    **The admission is local, and the negative pair is the proof.** The stray is resolved
    against the strays the DOCUMENT actually carries — never against the canonical list — so
    nothing here widens what any other path accepts, and the `sp-stray-heading` the stream
    write raises stays untouched.

    Three refusals, each with its own code, because they are fixed by different things: a
    canonical heading handed to `--fold` is not a stray; a name matching no stray in this
    document is a typo the candidate list answers; and a stray with no canonical section above
    it has no honest host — inventing one is worse than the refusal, so it writes nothing."""
    beside = [flag for flag, val in (("--moment", args.moment), ("--write", args.write),
                                     ("--scope", getattr(args, "scope", None)),
                                     ("--clear", getattr(args, "clear", False)),
                                     ("a heading", args.heading)) if val]
    if beside:
        msg = f"--fold takes the stray heading alone; drop {', '.join(beside)}"
        out.emit(args.json,
                 {"ok": False, "code": "sp-fold-exclusive", "beside": beside, "message": msg},
                 f"error: {msg}")
        return 2

    strays = stray_headings(info["sections"])
    hit = resolve_heading_name(args.fold, strays)
    if hit is None:
        canonical = _match_heading(args.fold)
        if canonical:
            msg = (f"`## {canonical}` is one of the thirteen canonical headings — --fold "
                   "closes a stray, and a canonical section is never one")
            code = "sp-fold-not-stray"
        else:
            msg = (f"no stray heading matching '{args.fold}' in this spec — "
                   f"strays: {', '.join(strays) or 'none'}")
            code = "sp-fold-unknown-stray"
        out.emit(args.json,
                 {"ok": False, "code": code, "heading": args.fold, "strays": strays,
                  "message": msg},
                 f"error: {msg}")
        return 2

    new_text, host = fold_stray_heading(info, hit)
    if host is None:
        msg = (f"`## {hit}` has no canonical section before it — there is nothing to fold it "
               "into, and picking a host would invent an owner for the text")
        out.emit(args.json,
                 {"ok": False, "code": "sp-fold-no-anchor", "heading": hit, "message": msg},
                 f"error: {msg}")
        return 2

    backend.write_spec(info, new_text)
    out.emit(args.json,
             {"ok": True, "id": info["id"], "heading": hit, "action": "folded",
              "host": host, "path": display_locator(info["path"], root)},
             f"folded ## {hit} into ## {host} in {info['id']}")
    return 0


def _canonical_wanted(args, out: Emitter) -> tuple[list[str], int]:
    """The canonical headings the call names, or ``([], exit_code)`` after a refusal."""
    if args.moment:
        wanted = headings_for_moment(args.moment)
        if not wanted:
            out.emit(args.json,
                     {"ok": False, "code": "sp-unknown-moment", "moment": args.moment,
                      "message": f"no canonical section declares moment '{args.moment}'"},
                     f"error: no canonical section declares moment '{args.moment}'")
            return [], 2
    else:
        wanted = [h for h in (p.strip() for p in (args.heading or "").split(",")) if h]
        if not wanted:
            out.emit(args.json,
                     {"ok": False, "code": "sp-no-heading",
                      "message": "give a heading, or --moment, to read"},
                     "error: give a heading, or --moment, to read")
            return [], 2
    headings, stray = [], []
    for name in wanted:
        h = _match_heading(name)
        (headings.append(h) if h else stray.append(name))
    if stray:
        out.emit(args.json,
                 {"ok": False, "code": "sp-stray-heading", "heading": stray[0],
                  "stray": stray, "canonical": canonical_headings(),
                  "message": f"not one of the thirteen canonical headings: {', '.join(stray)}"},
                 f"error: not a canonical heading: {', '.join(stray)}")
        return [], 2
    return headings, 0


def _read_sections(args, out: Emitter, info: dict, headings: list[str], scope) -> int:
    # A read COMPOSES `--scope` with a plural request — `--moment build` above all — so the
    # neighbouring headings come back whole and one call still answers a whole moment. Only
    # asking for `## Handoff` and then not scoping it is unreadable, which is the refusal.
    if scope and "Handoff" not in headings:
        return _refuse_scope(args, out, headings)
    rows = [{"heading": h, "state": section_state(info["sections"], h),
             "body": _scoped_handoff(info, scope) if scope and h == "Handoff"
             else info["sections"].get(h, {}).get("body", "")} for h in headings]
    absent = [r["heading"] for r in rows if r["state"] == "absent"]
    if args.json:
        one = rows[0] if len(rows) == 1 else {}
        print(json.dumps({"ok": not absent, "id": info["id"],
                          **one, "sections": rows, "absent": absent, "scope": scope},
                         indent=2, ensure_ascii=False))
    else:
        for r in rows:
            if r["state"] == "absent":
                print(f"(## {r['heading']} is absent)")
            elif len(rows) == 1:
                print(r["body"].strip())
            else:
                print(f"## {r['heading']}\n\n{r['body'].strip()}\n")
    return 0 if not absent else 1


def _refuse_nested_heading(args, out: Emitter, heading: str, line: str) -> int:
    msg = (f"the body carries `{line}` — written under `## {heading}` it would open a second "
           "top-level section; demote it to `###` or drop it")
    out.emit(args.json, {"ok": False, "code": "sp-write-nested-heading", "heading": heading,
                         "line": line, "message": msg}, f"error: {msg}")
    return 2


def _refuse_unclosed_fence(args, out: Emitter, heading: str, line: str) -> int:
    msg = (f"the body of `## {heading}` opens `{line}` and never closes it — written, the fence "
           "would swallow every section after it; close the block and write again")
    out.emit(args.json, {"ok": False, "code": "sp-write-unclosed-fence", "heading": heading,
                         "line": line, "message": msg}, f"error: {msg}")
    return 2


def refuse_mojibake(args, out: Emitter, words: list[str]) -> int:
    shown = words[:3]
    msg = (f"the body carries {len(words)} words with a `?` between letters ({', '.join(shown)}) — "
           "the signature of a non-ASCII character lost in the pipe, so nothing was written. In "
           "PowerShell 5 run `$OutputEncoding=[Text.UTF8Encoding]::new($false)` before the pipe, "
           "or write through the MCP tool `specs_section_write`; a `?` that is meant goes in a "
           "code span")
    out.emit(args.json, {"ok": False, "code": "sp-write-mojibake", "words": shown,
                         "count": len(words), "message": msg}, f"error: {msg}")
    return 2


def _refuse_empty_body(args, out: Emitter, empty: list[str]) -> int:
    names = ", ".join(f"## {h}" for h in empty)
    msg = (f"the body of {names} is empty — written, it would replace the section with the "
           "template's guidance; pass --clear to ask for that, or send the body")
    out.emit(args.json, {"ok": False, "code": "sp-write-empty-body", "headings": empty,
                         "message": msg}, f"error: {msg}")
    return 2


def _splice_stream(args, out: Emitter, info: dict, headings: list[str],
                   content: str) -> tuple[str, list[dict]] | int:
    """The text with every streamed section spliced in, or the exit code of a refusal."""
    # EVERY refusal is settled before the first splice, so a rejected call writes nothing
    # at all. The old failure mode this replaces was N separate invocations, where the one
    # that broke left the previous k already on disk.
    blocks = split_section_stream(content, canonical_headings())
    unresolved = [i for i, (h, _) in enumerate(blocks, 1) if h is None]
    if unresolved:
        out.emit(args.json,
                 {"ok": False, "code": "sp-stray-heading", "source": "stream",
                  "unresolvedBlocks": unresolved, "canonical": canonical_headings(),
                  "message": "the stream carries a `## ` heading that is not one of the "
                             "thirteen canonical ones, at block(s) "
                             f"{', '.join(str(i) for i in unresolved)}"},
                 f"error: non-canonical `## ` heading at stream block(s) "
                 f"{', '.join(str(i) for i in unresolved)}")
        return 2
    carried = [h for h, _ in blocks]
    if len(set(carried)) != len(carried):
        out.emit(args.json,
                 {"ok": False, "code": "sp-write-duplicate-heading", "stream": carried,
                  "message": "the stream carries the same heading twice — which body "
                             "wins is not derivable"},
                 "error: the stream carries the same heading twice")
        return 2
    # No blocks under exactly one declared heading is the SINGULAR form: a raw body, no
    # heading in the stream, written whole — what every caller did before this was plural.
    # It is the ONE case the set check cannot govern, because the stream declares nothing
    # for it to be checked against.
    singular = not blocks and len(headings) == 1
    # Before the set check: a fence left open swallows the `## ` lines after it, so the set
    # mismatch it causes is a symptom and the open fence is what the writer must fix.
    for heading, body in (blocks or ([(headings[0], content)] if singular else [])):
        if opener := unclosed_fence(body):
            return _refuse_unclosed_fence(args, out, heading, opener)
    if not singular and set(carried) != set(headings):
        out.emit(args.json,
                 {"ok": False, "code": "sp-write-set-mismatch", "declared": headings,
                  "stream": carried,
                  "message": "the headings declared on the command line and the ones the "
                             "stream carries are not the same set"},
                 f"error: declared {', '.join(headings)}; stream carries "
                 f"{', '.join(carried) or 'none'}")
        return 2
    # Last of the refusals: an empty body only rides with the explicit --clear, so a producer
    # that failed upstream of the pipe cannot wipe a filled section and still exit 0.
    pairs = blocks or [(headings[0], content)]
    empty = [h for h, body in pairs if not body.strip()]
    if empty and not getattr(args, "clear", False):
        return _refuse_empty_body(args, out, empty)
    # `upsert_section` splices by the line numbers of the info it was handed, so each
    # section's write invalidates the next one's offsets — re-derive between them, and
    # hand the backend the one text they all landed in.
    new_text, results, cur = info["text"], [], info
    for heading, body in pairs:
        block = (f"## {heading}\n\n{body.strip()}\n"
                 if body.strip() else section_guidance(heading))
        new_text, action = upsert_section(cur, heading, block)
        results.append({"heading": heading, "action": action})
        cur = derive_info(cur, new_text)
    if info["frontmatter"].get("approved") and any(h == "Tasks" for h, _ in pairs):
        if changed := _frozen_task_changes(info["tasks"], cur["tasks"]):
            return _refuse_frozen_tasks(args, out, changed)
    return new_text, results


def _task_contract(t: dict) -> tuple:
    """What an approved task promises: its words, its proof and its scope. State and `subject:`
    are the task verbs' to write, so they are not part of it."""
    return (TAIL_RE.sub("", t["text"]).strip(), t["verify"], t["files"], t["operational"],
            t["cwd"])


def _frozen_task_changes(old: list[dict], new: list[dict]) -> list[str]:
    """Ids of approved tasks the new `## Tasks` dropped or rewrote; appending is not one."""
    now = {t["id"]: t for t in new if t["id"]}
    return [t["id"] for t in old if t["id"]
            and (t["id"] not in now or _task_contract(t) != _task_contract(now[t["id"]]))]


def _refuse_frozen_tasks(args, out: Emitter, ids: list[str]) -> int:
    msg = (f"## Tasks is append-only after `approved`: task(s) {', '.join(ids)} were removed or "
           "had their text, `verify:`, `files:` or `cwd:` changed — retire scope with "
           "`task --descope --reason`, or add a task instead")
    out.emit(args.json, {"ok": False, "code": "sp-tasks-frozen", "tasks": ids, "message": msg},
             f"error: {msg}")
    return 2


def cmd_section(args, root: str, out: Emitter) -> int:
    """Deterministic partial read/write of N sections — what makes lean agent context real.

    An executor is handed a task line and `## Handoff`, never the whole spec; this is the
    command that slices it without an LLM re-reading and rewriting the file.

    **Both forms are plural, and that is half the saving, not a convenience.** Every turn
    re-sends the whole conversation, so a cost has two factors — tokens AND the turns that
    follow it. Six headings fetched over six turns can lose to the one `Read` this command
    replaced. Comma-separated, one call, back in the order asked.

    `--write` is plural through the stream itself: the bodies arrive delimited by the same
    `## <Heading>` lines the plural read prints, so the two round-trip and nothing new had to
    be invented to say where one body ends (`split_section_stream`). A stream that does not
    open on a canonical heading is one raw body under the one heading declared — the singular
    form, unchanged. The declared list stays required either way: it is the guard that turns a
    heading mistyped in the stream into a named refusal instead of a section written in the
    wrong place, and the N splices land as ONE `write_spec`, which is N fewer API round trips
    on a remote backend.

    The document comes from the BACKEND, never from a path: this is the reader every other
    front calls, so a backend that could not serve it would leave the whole surface tied to
    `files`."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    if getattr(args, "fold", None):
        return _fold_stray(args, backend, info, root, out)
    headings, code = _canonical_wanted(args, out)
    if code:
        return code
    scope = getattr(args, "scope", None)
    if getattr(args, "clear", False) and not args.write:
        msg = "--clear empties a section on a write; it takes --write beside it"
        out.emit(args.json, {"ok": False, "code": "sp-clear-without-write", "message": msg},
                 f"error: {msg}")
        return 2
    if not args.write:
        return _read_sections(args, out, info, headings, scope)
    if scope and headings != ["Handoff"]:
        # A write under `--scope` replaces ONE block of one section, so it is singular by what it
        # means and not merely by how stdin arrives — a plural request under it has no reading.
        return _refuse_scope(args, out, headings)

    content = sys.stdin.read() if not sys.stdin.isatty() else ""
    if len(garbled := mojibake_words(content)) >= MOJIBAKE_THRESHOLD:
        return refuse_mojibake(args, out, garbled)
    if scope or (len(headings) == 1
                 and not split_section_stream(content, canonical_headings())):
        # A body written whole (scoped, or the raw singular form) is spliced under `## <heading>`
        # by this command, so a `## ` line of its own would open a SECOND top-level section.
        content, refusal = whole_body(content, headings[0])
        if refusal:
            return _refuse_nested_heading(args, out, headings[0], refusal)
    if scope and (opener := unclosed_fence(content)):
        return _refuse_unclosed_fence(args, out, headings[0], opener)
    if scope and not content.strip() and not getattr(args, "clear", False):
        return _refuse_empty_body(args, out, headings)
    if scope:
        new_text, action = write_handoff_block(info, scope, content)
        results = [{"heading": "Handoff", "action": action}]
    else:
        spliced = _splice_stream(args, out, info, headings, content)
        if isinstance(spliced, int):
            return spliced
        new_text, results = spliced
    backend.write_spec(info, new_text)
    # ONE heading answers in the shape it always answered — `heading` and `action`, flat —
    # so no caller written against the singular write has to learn a second reading of it.
    # `sections` is what the plural form ADDS, and only it.
    shape = {"heading": results[0]["heading"], "action": results[0]["action"]} \
        if len(results) == 1 else {"sections": results}
    out.emit(args.json,
             {"ok": True, "id": info["id"], **shape,
              "scope": scope, "path": display_locator(info["path"], root)},
             "\n".join(f"{r['action']} ## {r['heading']}"
                       f"{f' ({scope})' if scope else ''} in "
                       f"{info['id']}" for r in results))
    return 0


# The reader's view of a task. `metaInsertAt`, `metaIndent`, `subjectLineno` and
# `commitLineno` are deliberately NOT here: they are the offsets `task` upserts by, and
# handing them to a reader is an invitation to do the string surgery `task` exists to
# prevent. `lineno` stays, because locating a task in the file is reading, not writing.
# `operational` stays for the opposite reason: the parser hands an operational task on as
# `files: []`, and without the flag a reader cannot tell it from a task that declared the
# empty list — the verifier would then excuse a sha it owes (`files: (operational)`) or
# charge one the task does not owe.
SHOWN_TASK_KEYS = ("index", "id", "state", "checked", "blocked", "reason", "text",
                   "section", "parallel", "files", "operational", "pattern", "verify",
                   "subject", "commit", "lineno")


def _task_view(t: dict) -> dict:
    return {k: t[k] for k in SHOWN_TASK_KEYS}


def _show_index(info: dict) -> dict:
    """The MAP of one spec — which sections exist, how big each is, which task ids there are.

    Bounded by the thirteen headings and the task count no matter how long the document is,
    which is what makes it affordable as the default. It also makes the NEXT call exact: a
    caller that knows the heading spellings and the task ids never has to read the document
    to find out what it may ask for."""
    return {
        "sections": [{"heading": h,
                      "state": section_state(info["sections"], h),
                      "lines": len(info["sections"].get(h, {}).get("lines", []))}
                     for h in canonical_headings()],
        "strays": stray_headings(info["sections"]),
        "tasks": [{"id": t["id"], "index": t["index"], "state": t["state"], "text": t["text"]}
                  for t in info["tasks"]],
    }


def _show_human(obj: dict, info: dict) -> str:
    head = (f"{obj['id']} — {obj['title']}\n"
            f"  {obj['path']}  [{obj['stage']}]")
    if obj["view"] == "full":
        return info["text"].rstrip("\n")
    marks = {"filled": "✓", "empty": "!", "absent": "·"}
    out = [head]
    if obj["view"] == "index":
        out.append(f"  sections ({sum(1 for s in obj['sections'] if s['state'] != 'absent')}"
                   f"/{len(obj['sections'])} present)")
        for s in obj["sections"]:
            size = f"  {s['lines']} line(s)" if s["state"] != "absent" else ""
            out.append(f"    {marks[s['state']]} ## {s['heading']}{size}")
        if obj["strays"]:
            out.append(f"  strays: {', '.join(obj['strays'])}")
        if obj["tasks"]:
            out.append(f"  tasks ({len(obj['tasks'])})")
            for t in obj["tasks"]:
                out.append(f"    [{t['state']}] {t['text']}")
        out.append(f"  read sections: cq specs section {obj['id']} \"<Heading>,<Heading>\"\n"
                   f"  read one task: cq specs show --spec {obj['id']} --task <id>"
                   f"   (--full for the whole document)")
        return "\n".join(out)
    for t in obj["tasks"]:
        out.append(f"\n- [{t['state']}] {t['text']}")
        for key in ("files", "pattern", "verify", "subject", "commit"):
            val = t[key]
            if val:
                val = ", ".join(val) if isinstance(val, list) else val
                out.append(f"      {key}: {val}")
    return "\n".join(out)


def cmd_show(args, root: str, out: Emitter) -> int:
    """Read ONE task, or the map of what is there — the whole document only when it is asked
    for by name.

    THE COST THIS ADDRESSES IS THE AGENT'S CONTEXT, NOT I/O. A backend may well have fetched
    the entire document to answer `--task 3.1`, and that is fine — reading a file twice is
    free. What is not free is an executor handed thirteen sections in order to edit one: it
    carries the other thirteen through every remaining turn of its conversation and pays for
    them again on each. So the DEFAULT IS THE INDEX AND NEVER THE DOCUMENT, and `--full`
    exists precisely so that the whole document has to be typed on purpose.

    **Section bodies are `section`'s, not this command's.** That reader is already plural and
    already resolves a `--moment` to its section set, so a second way to ask for a heading
    would be a second spelling of a measured answer — and the two would drift. What is left
    here is what `section` cannot say: WHICH headings and task ids exist (the index), one
    task's line and metadata, and the whole document under a name nobody types by accident.

    An unknown task id is a finding (exit 1), the same as an unknown ID."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)

    wanted_tasks = list(args.task or [])
    if args.full and wanted_tasks:
        # Two different cost profiles in one request. Silently letting one win would hand
        # back the whole document to a caller that asked for a slice, which is the exact
        # failure this command exists to make impossible.
        out.emit(args.json,
                 {"ok": False, "code": "sp-conflicting-selection",
                  "message": "--full asks for the whole document and --task for a slice of it "
                             "— pass one or the other"},
                 "error: --full does not combine with --task")
        return 2

    tasks: list[dict] = []
    for ident in wanted_tasks:
        t = _find_task(info["tasks"], ident)
        if not t:
            out.emit(args.json, {"ok": False, "code": "sp-unknown-task", "task": ident,
                                 "message": f"no task '{ident}' in {info['id']}"},
                     f"error: no task '{ident}' in {info['id']}")
            return 1
        tasks.append(_task_view(t))

    view = "full" if args.full else ("slice" if tasks else "index")
    obj = {"ok": True, "id": info["id"],
           "title": info["frontmatter"].get("title", ""),
           "stage": info["stage"], "phase": info["phase"], "folder": info["folder"],
           "path": display_locator(info["path"], root), "view": view}
    if view == "full":
        obj["document"] = info["text"]
        obj["lines"] = len(info["text"].splitlines())
    elif view == "slice":
        obj["tasks"] = tasks
    else:
        obj.update(_show_index(info))
    out.emit(args.json, obj, _show_human(obj, info))
    return 0
