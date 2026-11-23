"""The executor's two verbs — `task` and `discover`.

`task` flips a checkbox MECHANICALLY and records the anchor of the commit that implements it;
`--uncheck --reason` reopens a task and records why in `## Discoveries`;
`--descope` closes a task as deliberately out of scope and records why in `## Outcome`;
`discover` appends one line to `## Discoveries`. Both are written during a build, and both exist
so nothing does string surgery on a spec from the caller's side."""
from __future__ import annotations

from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter, read_one
from quenching.specs.parse.derive import derive_info
from quenching.specs.parse.edit import upsert_section
from quenching.specs.parse.edit import nested_heading
from quenching.specs.parse.epics import parse_items
from quenching.specs.parse.fields import SCALAR_LINE_BREAKS, scalar_break
from quenching.specs.parse.text import split_lines
from quenching.specs.parse.tasks import (BLOCKED_REASON_RE, CHECKBOX_RE, COMMIT_SHA_RE,
                                         SUBJECT_RE)


def _find_task(tasks: list[dict], ident: str) -> dict | None:
    for t in tasks:
        if t["id"] == ident or str(t["index"]) == ident:
            return t
    return None


def _refuse(args, out: Emitter, code: str, message: str, text: str, status: int = 1,
            **extra) -> int:
    out.emit(args.json, {"ok": False, "code": code, **extra, "message": message}, text)
    return status


def _flag_refusal(args, out: Emitter) -> int | None:
    """The first flag-combination refusal, already emitted, or None when the flags are legal."""
    # The subject names WHICH COMMIT IMPLEMENTS THIS TASK, so it is meaningful only on the
    # transition that says the task is done. On --uncheck any recorded anchor is dropped
    # rather than left behind pointing at work the checkbox no longer claims.
    if args.subject and not args.check:
        return _refuse(args, out, "sp-subject-without-check",
                       "--subject records the commit that implements a task, so it goes "
                       "with --check",
                       "error: --subject goes with --check")
    # `SUBJECT_RE` alone lets a trailing `\n` and every break other than `\r`/`\n` through.
    if args.subject is not None and (not SUBJECT_RE.match(args.subject.strip())
                                     or scalar_break(args.subject)):
        return _refuse(args, out, "sp-bad-subject",
                       "a commit subject must be one non-empty line",
                       "error: a commit subject must be one non-empty line", status=2,
                       subject=args.subject)
    # --commit is the sha form of the SAME anchor, meant to be called AFTER the commit that
    # implements the task already exists — the CLI never invents or looks up a sha, it only
    # records the one the caller already has. Same rule as --subject: meaningful only on the
    # transition that says the task is done, so it goes with --check too. It is additive,
    # not a replacement: a caller may still pass --subject alone, exactly as before.
    if args.commit and not args.check:
        return _refuse(args, out, "sp-commit-without-check",
                       "--commit records the sha of the commit that implements a task, so it "
                       "goes with --check",
                       "error: --commit goes with --check")
    if args.commit is not None and not COMMIT_SHA_RE.match(args.commit.strip()):
        return _refuse(args, out, "sp-bad-commit-sha",
                       "--commit takes a git sha (hex, 7-40 characters), not free text",
                       "error: --commit takes a git sha (hex, 7-40 characters), not free text",
                       commit=args.commit)
    # A reason is spliced into `## Outcome` / `## Discoveries` / the task line: a `## ` line of its
    # own would open a second top-level section there.
    bad = nested_heading(args.reason or "")
    if bad:
        return _refuse(args, out, "sp-write-nested-heading",
                       f"--reason carries `{bad}` — it would open a second top-level section",
                       f"error: --reason carries `{bad}` — demote it to `###` or drop it")
    # The reason is one line on all three routes: a break would forge the lines after it.
    bad = scalar_break(args.reason or "")
    if bad:
        return _refuse(args, out, "sp-bad-scalar",
                       f"--reason is one line with no control character — got {bad}",
                       f"error: --reason is one line with no control character — got {bad}",
                       status=2, field="reason", char=bad)
    if args.block and not args.reason:
        return _refuse(args, out, "sp-no-reason", "--block requires --reason",
                       "error: --block requires --reason (a blocked task without a reason is "
                       "the hidden state this replaced)")
    if args.descope and not args.reason:
        return _refuse(args, out, "sp-no-reason", "--descope requires --reason",
                       "error: --descope requires --reason (the reason is written into "
                       "## Outcome)")
    if args.uncheck and not args.reason:
        return _refuse(args, out, "sp-no-reason", "--uncheck requires --reason",
                       "error: --uncheck requires --reason (the reason is written into "
                       "## Discoveries)")
    return None


def _upsert_anchors(lines: list[str], t: dict, args) -> tuple[str | None, str | None]:
    """Upsert the `subject:`/`commit:` metadata lines of one task, in place.

    Replace one that is already there, otherwise append it after the task's last metadata line
    (or right under the checkbox). The two anchors are independent: passing one never disturbs
    an existing line for the other, and both may be written in the same call while a spec
    transitions from the subject anchor to the sha one. New lines are inserted together in ONE
    slice so inserting one never shifts the position computed for the other."""
    subject = None
    commit = None
    new_entries: list[str] = []
    if args.subject:
        subject = args.subject.strip()
        entry = f"{t['metaIndent']}subject: {subject}\n"
        if t["subjectLineno"] is not None:
            lines[t["subjectLineno"]] = entry
        else:
            new_entries.append(entry)
    if args.commit:
        commit = args.commit.strip()
        entry = f"{t['metaIndent']}commit: {commit}\n"
        if t["commitLineno"] is not None:
            lines[t["commitLineno"]] = entry
        else:
            new_entries.append(entry)
    if new_entries:
        lines[t["metaInsertAt"]:t["metaInsertAt"]] = new_entries
    if not args.subject and not args.commit and args.uncheck:
        # Drop whichever anchor the line carries — `subject:`, `commit:` written by
        # `--commit`, or the legacy `commit:` on a spec written before either form applied
        # to it. Highest offset first, so deleting one cannot shift the index of the other.
        for off in sorted((o for o in (t["subjectLineno"], t["commitLineno"])
                           if o is not None), reverse=True):
            del lines[off]
    return subject, commit


def _record_uncheck(info: dict, lines: list[str], ident: str, reason: str) -> list[str]:
    # Anchor removal may shift every section below the task. Re-derive before the splice;
    # using the first read's line numbers here would insert the discovery into the wrong
    # section whenever the task carried metadata and `## Discoveries` followed it.
    edited_info = derive_info(info, "".join(lines))
    discoveries = edited_info["sections"].get("Discoveries")
    prior = discoveries["body"].rstrip() if discoveries and discoveries["filled"] else ""
    entry = f"- task {ident} unchecked: {reason}"
    discovery_body = f"## Discoveries\n{prior}\n{entry}\n" if prior else \
        f"## Discoveries\n\n{entry}\n"
    edited, _ = upsert_section(edited_info, "Discoveries", discovery_body)
    return edited.splitlines(keepends=True)


def _record_descope(info: dict, lines: list[str], ident: str, reason: str) -> list[str]:
    # A descoped task is closed in the checklist, but its reason belongs to the close-out
    # narrative too. Replace an empty guidance block; otherwise append without losing prior
    # outcome facts or a reason recorded for another descoped task.
    info["text"] = "".join(lines)
    outcome = info["sections"].get("Outcome")
    prior = outcome["body"].strip() if outcome and outcome["filled"] else ""
    entry = f"- task {ident} descoped: {reason}"
    outcome_body = f"{prior}\n\n{entry}" if prior else entry
    edited, _ = upsert_section(info, "Outcome", f"## Outcome\n\n{outcome_body}\n")
    return edited.splitlines(keepends=True)


def _retick_line(m, args) -> str:
    body = BLOCKED_REASON_RE.sub("", m.group(3).rstrip()).rstrip()   # drop any stale blocked suffix
    if args.block:
        body = f"{body} — blocked: {args.reason.strip()}"
    elif args.descope:
        body = f"{body} — descoped: {args.reason.strip()}"
    return body


def cmd_task(args, root: str, out: Emitter) -> int:
    """Flip a checkbox MECHANICALLY — never by string surgery on the caller's side.

    `--block` writes the reason into the line itself. That visibility is the whole point:
    v1 kept an attempt counter in `.specs.json` that nobody read, and a task went quiet
    after five failures with no trace of why.

    `--descope` writes a checked box with a visible `descoped:` suffix and appends the reason
    to `## Outcome`. It is a closed task, not a blocked one: `promote --outcome done` can
    archive it without `--force`, while the Outcome remains the durable explanation.

    `--uncheck` requires `--reason`. It removes any stale `subject:`/`commit:` anchor and
    appends the reason to `## Discoveries` in the same backend write. A revert therefore leaves
    the task open without losing the explanation or pretending that the reverted commit still
    implements it.

    `--check` may carry `--subject`, `--commit`, both, or neither. `--commit <sha>` is
    called AFTER the commit implementing the task already exists — the CLI records the sha
    it is given, it never resolves or invents one — and is additive: `--subject` keeps
    working exactly as before for any caller that has not moved to the sha anchor. Whichever
    write actually lands the anchor (`backend.write_spec`, below) is where a failure — a
    `gh api` call included — is reported, never swallowed."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    ident = args.check or args.uncheck or args.block or args.descope
    if ident and any(str(i["label"]) == str(ident) for i in parse_items(info)):
        # A spec item is DERIVED from its member — a box written here would be a second,
        # stale copy of a fact the member already holds. Ordinary tasks in the same
        # document stay tickable.
        return out.emit_err(args.json, {
            "code": "sp-link-derived-task", "exit": 2, "id": info["id"], "task": ident,
            "message": f"'{ident}' in '{info['id']}' is a spec item — its state is derived "
                       f"from the member spec and never ticked; act on the member spec "
                       f"(`cq specs status --spec {info['id']}`)"})
    if not ident:
        out.emit(args.json, {"ok": False, "code": "sp-no-action",
                             "message": "pass --check, --uncheck, --block or --descope"},
                 "error: pass --check, --uncheck, --block or --descope")
        return 1
    refused = _flag_refusal(args, out)
    if refused is not None:
        return refused
    t = _find_task(info["tasks"], ident)
    if not t:
        return _refuse(args, out, "sp-unknown-task", f"no task '{ident}' in {info['id']}",
                       f"error: no task '{ident}' in {info['id']}", task=ident)

    lines = split_lines(info["text"], keepends=True)
    m = CHECKBOX_RE.match(lines[t["lineno"]].rstrip("\n"))
    if not m:
        return _refuse(args, out, "sp-line-drift",
                       "the parsed line is not a checkbox — the file changed",
                       "error: the parsed line is not a checkbox — re-read the spec",
                       task=ident, lineno=t["lineno"])
    action = ("check" if args.check else "uncheck" if args.uncheck else
              "block" if args.block else "descope")
    mark = {"check": "x", "uncheck": " ", "block": "!", "descope": "x"}[action]
    body = _retick_line(m, args)
    lines[t["lineno"]] = f"{m.group(1)}- [{mark}] {body}\n"

    subject, commit = _upsert_anchors(lines, t, args)
    if args.uncheck:
        lines = _record_uncheck(info, lines, ident, args.reason.strip())
    if args.descope:
        lines = _record_descope(info, lines, ident, args.reason.strip())
    # THE FAILURE-REPORTING CONTRACT: this call is the one that can fail out from under a
    # tick that already looks applied to `lines`. The backend either persists the document or
    # raises (`GitHubBackend` raises `BackendRefusal` the instant `gh api` fails). `main()` is
    # the one place that exception becomes an exit code and an `ok: false` JSON body; nothing
    # here catches it and nothing here prints a success message before this line returns.
    backend.write_spec(info, "".join(lines))

    verb = ("checked" if args.check else "unchecked" if args.uncheck else
            "blocked" if args.block else "descoped")
    anchor_lines = ((f"\n  subject: {subject}" if subject else "") +
                    (f"\n  commit: {commit}" if commit else ""))
    out.emit(args.json,
             {"ok": True, "id": info["id"], "task": ident, "action": verb,
              "state": mark, "text": body, "subject": subject, "commit": commit,
              "reason": args.reason.strip() if args.block or args.descope or args.uncheck else None},
             f"task {ident} {verb}: {body}" + anchor_lines)
    return 0


def cmd_discover(args, root: str, out: Emitter) -> int:
    """Append one line to `## Discoveries`, creating the section when absent.

    Captured INDISCRIMINATELY during execution — whether a discovery is worth acting on is
    triage's judgment, not the executor's, and the cost of asking mid-build is a human
    interrupted for something that may not matter."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    bad = nested_heading(args.text)
    if bad:
        msg = f"the finding carries `{bad}` — it would open a second top-level section"
        out.emit(args.json, {"ok": False, "code": "sp-write-nested-heading", "line": bad,
                             "message": msg}, f"error: {msg}")
        return 2
    if any(c in SCALAR_LINE_BREAKS for c in args.text.strip()):
        msg = "the finding carries a line break — one discovery is one line"
        out.emit(args.json, {"ok": False, "code": "sp-discover-multiline", "message": msg},
                 f"error: {msg}")
        return 2
    entry = f"- {args.text.strip()}"
    sec = info["sections"].get("Discoveries")
    if sec and sec["filled"]:
        block = f"## Discoveries\n{sec['body'].rstrip()}\n{entry}\n"
    else:
        block = f"## Discoveries\n\n{entry}\n"
    new_text, _ = upsert_section(info, "Discoveries", block)
    backend.write_spec(info, new_text)
    out.emit(args.json,
             {"ok": True, "id": info["id"], "entry": args.text.strip()},
             f"recorded in ## Discoveries: {args.text.strip()}")
    return 0
