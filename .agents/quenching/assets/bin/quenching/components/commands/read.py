"""read — N sections of any markdown file, in ONE call.

Moved verbatim out of the pre-refactor components script. The rule itself is `quenching.components.sections`; this is
the verb over it — the heading index when nothing was asked for, the refusal that names an
absent section, and the `--rules-only` note that keeps a missing marker from becoming silence.
"""
from __future__ import annotations

import json
import pathlib

from quenching.components.sections import (RULES_MARKER, expand_section_args, markdown_sections,
                                           select_sections, split_rule_and_rationale)


_SOURCE = pathlib.Path(__file__).resolve().parents[5]
_INSTALLED = ((".agents/skills/", "skills/"), (".agents/quenching/references/", "references/"))


def resolve_installed(path: pathlib.Path) -> pathlib.Path:
    """A path in the installed form (`.agents/skills/<name>/…`, `.agents/quenching/references/…`)
    that is not on disk reads from the source it was installed from, when this `cq` runs beside one.
    A file that exists is never redirected."""
    if path.is_file():
        return path
    text = path.as_posix()
    for installed, source in _INSTALLED:
        if text.startswith(installed):
            candidate = _SOURCE / source / text[len(installed):]
            if candidate.is_file():
                return candidate
    return path


def cmd_read(args, root: str) -> int:
    path = resolve_installed(pathlib.Path(args.path))
    if not path.is_file():
        payload = {"ok": False, "code": "sk-read-no-file", "path": str(path),
                   "message": f"{path} is not a file"}
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json
              else f"error: {path} is not a file")
        return 2
    heads = markdown_sections(path.read_text(encoding="utf-8"))

    if not args.sections:
        index = [{"heading": h["heading"], "level": h["level"], "chars": len(h["body"])}
                 for h in heads]
        if args.json:
            print(json.dumps({"ok": True, "path": str(path), "index": index},
                             indent=2, ensure_ascii=False))
        else:
            print(f"{path} — {len(index)} sections")
            for h in index:
                print(f"  {'  ' * (h['level'] - 1)}{'#' * h['level']} {h['heading']}"
                      f"  ({h['chars']} chars)")
        return 0

    wanted = expand_section_args(heads, args.sections)
    got, missing = select_sections(heads, wanted)
    if missing:
        # A refusal, never an empty answer: a caller that asked for a rule and got
        # silence proceeds as though the rule did not exist.
        payload = {"ok": False, "code": "sk-read-no-section", "path": str(path),
                   "missing": missing,
                   "available": [h["heading"] for h in heads],
                   "message": f"{path} has no section named: {', '.join(missing)}"}
        print(json.dumps(payload, indent=2, ensure_ascii=False) if args.json
              else f"error: {path} has no section named: {', '.join(missing)}\n"
                   f"  available: {', '.join(h['heading'] for h in heads)}")
        return 2

    rows = []
    for h in got:
        body, marked = ((h["body"], True) if not args.rules_only
                        else split_rule_and_rationale(h["body"]))
        rows.append({"heading": h["heading"], "level": h["level"], "body": body,
                     **({"marked": marked} if args.rules_only else {})})

    if args.json:
        print(json.dumps({"ok": True, "path": str(path), "rulesOnly": args.rules_only,
                          "sections": rows}, indent=2, ensure_ascii=False))
    else:
        for r in rows:
            print(f"{'#' * r['level']} {r['heading']}\n\n{r['body']}\n")
        unmarked = [r["heading"] for r in rows if r.get("marked") is False]
        if unmarked:
            # Stated, never silent: the caller asked for the rule half and got the whole
            # section, and a reader that does not know which one it holds cannot tell a
            # compact rule from a section nobody has marked up yet.
            print(f"note: no {RULES_MARKER} marker in: {', '.join(unmarked)} — returned "
                  f"the whole section")
    return 0
