"""registry reindex — this pillar OWNS the GENERATED zone format.

Moved verbatim out of the pre-refactor components script.

Until this existed `taxonomy.md` described the row format and two commands reproduced it by hand,
which asked one LLM to both generate a derived table and verify its own output. The zone is
derived here, exactly as the specs pillar owns its own derived listings, and `taxonomy.md` cites
this instead of restating it.
"""
from __future__ import annotations

import json
import os
import pathlib

from quenching.common.io import read_text
from quenching.common.output import finding, report_findings
from quenching.components.surface import _quoted_phrases, load_surface, plural, rel

# the registry's derived zone — markers, cells, and location, per the automation mold
REGISTRY_RELPATH = ("docs", "documentation", "reference", "automation.md")
ZONE_BEGIN = "<!-- GENERATED:BEGIN -->"
ZONE_END = "<!-- GENERATED:END -->"
EMPTY_CELL = "—"


def registry_rows(surface: dict) -> list[dict]:
    """One row per command, derived exclusively from the surface's own command
    frontmatter. Commands contributed by installed plugins are not part of the surface
    and never reach the zone.

    The mirror column is gone with the pair — the command path was always the other
    cell's flattened twin, and a table cannot usefully show a name next to itself."""
    rows = []
    for c in surface["commands"]:
        folder = os.path.dirname(c["relpath"])
        triggers = _quoted_phrases(str(c["frontmatter"].get("description", "")))
        rows.append({
            "command": c["command"],
            "serves": f"{folder}/" if folder else "generic",
            "trigger": triggers[0] if triggers else "",
        })
    return sorted(rows, key=lambda r: r["command"])


def _cell(value: str) -> str:
    """A pipe inside a trigger phrase would end the cell early."""
    return value.replace("|", "\\|")


def render_registry_zone(rows: list[dict]) -> str:
    lines = ["| Command | Serves | Typical trigger |", "| --- | --- | --- |"]
    for r in rows:
        trigger = f'"{_cell(r["trigger"])}"' if r["trigger"] else EMPTY_CELL
        lines.append(f"| {_cell(r['command'])} | {_cell(r['serves'])} | {trigger} |")
    return "\n".join(lines)


def find_registry(registry_arg: str | None, root: str) -> str | None:
    """The climb stops at the first directory holding `.git`: a registry above the target's own
    repository belongs to another repository and is never touched."""
    if registry_arg:
        return os.path.abspath(registry_arg)
    d = root
    while True:
        cand = os.path.join(d, *REGISTRY_RELPATH)
        if os.path.isfile(cand):
            return cand
        if os.path.exists(os.path.join(d, ".git")):
            return None
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def cmd_registry(args, root: str) -> int:
    surface = load_surface(root)
    path = find_registry(args.registry, root)
    if path is None or not os.path.isfile(path):
        return report_findings(
            args.json, f"components registry — {root}", {"root": root},
            [finding("sk-no-registry", "error",
                     f"no registry at {'/'.join(REGISTRY_RELPATH)} within the repository containing {root}", command="-",
                     remedy="install it from assets/templates/automation/registry.md, then rerun")],
            "command")
    text = read_text(path)
    if text is None:
        return report_findings(args.json, f"components registry — {path}", {"root": root},
                               [finding("sk-no-registry", "error",
                                        f"{path} is unreadable", command="-",
                                        remedy="check the file's encoding and permissions")],
                               "command")
    begin, end = text.find(ZONE_BEGIN), text.find(ZONE_END)
    if begin == -1 or end == -1 or end < begin:
        return report_findings(
            args.json, f"components registry — {path}", {"root": root},
            [finding("sk-no-zone", "error",
                     f"the registry carries no `{ZONE_BEGIN}` … `{ZONE_END}` zone", command="-",
                     remedy="add the markers from assets/templates/automation/registry.md — the "
                            "table is never placed at a guessed anchor inside curated prose")],
            "command")
    rows = registry_rows(surface)
    new = f"{text[:begin + len(ZONE_BEGIN)]}\n{render_registry_zone(rows)}\n{text[end:]}"
    changed = new != text
    if changed:
        pathlib.Path(path).write_text(new, encoding="utf-8")
    payload = {"ok": True, "path": rel(path, root), "changed": changed, "rows": len(rows)}
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"components registry — {path}")
        print(f"  {'rewrote' if changed else 'already current —'} "
              f"{plural(len(rows), 'row')} in the GENERATED zone")
    return 0
