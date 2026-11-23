"""`new` — capture a spec into the configured backend in ONE call: frontmatter, `tags`,
`priority.complexity` and N sections, all spliced into the body IN MEMORY before the single
`backend.create_spec`."""
from __future__ import annotations

import json
import re
import sys

from quenching.common.dates import today
from quenching.specs.backends import open_backend
from quenching.specs.commands.fields import record_field_value_error
from quenching.specs.commands.output import Emitter, display_locator
from quenching.specs.commands.push import card_failures, card_warnings
from quenching.specs.config import (azure_workitemtype_retirement, load_config,
                                    resolve_subject, resolve_type_key)
from quenching.specs.parse import derive_info
from quenching.specs.parse.edit import split_section_stream, upsert_section
from quenching.specs.parse.fields import (scalar_break, set_frontmatter_record,
                                          yaml_title_scalar)
from quenching.specs.parse.sections import section_state
from quenching.specs.schema import (DEFAULT_VERIFICATION, canonical_headings, capture_form,
                                    load_schema, section_guidance)


def _tags_repr(tags: list[str]) -> str:
    return "[" + ", ".join(json.dumps(t, ensure_ascii=False) for t in tags) + "]"


def cmd_new(args, root: str, out: Emitter) -> int:
    """Capture a titled spec, carrying whatever the capture supplies — `## Problem`
    alone by default, or every flag and section a caller hands in.

    THE DATE IS STAMPED HERE AND NEVER AGAIN, into the frontmatter's `date:`. The positional
    argument is the title; the provider assigns the spec's identity when it stores the body.

    EVERY REFUSAL IS SETTLED BEFORE THE ONE `create_spec`, exactly as `cmd_section`'s stream
    write already does: `--complexity` is validated and the stdin stream is decomposed and
    checked before a single byte of the document is assembled, so a rejected capture leaves
    no orphaned issue behind on a backend like `github` that has no local undo.

    `sp-write-set-mismatch` does not apply here: unlike `section --write`, `new` declares no
    heading set on the command line — the stream IS the declaration, so a stream that never
    opens on a canonical heading has no single implied heading to fall back on and refuses
    instead."""
    bad = scalar_break(args.title or args.name)
    if bad:
        out.emit(args.json,
                 {"ok": False, "code": "sp-bad-title", "char": bad,
                  "message": f"a title is one line with no control character — got {bad}"},
                 f"error: a title is one line with no control character — got {bad}")
        return 2
    bad = scalar_break(args.tags or "")
    if bad:
        out.emit(args.json,
                 {"ok": False, "code": "sp-bad-scalar", "field": "tags", "char": bad,
                  "message": f"a tag carries no line break or control character — got {bad}"},
                 f"error: a tag carries no line break or control character — got {bad}")
        return 2
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    cfg = load_config(root)
    subject, serr = resolve_subject(cfg, args.subject)
    if serr:
        return out.emit_err(args.json, serr)
    type_key, terr = resolve_type_key(cfg, args.type)
    if terr:
        return out.emit_err(args.json, terr)
    if cfg["backend"] == "azure-boards":
        retirement = azure_workitemtype_retirement(cfg, type_key)
        if retirement and retirement["severity"] == "error":
            return out.emit_err(args.json, retirement)
        if retirement:
            print(f"note: {retirement['message']}", file=sys.stderr)

    schema = load_schema()

    # `--complexity` — validated against the SAME per-field rule `cq specs record` enforces,
    # so a bad level refuses with `sp-bad-complexity` before anything is written.
    complexity_record = None
    if args.complexity:
        rspec = schema.get("frontmatter", {}).get("records", {}).get("priority", {})
        rerr = record_field_value_error(rspec, "complexity", args.complexity)
        if rerr:
            out.emit(args.json,
                     {"ok": False, "code": "sp-bad-complexity", "given": args.complexity,
                      "levels": rspec.get("complexity", {}).get("levels", []),
                      "message": rerr}, f"refused: {rerr}")
            return 2
        complexity_record = {"complexity": args.complexity}

    # The stdin stream — read once, decomposed and checked before the document exists at all.
    content = sys.stdin.read() if not sys.stdin.isatty() else ""
    blocks: list[tuple[str | None, str]] = []
    if content.strip():
        blocks = split_section_stream(content, canonical_headings(schema))
        if not blocks:
            msg = ("stdin does not open on a canonical `## <Heading>` line — the stream IS "
                   "the declaration for `cq specs new`, with no single implied heading to "
                   "fall back on the way `section --write` falls back on the one it was given")
            out.emit(args.json,
                     {"ok": False, "code": "sp-stray-heading", "source": "stream",
                      "canonical": canonical_headings(schema), "message": msg},
                     f"error: {msg}")
            return 2
        unresolved = [i for i, (h, _) in enumerate(blocks, 1) if h is None]
        if unresolved:
            out.emit(args.json,
                     {"ok": False, "code": "sp-stray-heading", "source": "stream",
                      "unresolvedBlocks": unresolved, "canonical": canonical_headings(schema),
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

    policy = args.verification or DEFAULT_VERIFICATION
    title = args.title or args.name
    # The title goes in last and in one pass, so a `<DATE>`, `<VERIFICATION>` or `<TITLE>` the
    # title itself carries is never rescanned as a template placeholder.
    template = (capture_form(schema=schema)
                .replace("<DATE>", today())
                .replace("<VERIFICATION>", policy))
    body = re.sub(r"title: <TITLE>|<TITLE>",
                  lambda m: (f"title: {yaml_title_scalar(title)}"
                             if m.group(0) != "<TITLE>" else title),
                  template)
    # The subject's fixed tags go into the CANONICAL DOCUMENT, never applied to the tracker
    # directly: `tags:` is a recognised frontmatter key, so `create_spec` reads them off this
    # text through the same path every other stored field takes — never a second,
    # backend-specific write path of this command's own. `--tags` REPLACES the whole list,
    # exactly as `cq specs tags` does, so the subject's fixed tags are folded into it here
    # rather than lost.
    tags: list[str] = []
    if args.tags:
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    if subject and subject.get("tags"):
        tags = tags + [t for t in subject["tags"] if t not in tags]
    if tags:
        close = body.index("\n---\n")
        body = body[:close] + f"\ntags: {_tags_repr(tags)}" + body[close:]
    # The abstract catalogue key, never the backend's native name — `create_spec` reads it
    # off `fresh["frontmatter"]` and projects the native name at write time (§Design).
    if type_key:
        close = body.index("\n---\n")
        body = body[:close] + f"\nworkItemType: {type_key}" + body[close:]
    if complexity_record:
        body = set_frontmatter_record(body, "priority", complexity_record)
    if blocks:
        # Re-derived between splices, exactly as `cmd_section`'s own stream write does: each
        # section's write invalidates the next one's line offsets.
        cur = derive_info({"phase": "plans"}, body)
        for heading, section_body in blocks:
            block = (f"## {heading}\n\n{section_body.strip()}\n"
                     if section_body.strip() else section_guidance(heading))
            body, _ = upsert_section(cur, heading, block)
            cur = derive_info({"phase": "plans"}, body)
    # The parent, by contrast, has no canonical-document counterpart to carry it in — it is
    # `azure-boards`-only, applied through the SAME `self.parent_id` the backend already
    # reaffirms on every write (§2.4); a resolved subject here simply overrides the
    # `defaultSubject` `open_azure_backend` applied when the backend was opened.
    if subject and subject.get("parent") and hasattr(backend, "parent_id"):
        backend.parent_id = subject["parent"]
    card_args = {}
    if getattr(args, "card", None) is not None:
        if not hasattr(backend, "read_card"):
            return out.emit_err(args.json, {
                "code": "sp-card-unsupported", "exit": 2,
                "message": "--card adopts an existing issue or work item into the branch "
                           f"store; backend '{backend.name}' keeps the spec in the tracker "
                           "item itself — nothing was written"})
        # Adoption: the item's own text becomes `## Problem` when the capture supplied none,
        # and the item is rewritten to the thin card once the spec is on the branch.
        item = backend.read_card(args.card)
        cur = derive_info({"phase": "plans"}, body)
        if item["body"].strip() and section_state(cur["sections"], "Problem") != "filled":
            body, _ = upsert_section(cur, "Problem", f"## Problem\n\n{item['body'].strip()}\n")
        card_args = {"card": args.card}
    path = backend.create_spec("plans", body, **card_args)
    # The provider-native id is the locator's trailing number (issue or work-item URL); a
    # caller chaining `validate --spec <id>` must not have to parse the URL to get it.
    id_match = re.search(r"(\d+)(?:\.md)?/?(?:[?#].*)?$", path or "")
    spec_id = int(id_match.group(1)) if id_match else None
    id_note = f"  id: {spec_id}" if spec_id is not None else ""
    # The hint follows the document that was actually written: a `## Problem` that arrived on
    # stdin (or by `--card`) is already there, so only a bare capture is told to write one.
    wrote = section_state(derive_info({"phase": "plans"}, body)["sections"], "Problem") == "filled"
    target = f"--spec {spec_id}" if spec_id is not None else "--spec <id>"
    next_step = (f"cq specs validate {target}" if wrote
                 else "write ## Problem, then continue with the provider-native spec ID")
    # The sync that wrote the spec may have carried other pending specs whose card could not
    # follow: the spec exists either way, so that is a warning beside it, never a failure.
    failures = card_failures(backend)
    out.emit(args.json,
             {"ok": True, "id": spec_id, "title": title, "verification": policy,
              "workItemType": type_key,
              "phase": "plans", "stage": "captured",
              "path": display_locator(path, root),
              **({"cardFailures": failures} if failures else {})},
             f"created '{title}'  (verification: {policy}){id_note}\n"
             f"next: {next_step}" + card_warnings(failures))
    return 0
