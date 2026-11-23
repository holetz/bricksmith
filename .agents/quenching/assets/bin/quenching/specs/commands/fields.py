"""The frontmatter writers — `verification`, the four state fields, and the records.

Three verbs that write ONE declared key each, through the backend and never by editing a path:
the verification policy (a declared scalar with a closed value set), `tags`/`assignee`/`start`/
`target` (state a backend reassembles on read), and the schema's records (judgments with their
own `writtenBy`/`writeOnce` rules)."""
from __future__ import annotations

import datetime
import json

from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter, read_one
from quenching.specs.parse.fields import (scalar_break, set_frontmatter_key,
                                          set_frontmatter_record, set_frontmatter_title)
from quenching.specs.parse.records import record_keys
from quenching.specs.schema import (DEFAULT_VERIFICATION, MERGE_NO_PR_STRATEGIES,
                                    VERIFICATION_POLICIES, load_schema)

RUN_VERDICTS = ("pass", "fail")


def cmd_verification(args, root: str, out: Emitter) -> int:
    """Read ONE spec's verification policy, or set it — through the backend, after capture.

    THE ONLY WRITER USED TO BE `new --verification`, and that is the one moment nobody has an
    opinion: the policy answers how long THIS repo's suite takes and whether a section is the
    smallest safe unit, which is what `/quenching-specs develop`'s gate bank exists to ask.
    With no writer after capture that bank could only land its answer by editing the
    frontmatter by hand — which needs a file, and an external backend has none. So under
    `github` the policy was decidable exactly once, before anyone knew the answer, and
    unchangeable afterwards.

    It is NOT a `record`: the seven records are `{field: value}` judgments with their own
    write-once rules, and this is a declared scalar with a closed value set. Folding it into
    `record` would have meant inventing a one-field record and a `value=` field name for it."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    declared = str(info["frontmatter"].get("verification", "")).strip().lower()

    if not args.policy:
        out.emit(args.json,
                 {"ok": True, "id": info["id"], "verification": info["verification"],
                  "declared": declared or None, "default": DEFAULT_VERIFICATION},
                 f"{info['id']} — verification: {info['verification']}"
                 + ("" if declared else "  (the default — nothing declared)"))
        return 0

    policy = args.policy.strip().lower()
    if policy not in VERIFICATION_POLICIES:
        out.emit(args.json,
                 {"ok": False, "code": "sp-bad-verification", "id": info["id"],
                  "given": args.policy, "policies": list(VERIFICATION_POLICIES),
                  "message": f"'{args.policy}' is not one of "
                         f"{', '.join(VERIFICATION_POLICIES)}"},
                 f"error: '{args.policy}' is not one of {', '.join(VERIFICATION_POLICIES)}")
        return 2
    backend.write_spec(info, set_frontmatter_key(info["text"], "verification", policy))
    out.emit(args.json,
             {"ok": True, "id": info["id"], "verification": policy,
              "previous": declared or None},
             f"{info['id']} — verification: {policy}"
             + (f"  (was {declared})" if declared and declared != policy else ""))
    return 0


def cmd_title(args, root: str, out: Emitter) -> int:
    """Read ONE spec's `title`, or set it — quoted when the text needs it, through the backend.

    The repair path for a `title` an older `new` stamped unquoted: a ` #` read as a comment
    erased it, and nothing else writes the key. The write keeps the first `title:` line's place
    and drops later ones, which is what a corrupted file carries."""
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    current = str(info["frontmatter"].get("title") or "")
    if args.title is None:
        out.emit(args.json, {"ok": True, "id": info["id"], "title": current or None},
                 f"{info['id']} — title: {current or '(none)'}")
        return 0
    title = args.title.strip()
    bad = scalar_break(title)
    if not title or bad:
        message = ("a title is one non-empty line with no control character"
                   + (f" — got {bad}" if bad else ""))
        out.emit(args.json,
                 {"ok": False, "code": "sp-bad-title", "id": info["id"], "char": bad,
                  "message": message},
                 f"error: {message}")
        return 2
    backend.write_spec(info, set_frontmatter_title(info["text"], title))
    out.emit(args.json,
             {"ok": True, "id": info["id"], "title": title, "previous": current or None},
             f"{info['id']} — title: {title}")
    return 0


def record_field_value_error(rspec: dict, k: str, v: str) -> str | None:
    """The one per-field value rule: a field declaring `levels:` admits only those.

    `priority.complexity` is the first field to declare one — the scale the orchestrator
    derives the gears plan from — but the rule is generic: a field without a `levels:`
    block stays unrestricted, exactly as every field was before this one. `writtenBy` on
    the block is the schema's declaration of who may write the field, read by the command
    bodies that call `record`; the tool itself cannot know who calls it, so the value rule
    is the only half it enforces."""
    fspec = rspec.get(k)
    if isinstance(fspec, dict) and fspec.get("levels") and v not in fspec["levels"]:
        return (f"`{k}:` must be one of {', '.join(fspec['levels'])} — got '{v}'")
    return None


def parse_field_date(value: str) -> str | None:
    """`value` as an ISO date, or `None` — REAL calendar validation, not a digit-shaped
    regex: `^\\d{4}-\\d{2}-\\d{2}$` accepts `2026-13-99`, which `date.fromisoformat` refuses."""
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def cmd_field(args, root: str, out: Emitter) -> int:
    """Read or set ONE declared scalar — `tags`/`assignee`/`start`/`target`/`end` —
    through the backend. The ONE deterministic verb `## Design` requires for each: an agent
    (or a human) may choose WHAT to write, a tag from `tagCatalog`, an assignee or a date,
    but never HOW. `args.field` is set by the subparser that dispatched here.

    **Not all of them are `FIELD_KEYS`.** That tuple is the four with a faithful native
    counterpart, and it drives `carry_forward_fields` and the azure backend's
    `strip_frontmatter_keys` — the keys STORED natively and therefore stripped from the
    None of them is a `record`: these are state, not a judgment with a `writtenBy`/`writeOnce`
    rule of its own — `cmd_verification` argues the same distinction for the one scalar that
    came before them."""
    key = args.field
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    current = info["frontmatter"].get(key)

    if args.value is None:
        out.emit(args.json, {"ok": True, "id": info["id"], key: current},
                 f"{info['id']} — {key}: {current if current else '(none)'}")
        return 0

    bad = scalar_break(args.value)
    if bad:
        message = f"`{key}:` carries no line break or control character — got {bad}"
        out.emit(args.json, {"ok": False, "code": "sp-bad-scalar", "field": key, "char": bad,
                             "message": message},
                 f"error: {message}")
        return 2
    if key == "tags":
        items = [t.strip() for t in args.value.split(",") if t.strip()]
        rendered = "[" + ", ".join(json.dumps(t, ensure_ascii=False) for t in items) + "]"
        stored: object = items
    elif key in ("start", "target", "end"):
        value = parse_field_date(args.value.strip())
        if value is None:
            out.emit(args.json, {"ok": False, "code": f"sp-bad-{key}", "given": args.value,
                                 "message": f"'{args.value}' is not a real YYYY-MM-DD date"},
                     f"error: '{args.value}' is not a real YYYY-MM-DD date")
            return 2
        rendered = stored = value
    else:   # assignee
        rendered = stored = args.value.strip()

    backend.write_spec(info, set_frontmatter_key(info["text"], key, rendered))
    out.emit(args.json, {"ok": True, "id": info["id"], key: stored, "previous": current},
             f"{info['id']} — {key}: {stored}"
             + (f"  (was {current})" if current and current != stored else ""))
    return 0


def cmd_record(args, root: str, out: Emitter) -> int:
    """Read or merge ONE frontmatter record, through the backend.

    The seven records were the last thing the command surface wrote by editing the file at
    its path — `triage` stamping `priority`, `isolate` stamping `branch`, `conclude`
    stamping `merge`. That is a path write, so it worked only while the backend happened to
    be `files`; against GitHub there is no file to edit.

    Which records exist, which fields each declares and which are write-once all come from
    the schema, so adding a record stays a schema edit. A record declaring no `fields:` is
    not writable here at all — that is `outcome`, whose one writer is `promote --outcome`,
    and it falls out of the declaration rather than being named in the code."""
    schema = load_schema()
    declared = schema.get("frontmatter", {}).get("records", {})
    if args.name not in record_keys(schema):
        out.emit(args.json,
                 {"ok": False, "code": "sp-unknown-record", "record": args.name,
                  "declared": record_keys(schema),
                  "message": f"'{args.name}' is not a declared record — the declared ones are "
                         f"{', '.join(record_keys(schema))}"},
                 f"error: '{args.name}' is not a declared record")
        return 2
    rspec = declared.get(args.name, {})
    fields = list(rspec.get("fields", []))

    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    current = info["frontmatter"].get(args.name) or None

    if not args.set:
        if args.json:
            print(json.dumps({"ok": current is not None, "id": info["id"],
                              "record": args.name, "value": current},
                             indent=2, ensure_ascii=False))
        else:
            print(f"{args.name}: {current}" if current is not None
                  else f"({args.name}: is unset)")
        return 0 if current is not None else 1

    if not fields:
        out.emit(args.json,
                 {"ok": False, "code": "sp-record-not-writable", "record": args.name,
                  "writtenBy": rspec.get("writtenBy", ""),
                  "message": f"`{args.name}:` declares no fields — its one writer is "
                         f"{rspec.get('writtenBy', 'another command')}"},
                 f"refused: `{args.name}:` is not written through this command")
        return 2
    if rspec.get("writeOnce") and current:
        out.emit(args.json,
                 {"ok": False, "code": "sp-record-write-once", "record": args.name,
                  "current": current,
                  "message": f"`{args.name}:` is write-once and already reads {current} — a "
                         f"record that disagrees with reality is a finding to report, "
                         f"never a value to overwrite"},
                 f"refused: `{args.name}:` is already set to {current}")
        return 2

    merged = dict(current) if isinstance(current, dict) else {}
    for pair in args.set:
        k, sep, v = pair.partition("=")
        k, v = k.strip(), v.strip()
        if not sep or k not in fields:
            out.emit(args.json,
                     {"ok": False, "code": "sp-unknown-record-field", "record": args.name,
                      "given": pair, "fields": fields,
                      "message": f"expected `field=value` with field one of "
                             f"{', '.join(fields)} — got '{pair}'"},
                     f"error: expected `field=value` for `{args.name}:` — got '{pair}'")
            return 2
        bad = scalar_break(v)
        if bad:
            message = f"`{args.name}.{k}` carries no line break or control character — got {bad}"
            out.emit(args.json,
                     {"ok": False, "code": "sp-bad-scalar", "record": args.name, "field": k,
                      "char": bad, "message": message},
                     f"refused: {message}")
            return 2
        err = record_field_value_error(rspec, k, v)
        if err:
            out.emit(args.json,
                     {"ok": False, "code": "sp-invalid-record-value", "record": args.name,
                      "field": k, "given": v, "levels": rspec[k].get("levels", []),
                      "message": err},
                     f"refused: {err}")
            return 2
        merged[k] = v
    if (args.name == "merge" and merged.get("pr")
            and merged.get("strategy") in MERGE_NO_PR_STRATEGIES):
        out.emit(args.json,
                 {"ok": False, "code": "sp-merge-pr-no-route", "record": args.name,
                  "strategy": merged.get("strategy"), "noPr": list(MERGE_NO_PR_STRATEGIES),
                  "message": f"`pr:` has no `gh pr merge` equivalent under "
                         f"`{merged.get('strategy')}` — the PR route is never offered "
                         f"under it, so there is nothing for `pr:` to record"},
                 f"refused: `pr:` is set but `{merged.get('strategy')}` has no PR route")
        return 2
    if args.name == "run":
        verdict = [pair.partition("=")[2].strip() for pair in args.set
                   if pair.partition("=")[0].strip() == "verifier"]
        attested = any(pair.replace(" ", "") == "by=verifier" for pair in args.set)
        if verdict and verdict[-1] in RUN_VERDICTS and not attested:
            out.emit(args.json,
                     {"ok": False, "code": "sp-run-verdict-unattested", "record": args.name,
                      "given": verdict[-1],
                      "message": f"`verifier={verdict[-1]}` is the verdict of the `verifier` agent: "
                             f"state it with `--set by=verifier` in the same call, or write "
                             f"`verifier=not-run`"},
                     "refused: a verifier verdict needs `--set by=verifier`")
            return 2
        if verdict and verdict[-1] not in RUN_VERDICTS:
            merged.pop("by", None)
    # The schema's field order, so a record reads the same however it was assembled and a
    # re-stamp never reshuffles what a human wrote.
    ordered = {k: merged[k] for k in fields if k in merged}
    backend.write_spec(info, set_frontmatter_record(info["text"], args.name, ordered))
    out.emit(args.json,
             {"ok": True, "id": info["id"], "record": args.name, "value": ordered},
             f"{info['id']} — {args.name}: "
             f"{{{', '.join(f'{k}: {v}' for k, v in ordered.items())}}}")
    return 0
