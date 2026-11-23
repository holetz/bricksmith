"""lint — the per-command conformance checks, and the citation form a plugin's prose must use.

Moved verbatim out of the pre-refactor components script.

Every check here is decidable FROM THE FILE. The doctrine's remaining tests — the no-op test,
sediment, sprawl, positive prescription — stay a human read in `/quenching-components align` Stage 2, because
each needs a claim about behaviour that no parser can make. Adding a heuristic for one of them
would move the plugin's anti-fabrication boundary, not the tool's coverage.
"""
from __future__ import annotations

import os
import re

from quenching.common.output import finding, report_findings
from quenching.common.io import read_text
from quenching.common.frontmatter import parse_frontmatter
from quenching.components.cq_calls import check_cq_calls, extract_calls
from quenching.components.hooks import hook_ladder_findings
from quenching.components.surface import (COMMANDS_DIR, REFERENCES_DIR, SKILL_FILE,
                                          SURFACE_MISSING,
                                          _quoted_phrases, agent_definitions,
                                          body_after_frontmatter, discover_commands,
                                          discover_entry_points, discover_references,
                                          discover_skills, entry_points_dir, plugin_prefix,
                                          plural, rel, surface_references)

# conformance thresholds — the normative statement of the front's mechanical rules.
# `doctrine.md` and `taxonomy.md` cite the sk-* codes rather than restating these numbers,
# so the tool and the doctrine cannot drift apart.
#
# Every character count here is taken on the PARSED value, never on the YAML source.
# A description written as a folded `>-` block costs its folded string — the block
# indentation and the newlines are syntax the parser removes before Claude Code ever
# sees the field. Counting the source lines instead inflates a description by roughly
# two characters per line, which is enough to invent a cap violation that the model
# never pays for.
CAP_METADATA = 1536             # the description; Claude Code truncates past it
CAP_DESCRIPTION_PORTABLE = 1024  # the Agent Skills standard's hard limit
CAP_DESCRIPTION_BUDGET = 250    # the budget: use case first, so the listing stays cheap
CAP_BODY_LINES = 500
BODY_BYTES_BUDGET = 12 * 1024   # a body is re-sent every turn it governs; past this it warns
BODY_SIZE_OPT_OUT_RE = re.compile(r"^<!--\s*body-size:\s*justified\b[^>]*-->\s*$", re.MULTILINE)
TRIGGER_SENTENCE_MAX = 2        # triggers live by the second sentence, so truncation keeps them
BOUNDARY_MARKER = "Not for:"
DONE_WHEN_MARKER = "**Done when:**"
UNSCOPED_TOOLS = ("Bash",)      # granting the whole shell for the turn
EFFORT_VALUES = ("low", "medium", "high", "xhigh", "max")

# A slash citation of a command in either spelling. The target dialect is the hyphenated id,
# `/quenching-knowledge add`; the retired spelling carries at least one `:`,
# `/quenching-knowledge add` or the bare `/knowledge:add`. The lookbehind rejects a citation
# already prefixed by a path or a scheme (`https://`, `.agents/quenching/…`), and requiring a
# segment after the `:` keeps a bare namespace (`/quenching-components-`) out: naming the
# namespace is not citing a command.
CITATION_RE = re.compile(r"(?<![\w:/-])/([a-z0-9-]+(?::[a-z0-9-]+)+)")
HYPHEN_CITATION_RE = re.compile(r"(?<![\w:/.-])/([a-z0-9]+(?:-[a-z0-9]+)+)(?![\w:-])")
CITATION_SAMPLE = 3      # examples carried in the message; the count carries the rest

HEADING_RE = re.compile(r"^#{1,6}\s")
STEP_HEADING_RE = re.compile(r"^#{2,6}\s+\d+[.)]\s")
STEP_ITEM_RE = re.compile(r"^\d+[.)]\s")
WORKFLOW_MARKER_RE = re.compile(r"^(?:\*\*Steps\*\*|#{1,6}\s+(?:Steps|Workflow)\b)", re.IGNORECASE)
WORKFLOW_HEADING_RE = re.compile(r"^(#{1,6})\s+(?:Steps|Workflow)\b", re.IGNORECASE)
UNNUMBERED_ITEM_RE = re.compile(r"^[-*]\s+\S")
DONE_WHEN_RE = re.compile(re.escape(DONE_WHEN_MARKER))

FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(.*)$")
KNOWN_FRONTMATTER_KEYS = {
    "name", "description", "argument-hint", "allowed-tools", "disallowed-tools",
    "user-invocable", "disable-model-invocation", "effort", "context", "agent",
    "background", "paths", "hooks",
}

GATE_PATTERNS = (
    re.compile(r"\bwait\s+for\b[^\n]{0,80}\bconfirmation\b", re.IGNORECASE),
    re.compile(r"\b(?:one|single)\s+confirmation\b[^\n]{0,80}\b(?:before|gates?|executes?)\b",
               re.IGNORECASE),
    re.compile(r"\bconfirmation\b[^\n]{0,80}\b(?:before|on|gates?)\b", re.IGNORECASE),
    re.compile(r"\bask(?:s|ed)?\b[^\n]{0,80}\b(?:confirmation|yes|OK)\b", re.IGNORECASE),
    re.compile(r"\bon\s+(?:its\s+own\s+)?(?:a\s+)?yes\b", re.IGNORECASE),
)

SHELL_COMMANDS = ("git", "gh", "az", "python3", "py", "rm", "mv", "mkdir", "find",
                  "grep", "rg", "mktemp", "npx", "uv", "zensical")
SHELL_COMMAND_RE = re.compile(r"^(?:" + "|".join(SHELL_COMMANDS) + r")\b")


def _split_sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _split_tools(spec: str) -> list[str]:
    """Split `Read, Bash(git add:*, git commit:*), Edit` on top-level commas only — a
    scoped grant may carry commas inside its parentheses."""
    out, depth, cur = [], 0, ""
    for ch in spec:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    out.append(cur.strip())
    return [t for t in out if t]


def _numbered_steps(body: str) -> list[str]:
    """Return explicit workflow steps, including unnumbered sequences.

    A child heading under `## Workflow`/`## Steps` is an operational step even without a
    numeric prefix. When the workflow uses a list instead, at least two top-level list items
    form the explicit sequence; a lone bullet remains prose. Lists outside those markers are
    never steps, so ordinary narrative and report tables stay outside this check.
    """
    steps: list[str] = []
    workflow_level: int | None = None
    workflow_steps: list[str] = []
    workflow_items: list[str] = []

    def close_workflow() -> None:
        nonlocal workflow_level, workflow_steps, workflow_items
        if workflow_steps:
            steps.extend(workflow_steps)
        elif len(workflow_items) >= 2:
            steps.extend(workflow_items)
        workflow_level = None
        workflow_steps = []
        workflow_items = []

    for line in body.splitlines():
        marker = WORKFLOW_HEADING_RE.match(line)
        if marker or WORKFLOW_MARKER_RE.match(line):
            close_workflow()
            workflow_level = len(marker.group(1)) if marker else 0
            continue

        if workflow_level is None:
            if STEP_HEADING_RE.match(line):
                steps.append(line.strip())
            continue

        if STEP_HEADING_RE.match(line):
            workflow_steps.append(line.strip())
            continue

        heading = re.match(r"^(#{1,6})\s+\S", line)
        if heading:
            level = len(heading.group(1))
            if level <= workflow_level:
                close_workflow()
                if STEP_HEADING_RE.match(line):
                    steps.append(line.strip())
            elif level == workflow_level + 1:
                workflow_steps.append(line.strip())
            continue

        if not line[:1].isspace() and (STEP_ITEM_RE.match(line) or UNNUMBERED_ITEM_RE.match(line)):
            workflow_items.append(line.strip())

    close_workflow()
    return steps


def _step_criteria(body: str) -> tuple[int, int]:
    """(steps, steps carrying a `**Done when:**` criterion). A criterion belongs to the
    step it follows, so the body is walked once and each marker credited to the step
    most recently opened."""
    steps = _numbered_steps(body)
    if not steps:
        return 0, 0
    opened = set(steps)
    covered, current = set(), None
    for line in body.splitlines():
        stripped = line.strip()
        if stripped in opened:
            current = stripped
        elif current and DONE_WHEN_RE.search(line):
            covered.add(current)
    return len(steps), len(covered)


def _body_has_confirmation_gate(body: str) -> bool:
    """Recognise a user-decision gate in prose, while leaving historical/negative wording alone."""
    fenced = False
    for line in body.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        for pattern in GATE_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            prefix = line[:match.start()].lower()
            if re.search(r"\b(?:not|never|no)\s*$", prefix):
                continue
            if re.search(r"\b(?:not|never|no)\s+(?:to\s+)?(?:ask|wait|confirmation)", prefix):
                continue
            return True
    return False


def _shell_command_lines(body: str) -> list[tuple[int, str]]:
    """Return executable-looking shell commands from fenced blocks, with body line numbers.

    Prose and examples outside a shell fence are not execution claims. ``cq`` is intentionally not
    in this inventory: command bodies use it as the unresolved shorthand that the preceding tool
    resolution step replaces with a literal path, so treating the shorthand as a grant would report
    the notation rather than the operation.
    """
    out: list[tuple[int, str]] = []
    fenced = False
    for lineno, line in enumerate(body.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced or not stripped or stripped.startswith("#"):
            continue
        for part in re.split(r"\s*(?:&&|\|\|)\s*", stripped):
            part = re.sub(r"^(?:if|then|else|elif)\s+", "", part).strip()
            if not SHELL_COMMAND_RE.match(part):
                continue
            tokens = part.split()
            if tokens[0] == "git" and len(tokens) >= 2 and tokens[1] == "-C":
                if len(tokens) < 4 or tokens[2].startswith("<"):
                    continue
                tokens = [tokens[0], *tokens[3:]]
            if tokens[0] == "git" and len(tokens) < 2:
                continue
            out.append((lineno, " ".join(tokens)))
    return out


def _bash_grants(fm: dict) -> tuple[bool, list[str]]:
    """Return whether all shell commands are granted, plus each scoped Bash prefix."""
    prefixes: list[str] = []
    for tool in _split_tools(str(fm.get("allowed-tools", ""))):
        if tool == "Bash":
            return True, []
        if not tool.startswith("Bash(") or not tool.endswith(")"):
            continue
        spec = tool[5:-1]
        prefixes.append(spec[:-2] if spec.endswith(":*") else spec)
    return False, prefixes


def _grant_covers(command: str, prefixes: list[str]) -> bool:
    return any(command == prefix or command.startswith(prefix + " ") for prefix in prefixes)


def _cq_call_findings(body: str, where: dict) -> list[dict]:
    """A `cq` call the parsers of the pillar it names would refuse — the verb that does not exist,
    the required option left out, the flag nobody declared. One finding per call, at its line."""
    out = []
    for issue in check_cq_calls(body):
        code = "sk-cq-unknown-verb" if issue.kind == "unknown-verb" else "sk-cq-flag"
        out.append(finding(code, "error",
                           f"`{issue.call}` at body line {issue.line}: {issue.detail}",
                           invocation=issue.call, line=issue.line, **where))
    return out


def _granted_words(binary: str, args: list[str]) -> list[str]:
    """The call's words without its options — the grant a missing one would need, never what the
    harness matches: that is the call as written."""
    words, skip = [binary], False
    for arg in args:
        if skip:
            skip = False
        elif arg in ("--root", "-C"):
            skip = True
        elif not arg.startswith("-"):
            words.append(arg)
    return words


def _agent_grant_findings(body: str, tools: str, where: dict) -> list[dict]:
    """Every `cq`, `git` or `gh` an agent body tells it to run must sit under a `Bash(<prefix>:*)`
    of its own `tools:` — the agent has no other grant to fall back on."""
    unrestricted, prefixes = _bash_grants({"allowed-tools": tools})
    if unrestricted:
        return []
    out, flagged = [], set()
    for call in extract_calls(body, ("cq", "git", "gh")):
        if call.binary == "cq" and not any(not a.startswith("-") for a in call.args):
            continue
        words = _granted_words(call.binary, call.args)
        form = " ".join(words[:3 if call.binary == "cq" else 2])
        if _grant_covers(call.text, prefixes):
            continue
        displaced = _grant_covers(" ".join(words), prefixes)
        if (form, displaced) in flagged:
            continue
        flagged.add((form, displaced))
        if displaced:
            remedy = (f"put the option after the verb, or `cd` first: `{form}` is granted, but the "
                      "harness matches the prefix literally")
            detail = (f"`{call.text}` at body line {call.line} puts an option before its verb, "
                      f"outside `Bash({form}:*)` — " + remedy)
        else:
            remedy = f"add `Bash({form}:*)` to tools or remove the call"
            detail = (f"`{form}` at body line {call.line} is called without a matching "
                      "scoped Bash grant — " + remedy)
        out.append(finding("sk-agent-grant-gap", "error", detail,
                           tool=form, invocation=call.text, line=call.line, remedy=remedy,
                           **where))
    return out


def _agent_disallowed_bash_findings(disallowed: str, where: dict, tools: str = "",
                                    body: str = "") -> list[dict]:
    """A `Bash(...)` pattern in an agent's `disallowedTools` removes the whole Bash tool
    (measured, Claude Code 2.1.294), so the agent's scoped `tools:` grants silently vanish. A
    bare `Bash` there is the same deny: a fence when the agent never uses shell, a contradiction
    when `tools:` grants Bash or the body runs `cq`, `git` or `gh`."""
    out = []
    uses_shell = None
    for tool in _split_tools(disallowed):
        if tool.startswith("Bash("):
            remedy = "drop it: fence Bash through the scoped grants of `tools:`"
            out.append(finding("sk-agent-disallowed-bash", "error",
                               f"`disallowedTools: {tool}` removes the whole Bash tool from the "
                               "agent — " + remedy, tool=tool, remedy=remedy, **where))
        elif tool == "Bash":
            if uses_shell is None:
                uses_shell = (any(t == "Bash" or t.startswith("Bash(") for t in _split_tools(tools))
                              or bool(extract_calls(body, ("cq", "git", "gh"))))
            if uses_shell:
                remedy = "drop the deny, or drop the Bash grants and the shell calls of the body"
                out.append(finding("sk-agent-disallowed-bash", "error",
                                   "`disallowedTools: Bash` removes the whole Bash tool, yet the "
                                   "agent's `tools:` or body still need it — " + remedy,
                                   tool=tool, remedy=remedy, **where))
    return out


def _agent_skill_findings(tools: str, where: dict) -> list[dict]:
    """`Skill` reaches every model-invocable skill, whose `allowed-tools` widen the agent that runs
    it, and `Skill(<name>)` in `tools:` restricts nothing (measured, Claude Code 2.1.294): an agent
    without unrestricted `Bash` holds a fence that `Skill` breaks (spec 1365)."""
    granted = _split_tools(tools)
    if "Bash" in granted:
        return []
    out = []
    for tool in granted:
        if tool == "Skill" or tool.startswith("Skill("):
            remedy = "drop it: read the command body and follow it with the agent's own `tools:`"
            out.append(finding("sk-agent-skill-widens", "error",
                               f"`tools: {tool}` lets any skill's `allowed-tools` widen the "
                               "agent's fenced grant — " + remedy, tool=tool, remedy=remedy,
                               **where))
    return out


def lint_agents(root: str, base: str) -> list[dict]:
    out: list[dict] = []
    for filename, fm in agent_definitions(root):
        path = os.path.join(root, "agents", filename)
        body = body_after_frontmatter(read_text(path) or "")
        where = {"command": f"agent:{filename[:-3]}", "path": rel(path, base)}
        out.extend(_cq_call_findings(body, where))
        disallowed = fm.get("disallowedTools", "")
        if isinstance(disallowed, list):
            disallowed = ", ".join(str(item) for item in disallowed)
        tools = fm.get("tools", "")
        if isinstance(tools, list):
            tools = ", ".join(str(item) for item in tools)
        out.extend(_agent_disallowed_bash_findings(str(disallowed), where, str(tools), body))
        if "tools" in fm:
            out.extend(_agent_skill_findings(str(tools), where))
            out.extend(_agent_grant_findings(body, str(tools), where))
    return out


def _frontmatter_strict_issues(text: str) -> list[dict]:
    """Find the three frontmatter shapes whose YAML meaning is unambiguous here.

    This is intentionally a small structural reader, not a second YAML parser. It reports a
    folded scalar swallowing a known top-level key, an unquoted flow sequence nested inside
    itself, and an unquoted plain scalar containing ``: ``. Other YAML that this plugin does not
    model remains outside the lint's claim rather than being guessed at.
    """
    if not text.startswith("---"):
        return []
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return []

    issues: list[dict] = []
    i = 1
    while i < end:
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#") or raw[:1] in (" ", "\t"):
            i += 1
            continue
        match = FRONTMATTER_KEY_RE.match(raw)
        if not match:
            i += 1
            continue
        key, value = match.group(1), match.group(2).strip()
        if value in {">", ">-", ">+", "|", "|-", "|+"}:
            j = i + 1
            while j < end and (not lines[j].strip() or lines[j][:1] in (" ", "\t")):
                swallowed = FRONTMATTER_KEY_RE.match(lines[j].strip())
                if swallowed and swallowed.group(1) in KNOWN_FRONTMATTER_KEYS:
                    issues.append({
                        "kind": "swallowed-key",
                        "key": swallowed.group(1),
                        "parent": key,
                        "line": j + 1,
                        "remedy": (f"align `{swallowed.group(1)}:` with the top-level keys; "
                                   "do not indent it under the folded scalar"),
                    })
                j += 1
            i = j
            continue

        if value and value[0] not in ("'", '"'):
            if value.startswith("["):
                depth = 0
                nested = False
                for char in value:
                    if char == "[":
                        depth += 1
                        nested = nested or depth > 1
                    elif char == "]":
                        depth = max(0, depth - 1)
                if nested:
                    issues.append({
                        "kind": "nested-flow-sequence",
                        "key": key,
                        "line": i + 1,
                        "remedy": (f"quote the complete `{key}` value or use a flat sequence; "
                                   "do not nest `[` inside an unquoted hint"),
                    })
            elif ": " in value:
                issues.append({
                    "kind": "plain-colon-space",
                    "key": key,
                    "line": i + 1,
                    "remedy": (f"quote the `{key}` value or write it as a `>-` block scalar "
                               "before using `: ` inside the text"),
                })
        i += 1
    return issues


FENCE_RE = re.compile(r"^\s*(```|~~~)")


def unscoped_marker(tool: str) -> str:
    """The one literal line a body writes to price an unscoped grant, for `tool`.

    Parameterised by the name rather than written out, so the marker cannot drift from
    `UNSCOPED_TOOLS`: adding a second unscoped tool gets its marker for free."""
    return f"**Why `{tool}` is unrestricted here.**"


def marker_present(body: str, tool: str) -> bool:
    """Does the body carry `tool`'s marker as a line of its own, outside a fence?

    A BOOLEAN, and deliberately nothing more. It does not read the reason, judge whether it
    is good, or count how long it is — the finding stays reported either way, and what the
    marker buys is a reader who can tell a deliberate grant from an unexamined one.

    The fence state is why this is a walk rather than a `in body` test: a body that QUOTES
    the marker inside a fenced example — this repo's own moulds do exactly that — would
    otherwise price itself by showing what pricing looks like."""
    marker, fenced = unscoped_marker(tool), False
    for line in body.splitlines():
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        if not fenced and line.strip().startswith(marker):
            return True
    return False


SKILL_TOOL_RE = re.compile(r"[*`_]*Skill[*`_]*\s+tool", re.I)
SKILL_TOOL_WINDOW = 1           # lines either side of the name, so a wrapped sentence counts

# The shape of a command's registry id, `quenching-specs-develop` or a one-segment
# `quenching-align` — the same generic-form-then-membership pattern CITATION_RE above uses, and
# for the same reason: one regex over each body, never one regex per target name repeated for
# every caller.
NAME_SHAPE = r"([a-z0-9]+(?:-[a-z0-9]+)*)"


def named_by_bodies(commands: list[dict], prefix: str) -> dict[str, set[str]]:
    """Which commands another command's BODY reaches BY NAME — derived from disk, never
    from a hand-kept list, so a mass rename moves the paths and the check still holds.

    Frontmatter is not read. A `Not for: X -> /other` boundary names a neighbour it is
    steering AWAY from, and counting it would put most of the surface in this set.

    Two conventions are in use, and only the first is unambiguous on its own:

      A. the BARE registry form, `prefix-docs-align` — what you actually hand the Skill
         tool. The same name written `/prefix-docs-align` is a human-facing citation, so
         a leading slash disqualifies it;
      B. any form of the name on a line whose neighbourhood says "Skill tool" — which is
         how a body that writes "chain into `/quenching-specs conclude` (the `Skill` tool)"
         says the same thing.

    The union is deliberately the WIDER read. The two errors are not symmetric: a false
    positive costs a command its place in the typed-only class, which is an argument; a
    false negative lets a real conductor stage be flagged typed-only and go silently
    inert, which is the failure this check exists to prevent.

    Each body is read ONCE, for names of the generic shape, and every name found is decided
    by membership in the surface's own set — never one regex per target name re-run for
    every caller, which cost the square of the surface's size."""
    by_name = {c["command"].lstrip("/"): c["command"] for c in commands}
    esc = re.escape(prefix)
    registry_re = re.compile(rf"(?<![\w:/-])({esc}-{NAME_SHAPE})(?![\w:-])")
    adjacent_re = re.compile(rf"(?<![\w:-])/?({esc}-{NAME_SHAPE})(?![\w:-])")

    out: dict[str, set[str]] = {}
    for caller in commands:
        text = caller["body"] + "\n" + caller.get("refBody", "")
        lines = text.splitlines()
        skill_lines = [i for i, _ in enumerate(lines)
                       if SKILL_TOOL_RE.search("\n".join(
                           lines[max(0, i - SKILL_TOOL_WINDOW): i + SKILL_TOOL_WINDOW + 1]))]
        found = {m.group(1) for m in registry_re.finditer(text)}
        for i in skill_lines:
            for m in adjacent_re.finditer(lines[i]):
                found.add(m.group(1))
        found.discard(caller["command"].lstrip("/"))
        for cand in found:
            if cand in by_name:
                out.setdefault(by_name[cand], set()).add(caller["command"])
    return out


def description_is_resident(fm: dict) -> bool:
    """Is this command's `description` in every session's context?

    THE one place that answers it. `lint` scopes its two routing codes by it, so
    the routing codes and residency can never disagree about the same surface — two
    copies of this condition is the shape that diverges in silence.

    `disable-model-invocation: true` is the only field that makes the answer no.
    Measured, not assumed: Claude Code drops the description from the listing AND
    refuses the command by name through the Skill tool — row 7 of
    `/docs/external/tools/claude-code-skill-command-mechanics.md`, Claude Code 2.1.220.
    The description is still read by a human, in the file and in the `/` menu; it is
    residency it loses, never content."""
    return str(fm.get("disable-model-invocation", "")).strip().lower() != "true"


def lint_command(cmd: dict, base: str, named_by: set[str] | None = None,
                 known_refs: set[str] | None = None) -> list[dict]:
    """`named_by` is the set of commands whose bodies reach THIS one by name. It is a
    property of the whole surface, so only a caller holding one can supply it — and a
    surface with no plugin manifest has no registry form to be reached by, which is why
    `None` (the check does not apply) is a legitimate state rather than a skipped one."""
    fm, body = cmd["frontmatter"], cmd["body"]
    where = {"command": cmd["command"], "path": rel(cmd["path"], base)}
    if not fm:
        return [finding("sk-no-frontmatter", "error",
                        "the command file has no YAML frontmatter — Claude Code cannot load it",
                        **where)]
    out: list[dict] = []

    source = cmd.get("source")
    if source is None:
        source = read_text(cmd["path"])
    for issue in _frontmatter_strict_issues(source or ""):
        out.append(finding("sk-frontmatter-strict", "warn",
                           f"`{issue['key']}` at line {issue['line']}: {issue['kind']} — "
                           f"{issue['remedy']}",
                           key=issue["key"], line=issue["line"], kind=issue["kind"],
                           remedy=issue["remedy"], **where))

    tools = _split_tools(str(fm.get("allowed-tools", "")))
    if _body_has_confirmation_gate(body) and "AskUserQuestion" not in tools:
        out.append(finding("sk-prose-gate", "error",
                           "the body pauses for a user confirmation but `AskUserQuestion` is "
                           "not granted — add the narrow tool grant",
                           tool="AskUserQuestion", remedy="add `AskUserQuestion` to allowed-tools",
                           **where))

    unrestricted_bash, bash_prefixes = _bash_grants(fm)
    if not unrestricted_bash:
        for line, command in _shell_command_lines(body):
            if _grant_covers(command, bash_prefixes):
                continue
            tool = " ".join(command.split()[:2]) if command.startswith("git ") else command.split()[0]
            remedy = f"add `Bash({tool}:*)` to allowed-tools or remove the call"
            out.append(finding("sk-grant-gap", "error",
                               f"`{tool}` at body line {line} is called without a matching "
                               "scoped Bash grant — " + remedy,
                               tool=tool, invocation=command, line=line, remedy=remedy, **where))

    # BEFORE any content check, so a parse failure is never presented as a content
    # gap. A `description` truncated at a `#` used to surface as `sk-no-description`,
    # which names a missing part rather than the truncation that removed it.
    for a in cmd.get("anomalies", []):
        out.append(finding("sk-frontmatter-unparsed", "warn",
                           f"`{a['key']}`: {a['detail']}", kind=a["kind"], key=a["key"], **where))

    # `name` is not checked: the command's path IS its name, so there is no second
    # spelling that could disagree with it. `sk-no-name` and `sk-name-mismatch` are
    # deleted with the pair rather than reinterpreted.
    description = str(fm.get("description", "")).strip()
    if not description:
        out.append(finding("sk-no-description", "error",
                           "no `description` — the command can never be selected", **where))
        return out

    # one file, one description — `when_to_use` went with the skill half
    always_on = len(description)
    if always_on > CAP_METADATA:
        out.append(finding("sk-metadata-cap", "error",
                           f"description is {always_on} characters, over the "
                           f"{CAP_METADATA} cap — Claude Code truncates the tail, which is where "
                           "the `Not for:` boundary lives",
                           characters=always_on, cap=CAP_METADATA, **where))
    if len(description) > CAP_DESCRIPTION_PORTABLE:
        out.append(finding("sk-description-portable", "warn",
                           f"description is {len(description)} characters, over the "
                           f"{CAP_DESCRIPTION_PORTABLE} limit of the Agent Skills standard — the "
                           "command is not portable outside Claude Code",
                           characters=len(description), cap=CAP_DESCRIPTION_PORTABLE, **where))

    # A router's description is a map of intent per flow, so the 250 budget contradicts it:
    # the budget binds only a skill with no table of flows (`sk-router-description` binds the rest).
    if len(description) > CAP_DESCRIPTION_BUDGET and not FLOW_ROW_RE.search(body):
        out.append(finding("sk-description-budget", "warn",
                           f"description is {len(description)} characters, over the "
                           f"{CAP_DESCRIPTION_BUDGET} budget — put the key use case first and "
                           "trim the rest",
                           characters=len(description), cap=CAP_DESCRIPTION_BUDGET, **where))

    # Both codes below judge ROUTING FROM PROSE, so both are scoped to a description
    # that is actually in context. For a typed-only command there is no listing for a
    # trigger phrase to sit in and no neighbour for a boundary to discriminate against
    # — the human reaches it by typing the name. Reporting it as badly written for a
    # routing that cannot happen is `lint` penalizing a description nothing routes from.
    if description_is_resident(fm):
        triggers = _quoted_phrases(description)
        if not triggers:
            out.append(finding("sk-trigger-position", "warn",
                               "the description quotes no trigger phrase — no user wording routes "
                               "to this command", **where))
        elif not _quoted_phrases(" ".join(_split_sentences(description)[:TRIGGER_SENTENCE_MAX])):
            out.append(finding("sk-trigger-position", "warn",
                               f"the first trigger phrase appears after sentence "
                               f"{TRIGGER_SENTENCE_MAX} — a truncated description loses it",
                               **where))
        if BOUNDARY_MARKER not in description:
            out.append(finding("sk-no-boundary", "warn",
                               f"the description states no `{BOUNDARY_MARKER}` boundary — the "
                               "routing story is missing from the only text always in context",
                               **where))

    if cmd["bodyLines"] > CAP_BODY_LINES:
        out.append(finding("sk-body-length", "error",
                           f"body is {cmd['bodyLines']} lines, over the {CAP_BODY_LINES} cap — "
                           "push shared procedure into a bundled reference and cite it by "
                           "absolute path",
                           lines=cmd["bodyLines"], cap=CAP_BODY_LINES, **where))

    body_bytes = len(body.encode("utf-8"))
    if body_bytes > BODY_BYTES_BUDGET and not BODY_SIZE_OPT_OUT_RE.search(body):
        out.append(finding("sk-body-size", "warn",
                           f"body is {body_bytes} bytes, over the {BODY_BYTES_BUDGET}-byte budget "
                           "— cut dead weight, move shared procedure into a reference, or declare "
                           "the exception on its own line: "
                           "`<!-- body-size: justified — <reason> -->`",
                           bytes=body_bytes, budget=BODY_BYTES_BUDGET, **where))

    steps, covered = _step_criteria(body)
    if steps and covered < steps:
        out.append(finding("sk-step-criterion", "warn",
                           f"{steps - covered} of {steps} workflow steps carry no "
                           f"`{DONE_WHEN_MARKER}` criterion — a step with no observable end state "
                           "can be claimed done early", steps=steps, covered=covered, **where))

    # BOTH cases are reported, and that is the point: the grant is still the whole shell for
    # the turn either way. What the marker changes is the REMEDY — the unpriced message asks
    # for something the reader can do, instead of pointing at a body this check never read.
    for bare in (t for t in _split_tools(str(fm.get("allowed-tools", ""))) if t in UNSCOPED_TOOLS):
        priced = marker_present(body, bare)
        message = (f"`{bare}` is granted unscoped and the body prices it — the grant is still "
                   "the whole shell for the turn" if priced else
                   f"`{bare}` is granted unscoped — scope it to the commands the workflow runs, "
                   f"or open a line with `{unscoped_marker(bare)}` and state the reason there")
        out.append(finding("sk-unscoped-bash", "warn", message,
                           tool=bare, priced=priced, **where))

    out.extend(reference_root_findings(body, known_refs, where))
    out.extend(_lint_invocation(fm, where, named_by))
    out.extend(_lint_frontmatter_hooks(cmd, where))
    out.extend(_lint_profile(fm, where))
    return out


def _lint_profile(fm: dict, where: dict) -> list[dict]:
    """The execution profile's mechanically decidable slice. `context: fork` runs the
    command in a forked context that CANNOT present a mid-flow question, so a fork beside
    an `AskUserQuestion` grant is incoherent by construction — the one profile combination
    that is an error rather than a judgment. Unparseable `context`/`effort` values are
    warned: Claude Code reads them as unset, so the command silently runs without the
    profile its author thought it had."""
    out: list[dict] = []
    context = str(fm.get("context", "")).strip().lower()
    if context and context != "fork":
        out.append(finding("sk-profile-value", "warn",
                           f"`context: {fm['context']}` is not a value Claude Code reads — "
                           "the only supported value is `fork`", **where))
    effort = str(fm.get("effort", "")).strip().lower()
    if effort and effort not in EFFORT_VALUES:
        out.append(finding("sk-profile-value", "warn",
                           f"`effort: {fm['effort']}` is not one of "
                           f"{'/'.join(EFFORT_VALUES)} — Claude Code reads it as unset",
                           **where))
    if context == "fork":
        tools = _split_tools(str(fm.get("allowed-tools", "")))
        if any(t == "AskUserQuestion" or t.startswith("AskUserQuestion(") for t in tools):
            out.append(finding("sk-fork-gate", "error",
                               "`context: fork` beside an `AskUserQuestion` grant — a forked "
                               "context cannot present a mid-flow question, so one of the two "
                               "is a lie", **where))
    return out


def _lint_invocation(fm: dict, where: dict, named_by: set[str] | None = None) -> list[dict]:
    """Both keys are optional and default invocation is the norm — a collapsed command
    is typable at `/` AND reachable by name, which is what lets a conductor invoke a
    stage. The incoherence worth an error is the combination that leaves NO caller:
    the menu off and the model blocked.

    The second incoherence is narrower and was invisible until it was measured: a
    command another body reaches BY NAME cannot also be typed-only, because the Skill
    tool refuses it (row 7 of the mechanics reference). Nothing else on this surface
    catches it — `doctor` still counts it as present, and the conductor does not
    fail, it simply does nothing."""
    out, values = [], {}
    for key in ("user-invocable", "disable-model-invocation"):
        if key not in fm:
            continue
        raw = str(fm[key]).strip().lower()
        if raw in ("true", "false"):
            values[key] = raw == "true"
        else:
            out.append(finding("sk-invocation-value", "warn",
                               f"`{key}: {fm[key]}` is not a boolean — Claude Code reads an "
                               "unparsable value as unset", **where))
    if values.get("user-invocable") is False and values.get("disable-model-invocation") is True:
        out.append(finding("sk-unreachable", "error",
                           "`user-invocable: false` with `disable-model-invocation: true` leaves "
                           "no way to invoke the command — neither the menu nor the model",
                           **where))
    if values.get("disable-model-invocation") is True and named_by:
        callers = ", ".join(sorted(named_by))
        out.append(finding("sk-inert-stage", "error",
                           f"`disable-model-invocation: true` on a command reached by name from "
                           f"{callers} — the Skill tool refuses the call, so that body runs and "
                           "this stage silently does nothing",
                           namedBy=sorted(named_by), **where))

    return out


def _lint_frontmatter_hooks(cmd: dict, where: dict) -> list[dict]:
    """The scope ladder's narrowest rung. A frontmatter hook fires only while this command
    runs, which is why it is the default rung — but "narrow" is about SCOPE, not about cost:
    an unmatched matcher here still taxes every tool call the command makes, so the same two
    ladder codes apply, from the same implementation `settings.json` uses."""
    out: list[dict] = []
    if not cmd.get("hooksParsed", True):
        out.append(finding("sk-hook-unparseable", "warn",
                           "the frontmatter `hooks:` block does not match the mold's shape, so "
                           "the ladder checks could not read it — it is NOT reported as clean",
                           **where,
                           remedy="rewrite it in the shape of "
                                  "assets/templates/automation/hook.md shape 1 (/quenching-components hook-new)"))
    out.extend(hook_ladder_findings(cmd.get("hooks") or {},
                                    where_in=f"{where['path']} frontmatter", where=where))
    return out


COMMON_REF_ROOT = ".agents/quenching/references/"
SKILL_REF_RE = re.compile(r"\.agents/skills/[a-z0-9-]+/references/")
REF_LINK_RE = re.compile(r"\]\(([^)\s]+\.md)(?:#[^)]*)?\)|components read\s+[\"']?([^\s`\"'<>-][^\s`\"']*\.md)")


URL_SCHEME_RE = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")


def reference_root_findings(body: str, known: set[str] | None, where: dict) -> list[dict]:
    """`sk-reference-root` (error): a link or `cq components read` target that names a reference
    without its installed root, or whose rooted path does not exist.

    A reference is cited by the path it has once installed: `.agents/skills/<name>/references/<path>`
    beside its skill, or `.agents/quenching/references/<path>` in the common set. `known` is the
    set of those paths on this surface (`None`: no tree to check against). The retired
    `assets` root of the references and a bare `specs-develop/questions.md` both make
    `cq components read` exit 2."""
    out: list[dict] = []
    seen: set[str] = set()
    for m in REF_LINK_RE.finditer(body):
        target = m.group(1) or m.group(2)
        if target in seen or URL_SCHEME_RE.match(target):
            continue
        if target.startswith(COMMON_REF_ROOT) or SKILL_REF_RE.match(target):
            if known is not None and target not in known:
                problem = "does not exist on this surface"
            else:
                continue
        elif "assets/" + "references/" in target:
            problem = "is not anchored at an installed references root"
        elif known is not None and "/" in target and not target.startswith(("/", ".", "$", "http")) \
                and COMMON_REF_ROOT + target in known:
            problem = "names a reference without its installed root"
        else:
            continue
        seen.add(target)
        out.append(finding("sk-reference-root", "error",
                           f"`{target}` {problem} — cite it as `{COMMON_REF_ROOT}<path>` or "
                           "`.agents/skills/<name>/references/<path>` so it resolves once installed",
                           target=target, **where))
    return out


# --------------------------------------------------------------------------- #
# routers — a skill whose body is a table of flows, each flow a reference of its own
# --------------------------------------------------------------------------- #
FLOW_ROW_RE = re.compile(r"^\|\s*`([a-z0-9-]+)`\s*\|\s*\[[^\]]*\]\(([^)\s]+)\)", re.MULTILINE)
FLOW_CITATION_RE = re.compile(r"`/(quenching-[a-z0-9]+(?:-[a-z0-9]+)*) ([a-z][a-z0-9-]*)(?=[`\s])")


def router_flows(cmd: dict) -> dict[str, str]:
    """`{flow: reference file}` read from a router skill's table of flows; `{}` for any other skill.
    The table is the router's contract, so the flows are never listed a second time elsewhere."""
    flows: dict[str, str] = {}
    folder = os.path.dirname(cmd["path"])
    for m in FLOW_ROW_RE.finditer(cmd["body"]):
        link = m.group(2)
        tail = link.split("/references/", 1)
        flows[m.group(1)] = os.path.join(folder, "references", tail[1]) if len(tail) == 2 \
            else os.path.join(folder, link)
    return flows


def router_grant_findings(cmd: dict, flows: dict[str, str], where: dict) -> list[dict]:
    """`sk-router-grant` (error): the router grants the UNION of its flows' `allowed-tools`, each
    declared in the flow's own reference. A tool a flow needs and the router lacks fails the flow
    the moment the router loads it; a bare `Bash` covers every scoped `Bash(...)`."""
    granted = set(_split_tools(str(cmd["frontmatter"].get("allowed-tools", ""))))
    bash_open, prefixes = _bash_grants(cmd["frontmatter"])
    missing: dict[str, list[str]] = {}
    for flow, path in flows.items():
        text = read_text(path)
        if text is None:
            continue
        for tool in _split_tools(str(parse_frontmatter(text).get("allowed-tools", ""))):
            if tool in granted or (bash_open and tool.startswith("Bash(")):
                continue
            if tool.startswith("Bash(") and tool.endswith(")"):
                spec = tool[5:-1]
                if _grant_covers(spec[:-2] if spec.endswith(":*") else spec, prefixes):
                    continue
            missing.setdefault(tool, []).append(flow)
    if not missing:
        return []
    shown = ", ".join(f"`{t}` ({'/'.join(f)})" for t, f in sorted(missing.items()))
    return [finding("sk-router-grant", "error",
                    f"the router does not grant what its flows declare — {shown}. The grant is the "
                    "union of the flows' `allowed-tools`",
                    missing=sorted(missing), remedy="add the missing tools to the router's allowed-tools",
                    **where)]


def router_description_findings(cmd: dict, flows: dict[str, str], where: dict) -> list[dict]:
    """`sk-router-description` (error): a flow of the router's table that the description does not
    name. The description is the only text always in context, so a flow it omits is a flow the
    user's wording cannot reach."""
    description = str(cmd["frontmatter"].get("description", ""))
    missing = [f for f in flows
               if not re.search(r"(?<![a-z0-9-])" + re.escape(f) + r"(?![a-z0-9-])", description)]
    if not missing:
        return []
    shown = ", ".join(f"`{f}`" for f in missing)
    return [finding("sk-router-description", "error",
                    f"the description does not name {shown} from the table of flows — the router "
                    "can only route the flows its description maps",
                    missing=missing, remedy="name every flow of the table in the description",
                    **where)]


def dangling_flow_citations(body: str, routers: dict[str, set[str]]) -> list[str]:
    """The `` `/quenching-<vertical> <flow>` `` citations whose vertical is a router of this
    surface but whose flow is not in its table."""
    seen: list[str] = []
    for m in FLOW_CITATION_RE.finditer(body):
        flows = routers.get(m.group(1))
        cite = f"/{m.group(1)} {m.group(2)}"
        if flows is not None and m.group(2) not in flows and cite not in seen:
            seen.append(cite)
    return seen


def bare_citations(body: str, invocations: set[str], prefix: str | None = None) -> list[str]:
    """The retired `:` citations in `body` that name a command of THIS surface, in order of
    appearance and without repeats: the bare `/front:verb`, and with a `prefix` the module-led
    `/<prefix>-<module>:front:verb` too.

    `body` is the text after the frontmatter, so a citation inside `description:` cannot
    reach here — the boundary is the parse, never a pattern that has to be kept in step
    with one."""
    seen: list[str] = []
    for m in CITATION_RE.finditer(body):
        name = m.group(1)
        if prefix and name.startswith(prefix + "-") and ":" in name:
            name = name.split(":", 1)[1]
        cite = "/" + name
        if cite in invocations and "/" + m.group(1) not in seen:
            seen.append("/" + m.group(1))
    return seen


def lint_citations(prefix: str, body: str, invocations: set[str], where: dict) -> list[dict]:
    """One finding per file, never one per citation: a surface mid-sweep carries hundreds,
    and a report nobody can read is a report nobody acts on."""
    bare = bare_citations(body, invocations, prefix)
    if not bare:
        return []
    shown = ", ".join(f"`{c}`" for c in bare[:CITATION_SAMPLE])
    more = f" (+{len(bare) - CITATION_SAMPLE} more)" if len(bare) > CITATION_SAMPLE else ""
    return [finding("sk-bare-citation", "warn",
                    f"{plural(len(bare), 'retired command citation')} in the body — {shown}{more}. "
                    "The source is written in the target dialect: the id that resolves is "
                    f"`{prefix}-<front>-<verb>` for the Skill tool and "
                    f"`/{prefix}-<front>-<verb>` for a human; the `:` spelling is retired",
                    citations=bare, **where)]


def dangling_citations(body: str, invocations: set[str]) -> list[str]:
    """The `/front:verb` citations in `body` whose front exists on THIS surface but whose
    command does not — a command removed or renamed while another body still cites it.

    A citation whose first segment is no front of the surface (a plugin's command, another
    namespace) is not this surface's to judge. A citation that is a leading part of an
    existing command (`/front:sub` beside `/front:sub:verb`) names a namespace, not a miss."""
    fronts = {c[1:].split(":", 1)[0] for c in invocations}
    seen: list[str] = []
    for m in CITATION_RE.finditer(body):
        cite = "/" + m.group(1)
        if cite in seen or cite in invocations or m.group(1).split(":", 1)[0] not in fronts:
            continue
        if any(i.startswith(cite + ":") for i in invocations):
            continue
        seen.append(cite)
    return seen


def dangling_hyphen_citations(body: str, invocations: set[str], prefix: str,
                              fronts: set[str]) -> list[str]:
    """The `/<prefix>-<front>-<verb>` citations in `body` whose front exists on THIS surface but
    whose command does not. A citation that leads an existing command names a namespace."""
    seen: list[str] = []
    for m in HYPHEN_CITATION_RE.finditer(body):
        cite = "/" + m.group(1)
        head = prefix + "-"
        if cite in seen or cite in invocations or not m.group(1).startswith(head):
            continue
        if not any(m.group(1)[len(head):].startswith(front + "-") for front in fronts):
            continue
        if any(i.startswith(cite + "-") for i in invocations):
            continue
        seen.append(cite)
    return seen


def lint_dangling(body: str, invocations: set[str], where: dict, prefix: str | None = None,
                  fronts: set[str] | None = None,
                  routers: dict[str, set[str]] | None = None) -> list[dict]:
    """One finding per file, like `lint_citations`: the citations that resolve to nothing.

    `fronts` are the routers' short names: `/quenching-<router>-<x>` is a retired shortcut, and a
    router cited with a flow its table does not list names a flow that is gone."""
    dangling = (dangling_hyphen_citations(body, invocations, prefix, fronts or set()) if prefix
                else dangling_citations(body, invocations))
    if prefix and routers:
        dangling += dangling_flow_citations(body, routers)
    if not dangling:
        return []
    shown = ", ".join(f"`{c}`" for c in dangling[:CITATION_SAMPLE])
    more = (f" (+{len(dangling) - CITATION_SAMPLE} more)"
            if len(dangling) > CITATION_SAMPLE else "")
    return [finding("sk-dangling-citation", "warn",
                    f"{plural(len(dangling), 'citation')} of a command this surface no longer "
                    f"has — {shown}{more}. The front exists but the command does not: it was "
                    "removed or renamed while this file still cites it",
                    citations=dangling,
                    remedy="cite the command that replaced it, or drop the citation",
                    **where)]


def nearest_commands_dir(file_path: str) -> str | None:
    """The closest ancestor directory named `commands`, or None: the tree whose relative paths
    ARE the file's invocation, so a nested file keeps its full name instead of the bare file name."""
    probe = os.path.dirname(os.path.abspath(file_path))
    while True:
        if os.path.basename(probe) == COMMANDS_DIR:
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent


def resolve_lint_targets(path_arg: str | None, root: str) -> tuple[str, list[dict], list[dict]]:
    """(base, commands, references). Accepts a single command file, a commands/ directory,
    or a surface root — so `lint commands`, `lint .claude`, and `lint commands/docs/add.md`
    all mean what they read like.

    The references ride along only when the base IS a surface root: linting one file, or a
    bare `commands/` directory, is a scope the caller named and this never widens it."""
    if not path_arg:
        return (root, discover_entry_points(root), surface_references(root))
    target = os.path.abspath(path_arg)
    if os.path.isfile(target) and os.path.basename(target) == SKILL_FILE:
        base = os.path.dirname(os.path.dirname(target))
        return base, [c for c in discover_skills(base)
                      if os.path.abspath(c["path"]) == target], []
    if os.path.isfile(target):
        base = nearest_commands_dir(target) or os.path.dirname(target)
        return base, [c for c in discover_commands(base)
                      if os.path.abspath(c["path"]) == target], []
    if os.path.isdir(entry_points_dir(target)):
        return (target, discover_entry_points(target), surface_references(target))
    return target, discover_commands(target), []


def surface_root_of(base: str, root: str) -> str:
    """The surface root that judges `base`: `base` itself when it holds `commands/`, else the
    nearest ancestor whose `commands/` contains it, else the cwd surface `root`."""
    probe = os.path.abspath(base)
    if os.path.basename(probe) == COMMANDS_DIR:
        return os.path.dirname(probe)
    while True:
        commands = os.path.join(probe, COMMANDS_DIR)
        if os.path.isdir(commands) and (probe == os.path.abspath(base)
                                        or os.path.abspath(base).startswith(commands + os.sep)):
            return probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return root
        probe = parent


def cmd_lint(args, root: str) -> int:
    base, commands, references = resolve_lint_targets(args.path, root)
    if not commands and not os.path.isfile(os.path.abspath(args.path or base)) \
            and agent_definitions(base):
        # A surface of agents alone is still a surface: its agents are checked, not refused.
        return report_findings(args.json, f"components lint — {base} (0 commands)",
                               {"root": base, "commandCount": 0},
                               lint_agents(base, base), "command")
    if not commands:
        return report_findings(args.json, f"components lint — {base}",
                               {"root": base, "commandCount": 0},
                               [finding("sk-no-commands", "error",
                                        f"no command file found under {base}",
                                        command=SURFACE_MISSING)], "command")
    surface = surface_root_of(base, root)
    prefix = plugin_prefix(surface)
    # Same surface-versus-scope rule as the citations below, and for the same reason: the
    # body that names a stage is usually NOT the file being linted, so deriving this from
    # `commands` would make `lint <one file>` blind to the conductor that reaches it.
    surface_commands = (commands if base == surface
                        else discover_entry_points(surface))
    named_by = named_by_bodies(surface_commands, prefix) if prefix else {}
    surface_flows = {c["command"].lstrip("/"): router_flows(c) for c in surface_commands}
    routers = {name: set(flows) for name, flows in surface_flows.items() if flows}
    known_refs = ({r["relpath"] for r in surface_references(surface)}
                  if os.path.isdir(os.path.join(surface, "skills")) else None)

    findings: list[dict] = []
    for cmd in commands:
        findings.extend(lint_command(cmd, base,
                                     named_by.get(cmd["command"]) if prefix else None,
                                     known_refs))
        where = {"command": cmd["command"], "path": rel(cmd["path"], base)}
        findings.extend(_cq_call_findings(cmd["body"], where))
        flows = surface_flows.get(cmd["command"].lstrip("/")) or {}
        if flows:
            findings.extend(router_grant_findings(cmd, flows, where))
            findings.extend(router_description_findings(cmd, flows, where))
    if base == surface:
        findings.extend(lint_agents(base, base))

    invocations = {c["command"] for c in surface_commands}
    if prefix:
        fronts = {name[len(prefix) + 1:] for name in routers if name.startswith(prefix + "-")}
        spellings = {"/" + ":".join(c["relpath"][:-3].split("/")) for c in surface_commands
                     if "relpath" in c and not c["relpath"].endswith(SKILL_FILE)}
        for cmd in commands:
            where = {"command": cmd["command"], "path": rel(cmd["path"], base)}
            findings.extend(lint_citations(prefix, cmd["body"], spellings, where))
            findings.extend(lint_dangling(cmd["body"], invocations, where, prefix, fronts, routers))
        for ref in references:
            where = {"command": SURFACE_MISSING, "path": rel(ref["path"], base)}
            findings.extend(lint_citations(prefix, ref["body"], spellings, where))
            findings.extend(lint_dangling(ref["body"], invocations, where, prefix, fronts, routers))

    else:
        for cmd in commands:
            findings.extend(lint_dangling(cmd["body"], invocations,
                                          {"command": cmd["command"],
                                           "path": rel(cmd["path"], base)}))
        for ref in references:
            findings.extend(lint_dangling(ref["body"], invocations,
                                          {"command": SURFACE_MISSING,
                                           "path": rel(ref["path"], base)}))

    header = f"components lint — {base} ({plural(len(commands), 'command')}"
    header += f", {plural(len(references), 'reference')})" if references else ")"
    return report_findings(args.json, header,
                           {"root": base, "commandCount": len(commands),
                            "referenceCount": len(references)}, findings, "command")
