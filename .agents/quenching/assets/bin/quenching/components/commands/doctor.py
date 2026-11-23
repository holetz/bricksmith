"""doctor — the surface's shape, plus the report-only inventory of everything beside it.

Moved verbatim out of the pre-refactor components script.

`lint` judges one command against the doctrine; `doctor` judges the surface as a whole, which
is where the front's convergence condition actually lives. Every finding carries a `remedy` the
sweep applies rather than invents, the same contract `cq specs doctor` already gives the specs
front.

WHAT REPLACED THE BIJECTION. This used to check that every command had exactly one mirrored
wrapper and vice versa. With one file per entry point there is no second half that can be
missing, duplicated, dangling, or misnamed, so `sk-orphan-skill`, `sk-dangling-wrapper`,
`sk-duplicate-wrapper`, `sk-wrapper-no-target` and `sk-path-mismatch` are deleted rather than
reinterpreted. The invariant that took their place is narrower and total: every command carries
a non-empty `description`, no two resolve to the same `/` path, and every segment is kebab-case.

The bijection did catch a real class of error — a command minted without a wrapper — and nothing
replaces it, because after the collapse that state cannot exist.
"""
from __future__ import annotations

import json
import os
import re

from quenching.common.io import read_text
from quenching.common.output import finding, report_findings
from quenching.components.commands.lint import lint_dangling
from quenching.components.hooks import hook_ladder_findings
from quenching.components.surface import (COMMANDS_DIR, SKILL_FILE, SURFACE_MISSING,
                                          agent_definitions, claude_side, load_surface,
                                          plugin_prefix, plural)

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _doctor_findings(surface: dict) -> list[dict]:
    """Doctor's checks as a pure function, so `selftest` can assert on them without
    parsing printed output or re-implementing the rules it is meant to be proving."""
    commands = surface["commands"]
    findings: list[dict] = []

    seen_lower: dict[str, str] = {}
    for c in commands:
        where = {"command": c["command"], "path": c["relpath"]}

        # a stray file under commands/ IS a registered entry point with no way to be
        # selected — the phantom-command failure mode, caught by a check that existed
        if not str(c["frontmatter"].get("description", "")).strip():
            findings.append(finding("sk-no-description", "error",
                                    f"{c['command']} has no `description` — it registers as a "
                                    "command that can never be selected; if it is not an entry "
                                    f"point it does not belong under {COMMANDS_DIR}/", **where,
                                    remedy="add a description, or move the file out of "
                                           f"{COMMANDS_DIR}/ (shared procedure belongs beside the "
                                           "payload, never beside a command)"))

        # a generic skill is named by its folder (`/<name>` from `<name>/SKILL.md`); a
        # `SKILL.md` under commands/ is still a command path and is checked whole
        skill = (os.path.basename(c["relpath"]) == SKILL_FILE
                 and c["command"] == "/" + os.path.dirname(c["relpath"]))
        stem = os.path.dirname(c["relpath"]) if skill else c["relpath"][:-3]
        for segment in stem.split("/"):
            if not KEBAB_RE.match(segment):
                findings.append(finding("sk-non-canonical-name", "error",
                                        f"`{segment}` in {c['command']} is not kebab-case",
                                        **where,
                                        remedy="rename the path segment to kebab-case (gate a "
                                               "code-coupled rename on its own)"))

        clash = seen_lower.setdefault(c["command"].lower(), c["command"])
        if clash != c["command"]:
            findings.append(finding("sk-name-collision", "error",
                                    f"{c['command']} collides with {clash} on a case-insensitive "
                                    "filesystem", **where,
                                    remedy="rename one of the two commands"))

    if plugin_prefix(surface["root"]) is None:
        # A target's own surface: the bare form is the one that resolves, so a citation of a
        # front it has but a verb it lost is a removal nobody swept.
        invocations = {c["command"] for c in commands}
        for c in commands:
            findings.extend(lint_dangling(c["body"], invocations,
                                          {"command": c["command"], "path": c["relpath"]}))

    findings.extend(_wider_findings(surface["root"]))
    return findings


def _wider_findings(root: str) -> list[dict]:
    """REPORT-ONLY inventory of the surfaces beside commands/: subagent definitions
    (`<root>/agents/*.md`) and the hooks wired in `<root>/settings*.json`. The sweep never
    renames or rewrites anything here — each finding names the mint that owns the fix
    (/quenching-components agent-new, /quenching-components hook-new), so the confirmed plan's
    write set stays exactly the command surface's."""
    findings: list[dict] = []

    for fn, fm in agent_definitions(root):
        if not str(fm.get("description", "")).strip():
            findings.append(finding(
                "sk-agent-no-description", "error",
                f"agents/{fn} has no `description` — the agent can never be delegated to",
                command=f"agents/{fn}", path=f"agents/{fn}",
                remedy="add a description stating what it does and when to invoke it "
                       "(/quenching-components agent-new)"))

    for settings_name in ("settings.json", "settings.local.json"):
        text = read_text(os.path.join(claude_side(root), settings_name))
        if text is None:
            continue
        try:
            hooks = json.loads(text).get("hooks", {})
        except (json.JSONDecodeError, AttributeError):
            findings.append(finding(
                "sk-hook-unparseable", "error",
                f"{settings_name} is not valid JSON — every hook wired in it is dead",
                command=settings_name, path=settings_name,
                remedy="repair the JSON (`python3 -m json.tool` names the position)"))
            continue
        if not isinstance(hooks, dict):
            continue
        findings.extend(hook_ladder_findings(
            hooks, where_in=settings_name,
            where={"command": settings_name, "path": settings_name}))
    return findings


def cmd_doctor(args, root: str) -> int:
    surface = load_surface(root)
    if not os.path.isdir(surface["commandsDir"]):
        return report_findings(
            args.json, f"components doctor — {root}", {"root": root},
            [finding("sk-no-surface", "error",
                     f"no {COMMANDS_DIR}/ under {root}", command=SURFACE_MISSING,
                     remedy="point --root at the surface, or scaffold "
                            f"{COMMANDS_DIR}/")], "command")
    commands = surface["commands"]
    payload = {"root": root, "commands": len(commands)}
    return report_findings(args.json, f"components doctor — {root} "
                                      f"({plural(len(commands), 'command')})", payload,
                           _doctor_findings(surface), "command")
