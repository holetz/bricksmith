"""The surface model — where the surface is, what a command IS on it, and what else it holds.

Moved verbatim out of the pre-refactor components script.

A **surface root** is the directory holding an automation surface's `commands/` tree.
Two shapes are conformant and both resolve here:

    <target>/.agents/          # a target repo's generic surface (preferred)
      skills/<name>/SKILL.md
    <target>/.claude/          # a target repo's legacy local surface
      commands/<front>/[<object>/]<verb>.md

    plugins/<plugin-name>/     # a packaged plugin's surface (this repo's own shape)
      commands/<front>/[<object>/]<verb>.md

ONE FILE PER ENTRY POINT. A command file carries its own description AND its own body; a
command's path IS its invocation and its identity: `commands/specs/develop.md` is
`/quenching-specs develop`, one `:` per path segment. There is nothing to mirror.

`commands/**` is the ONLY tree Claude Code registers, which is why nothing else may live
there: a `references/` folder beside a command file would surface every reference as a
phantom entry. Such a file carries no `description`, so `sk-no-description` already catches
it — the layout rule needs no check of its own.

`_quoted_phrases` sits at this layer and not in `lint`: `lint` reads a description's quoted
phrases for the trigger-position code and `registry` reads them for the Typical trigger cell,
and neither verb owns the other.
"""
from __future__ import annotations

import json
import os
import re

from quenching.common.deploy import generated_for_skills_dir
from quenching.common.frontmatter import frontmatter_anomalies, parse_frontmatter
from quenching.common.io import read_text
from quenching.components.hooks import parse_frontmatter_hooks

COMMANDS_DIR = "commands"
CLAUDE_DIR = ".claude"
AGENTS_DIR = ".agents"
SKILLS_DIR = "skills"
SKILL_FILE = "SKILL.md"
HOOKS_DIR = "hooks"

# The prose checks reach the references as well as the skill bodies, decided by count:
# of the 519 bare command citations this surface carried on 2026-07-30, 183 lived in
# references, so a scope stopping at the bodies leaves 35% of the prose with no
# net — and a reference is read by the same session that reads the body citing it, so a
# body and its reference disagreeing is exactly the drift worth catching. A surface with no
# such directory (every target repo's `.claude/`) contributes nothing and reports nothing.
REFERENCES_DIR = "references"
PAYLOAD_REFERENCES = ".agents/quenching/references"

SURFACE_MISSING = "—"

FRONTMATTER_FENCE = "---"


QUOTED_RE = re.compile(r"[\"“]([^\"”]{2,}?)[\"”]")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def rel(path: str, base: str) -> str:
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:      # different drives on Windows
        return path.replace(os.sep, "/")


def body_after_frontmatter(text: str) -> str:
    if not text.startswith(FRONTMATTER_FENCE):
        return text
    lines = text.splitlines()
    if lines[0].strip() != FRONTMATTER_FENCE:
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_FENCE:
            return "\n".join(lines[i + 1:])
    return text


def _quoted_phrases(text: str) -> list[str]:
    return [m.group(1).strip() for m in QUOTED_RE.finditer(text) if m.group(1).strip()]


def generic_surface(agents: str) -> bool:
    """`.agents/` is a target's own surface when `skills/` holds a flat `<name>/SKILL.md` and
    no translation manifest marks the tree as generated from `.claude/commands/`."""
    skills = os.path.join(agents, SKILLS_DIR)
    if not os.path.isdir(skills) or os.path.isfile(os.path.join(agents, ".generated-from.json")):
        return False
    return any(os.path.isfile(os.path.join(skills, name, SKILL_FILE)) for name in os.listdir(skills))


def find_surface_root(root_arg: str | None) -> str:
    """The directory holding the surface's entry points. `.agents/skills/` wins over
    `.claude/commands/`, which wins over a bare `commands/` at the same level, so a repo root
    carrying a target surface and a packaged plugin resolves to the target's."""
    if root_arg:
        # A `--root` names a repository root or the surface itself, resolved in the same
        # order as the walk below: the target's own `.agents/skills`, then `.claude/commands`,
        # then a bare `commands/` beside it (a packaged plugin); else the path unchanged,
        # which reports `missing`.
        top = os.path.abspath(root_arg)
        if generic_surface(os.path.join(top, AGENTS_DIR)):
            return os.path.join(top, AGENTS_DIR)
        if os.path.isdir(os.path.join(top, CLAUDE_DIR, COMMANDS_DIR)):
            return os.path.join(top, CLAUDE_DIR)
        if os.path.isdir(os.path.join(top, COMMANDS_DIR)):
            return top
        return top
    env = os.environ.get("SKILLS_ROOT")
    if env:
        return os.path.abspath(env)
    d = os.path.abspath(os.getcwd())
    while True:
        if generic_surface(os.path.join(d, AGENTS_DIR)):
            return os.path.join(d, AGENTS_DIR)
        if os.path.isdir(os.path.join(d, CLAUDE_DIR, COMMANDS_DIR)):
            return os.path.join(d, CLAUDE_DIR)
        if os.path.isdir(os.path.join(d, COMMANDS_DIR)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return os.path.join(os.path.abspath(os.getcwd()), CLAUDE_DIR)


# --------------------------------------------------------------------------- #
# the surface model — what every subcommand reads
# --------------------------------------------------------------------------- #
def command_invocation(relpath: str, prefix: str | None = None) -> str:
    """`specs/develop.md` -> `/quenching-specs develop` on the source surface (the `prefix` is
    `quenching`), `/front:verb` on a target's own `.claude/commands/`. The path IS the identity,
    so this is the whole naming rule; nothing derives a second name to compare it against."""
    parts = relpath[:-3].split("/")
    return "/" + "-".join([prefix, *parts]) if prefix else "/" + ":".join(parts)


def discover_commands(commands_dir: str) -> list[dict]:
    """Every `<commands_dir>/**/*.md`, sorted by invocation — one row per entry point.

    Every `.md` under the tree is a command, including one that should not be there:
    `commands/**` is the only tree Claude Code registers, so a stray file IS a
    registered entry point and must be reported as one, never filtered out here."""
    if not os.path.isdir(commands_dir):
        return []
    prefix = plugin_prefix(os.path.dirname(os.path.abspath(commands_dir)))
    out = []
    for dirpath, _dirnames, filenames in os.walk(commands_dir):
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            relpath = rel(path, commands_dir)
            text = read_text(path) or ""
            body = body_after_frontmatter(text)
            hooks, hooks_parsed = parse_frontmatter_hooks(text)
            out.append({
                "command": command_invocation(relpath, prefix),
                "path": path,
                "relpath": relpath,
                "frontmatter": parse_frontmatter(text),
                "anomalies": frontmatter_anomalies(text),
                "hooks": hooks,
                "hooksParsed": hooks_parsed,
                "body": body,
                "bodyLines": len(body.splitlines()),
            })
    return sorted(out, key=lambda c: c["command"])


def discover_references(references_dir: str) -> list[dict]:
    """Every `<references_dir>/**/*.md` — the shared procedure a command body cites by
    absolute path instead of restating. These are NOT entry points and carry no
    invocation: they are prose the prose checks read, nothing more."""
    if not os.path.isdir(references_dir):
        return []
    out = []
    for dirpath, _dirnames, filenames in os.walk(references_dir):
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            text = read_text(path) or ""
            out.append({
                "path": path,
                "relpath": rel(path, references_dir),
                "body": body_after_frontmatter(text),
            })
    return sorted(out, key=lambda r: r["relpath"])


def surface_references(root: str) -> list[dict]:
    """Every reference of the surface rooted at `root`, keyed by the path a body cites it by.

    A reference lives in one of two places: beside its skill (`skills/<name>/references/`, cited as
    `.agents/skills/<name>/references/<path>`) or in the common set every skill shares (the
    source's `references/`, a target's `.agents/quenching/references/`, cited as
    `.agents/quenching/references/<path>`). `relpath` is the cited path in both."""
    out: list[dict] = []
    common = (os.path.join(root, "quenching", REFERENCES_DIR)
              if os.path.basename(os.path.normpath(root)) == AGENTS_DIR
              else os.path.join(root, REFERENCES_DIR))
    for item in discover_references(common):
        out.append(dict(item, relpath=f"{PAYLOAD_REFERENCES}/{item['relpath']}"))
    skills = os.path.join(root, SKILLS_DIR)
    if os.path.isdir(skills):
        generated = generated_for_skills_dir(skills)
        for name in sorted(os.listdir(skills)):
            if name in generated:
                continue
            for item in discover_references(os.path.join(skills, name, "references")):
                out.append(dict(item, relpath=f".agents/skills/{name}/references/{item['relpath']}"))
    return out


def skill_reference_body(skill_dir: str) -> str:
    """The bodies of a skill's own references, joined: what a router's flows say on its behalf."""
    return "\n".join(item["body"] for item in discover_references(os.path.join(skill_dir, "references")))


def discover_skills(skills_dir: str) -> list[dict]:
    """Every `<skills_dir>/<name>/SKILL.md`, one entry point per skill, invoked `/<name>`.

    A target's generic surface is flat: the Claude Code bridge copies `<name>/` verbatim, and
    Claude Code does not discover a nested `skills/<a>/<b>/SKILL.md`."""
    if not os.path.isdir(skills_dir):
        return []
    out = []
    generated = generated_for_skills_dir(skills_dir)
    for name in sorted(os.listdir(skills_dir)):
        path = os.path.join(skills_dir, name, SKILL_FILE)
        if not os.path.isfile(path) or name in generated:
            continue
        text = read_text(path) or ""
        body = body_after_frontmatter(text)
        hooks, hooks_parsed = parse_frontmatter_hooks(text)
        out.append({
            "command": "/" + name,
            "path": path,
            "relpath": f"{name}/{SKILL_FILE}",
            "frontmatter": parse_frontmatter(text),
            "anomalies": frontmatter_anomalies(text),
            "hooks": hooks,
            "hooksParsed": hooks_parsed,
            "body": body,
            "refBody": skill_reference_body(os.path.dirname(path)),
            "bodyLines": len(body.splitlines()),
        })
    return out


def entry_points_dir(root: str) -> str:
    """`<root>/skills` for a generic surface that has no `commands/`, else `<root>/commands`."""
    skills = os.path.join(root, SKILLS_DIR)
    if os.path.isdir(skills) and not os.path.isdir(os.path.join(root, COMMANDS_DIR)):
        return skills
    return os.path.join(root, COMMANDS_DIR)


def discover_entry_points(root: str) -> list[dict]:
    """The entry points of the surface rooted at `root`, commands or skills."""
    entry = entry_points_dir(root)
    if os.path.basename(entry) == SKILLS_DIR:
        return discover_skills(entry)
    return discover_commands(entry)


def load_surface(root: str) -> dict:
    entry = entry_points_dir(root)
    commands = discover_entry_points(root)
    generated = sorted(generated_for_skills_dir(entry)) if os.path.basename(entry) == SKILLS_DIR else []
    return {
        "root": root,
        "commandsDir": entry,
        "commands": commands,
        "generated": generated,
    }


def plugin_prefix(root: str) -> str | None:
    """The registry prefix this surface's commands carry: `quenching` for the source of quenching
    (the root with `VERSION` and `assets/modules.json`). `None` says the surface is a target repo's
    own `.claude/` — where a command file IS in `.claude/commands/`, the bare form is the one that
    resolves, and the citation check has nothing to say."""
    if os.path.isfile(os.path.join(root, "VERSION")) and os.path.isfile(os.path.join(root, "assets", "modules.json")):
        return "quenching"
    return None


def claude_side(root: str) -> str:
    """Where the Claude-only artefacts of the surface rooted at `root` live.

    A generic `.agents/` surface keeps its subagents, hooks and `settings*.json` in the sibling
    `.claude/`, which no generic home replaces; every other root holds them itself."""
    if os.path.basename(os.path.normpath(root)) == AGENTS_DIR:
        return os.path.join(os.path.dirname(os.path.normpath(root)), CLAUDE_DIR)
    return root


def agent_definitions(root: str) -> list[tuple[str, dict]]:
    """`(filename, frontmatter)` for every `<root>/agents/*.md`, sorted — for a generic
    `.agents/` root, the sibling `.claude/agents/`.

    Read by `doctor` (which checks each definition is reachable)."""
    agents_dir = os.path.join(claude_side(root), "agents")
    if not os.path.isdir(agents_dir):
        return []
    return [(fn, parse_frontmatter(read_text(os.path.join(agents_dir, fn)) or ""))
            for fn in sorted(os.listdir(agents_dir)) if fn.endswith(".md")]
