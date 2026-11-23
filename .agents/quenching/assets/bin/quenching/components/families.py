"""The verticals of a target's own skills — the one calculation `status` and `migrate` share.

A family is the first kebab segment of a skill's name (`bundle-tabela-criar` -> `bundle`), or the
folder of a legacy `.claude/commands/<a>/<b>.md`. Every skill the target wrote counts, because the
shortcut is gone and `disable-model-invocation` no longer separates anything. A family with
`FAMILY_MIN` or more members is a candidate for one router; a skill already named for the family
makes it `existing`. The `quenching-` prefix belongs to the installed modules and never forms a
family. Read-only: nothing here writes.
"""
from __future__ import annotations

FAMILY_MIN = 3
RESERVED = "quenching"


def family_of(entry: dict) -> tuple[str, str] | None:
    """`(family, flow)` for one entry point, or `None` when it names no family."""
    relpath = entry.get("relpath", "")
    if relpath.endswith(".md") and not relpath.endswith("SKILL.md") and "/" in relpath:
        parts = relpath[:-3].split("/")
        return parts[0], "-".join(parts[1:])
    name = entry["command"].lstrip("/")
    if "-" not in name:
        return None
    family, flow = name.split("-", 1)
    return family, flow


def compute_families(entries: list[dict]) -> dict:
    """Group the target's own entry points by vertical.

    Each family reports `skills` (members), `invocable` (members the model list carries),
    `members` (their names), `flows` (the flow each answers to), `router` (the family name, the
    skill that will route), `kind` (`existing` when a skill of that name is already there, else
    `new`) and `candidate`."""
    names = {entry["command"].lstrip("/") for entry in entries}
    families: dict[str, dict] = {}
    for entry in entries:
        found = family_of(entry)
        if found is None or found[0] == RESERVED or found[0].startswith(RESERVED + "-"):
            continue
        family, flow = found
        item = families.setdefault(family, {"skills": 0, "invocable": 0, "members": [],
                                            "flows": []})
        item["skills"] += 1
        item["members"].append(entry["command"].lstrip("/"))
        item["flows"].append(flow)
        flag = str(entry["frontmatter"].get("disable-model-invocation", "")).lower()
        if flag != "true":
            item["invocable"] += 1
    for family, item in families.items():
        item["router"] = family
        item["kind"] = "existing" if family in names else "new"
        item["candidate"] = item["skills"] >= (1 if item["kind"] == "existing" else FAMILY_MIN)
    return dict(sorted(families.items()))
