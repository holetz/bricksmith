"""The frontmatter `hooks:` block, and the ladder checks that run over any `hooks` object.

Moved verbatim out of the pre-refactor components script.

Two things live together here because they are the same rung read twice. `parse_frontmatter_hooks`
is the reader the generic parser deliberately does not have — it is the reason
`quenching.common.frontmatter.ANOMALY_EXEMPT_KEYS` names `hooks` at all — and
`hook_ladder_findings` is the one implementation both `lint` (a command's frontmatter block) and
`doctor` (the `settings.json` wiring) grade against, which is also what keeps those two verbs from
importing each other.
"""
from __future__ import annotations

from quenching.common.frontmatter import _frontmatter_body, _indented_run, _unquote
from quenching.common.output import finding

TOOL_EVENTS = ("PreToolUse", "PostToolUse")   # the per-tool-call hook events
LLM_HANDLERS = ("prompt", "agent")            # hook handlers that run an inference per firing


def parse_frontmatter_hooks(text: str) -> tuple[dict, bool]:
    """The frontmatter `hooks:` block — the scope ladder's NARROWEST rung, and the one
    `parse_frontmatter` structurally cannot see: it keeps top-level pairs only, so a
    nested block reads back as `""` and the rung was invisible to `lint` entirely.

    Returns `({event: [{"matcher": str, "hooks": [{...}]}]}, understood)`, shaped to
    match the `hooks` object in `settings.json` so the SAME ladder checks run over both
    without a second implementation.

    This grows ONE case, not a YAML implementation. It reads exactly what
    `assets/templates/automation/hook.md` shape 1 emits:

        hooks:
          PostToolUse:
            - matcher: "Write|Edit"
              hooks:
                - type: command
                  command: "python3 ..."
                  timeout: 10

    Anything else sets `understood` False and the caller WARNS rather than guessing —
    fail-open, because a parser that silently misreads a hook is worse than one that
    admits it cannot read it."""
    body = _frontmatter_body(text)
    if body is None:
        return {}, True
    start = None
    for i, ln in enumerate(body):
        if ln[:1] not in (" ", "\t") and ln.strip() == "hooks:":
            start = i + 1
            break
    if start is None:
        return {}, True

    run, _ = _indented_run(body, start)
    if not run:
        return {}, False

    base = min(len(ln) - len(ln.lstrip()) for ln in run if ln.strip())
    events: dict = {}
    understood = True
    event = entry = None
    handlers_indent = None

    for ln in run:
        if not ln.strip():
            continue
        indent, s = len(ln) - len(ln.lstrip()), ln.strip()
        if indent == base:                                    # an event key
            if s.startswith("-") or not s.endswith(":"):
                understood = False
                continue
            event, entry, handlers_indent = s[:-1].strip(), None, None
            events.setdefault(event, [])
            continue
        if event is None:
            understood = False
            continue
        key, _, val = (s[2:] if s.startswith("- ") else s).partition(":")
        key = key.strip()
        if s.startswith("- "):
            if handlers_indent is not None and indent > handlers_indent:
                entry["hooks"].append({key: _unquote(val)})   # a handler mapping
                continue
            entry, handlers_indent = {"matcher": "", "hooks": []}, None
            events[event].append(entry)
        if entry is None:
            understood = False
            continue
        if key == "matcher":
            entry["matcher"] = _unquote(val)
        elif key == "hooks":
            handlers_indent = indent
        elif key in ("type", "command", "prompt", "timeout", "once"):
            if entry["hooks"]:
                entry["hooks"][-1][key] = _unquote(val)
            else:
                understood = False
        elif key:
            understood = False
    return events, understood


def hook_ladder_findings(hooks: dict, where_in: str, where: dict) -> list[dict]:
    """The scope- and handler-ladder checks over a `hooks` object.

    Written once and run over BOTH rungs — the `settings.json` wiring and a command's
    frontmatter block — because the economics are identical: an unmatched tool-event
    hook taxes every tool call, and an inference handler charges a model call per
    firing, wherever the wiring happens to be declared."""
    out: list[dict] = []
    if not isinstance(hooks, dict):
        return out
    for event in TOOL_EVENTS:
        for entry in hooks.get(event) or []:
            if not isinstance(entry, dict):
                continue
            at = {**where, "event": event}
            if str(entry.get("matcher", "")).strip() in ("", "*"):
                out.append(finding(
                    "sk-hook-unmatched", "warn",
                    f"a {event} hook in {where_in} has no matcher — it fires on "
                    "every tool call, and every iteration in the repo pays it",
                    **at,
                    remedy="add a matcher, or state where it is wired why nothing "
                           "narrower suffices (/quenching-components hook-new owns the scope "
                           "ladder)"))
            for h in entry.get("hooks") or []:
                if isinstance(h, dict) and str(h.get("type", "")).strip() in LLM_HANDLERS:
                    out.append(finding(
                        "sk-hook-llm-frequent", "warn",
                        f"a `{h.get('type')}` handler on {event} in {where_in} "
                        "runs an inference per matched tool call",
                        **at,
                        remedy="decide the deterministic path with a command handler "
                               "and keep the inference for the judgment tail "
                               "(/quenching-components hook-new owns the handler ladder)"))
    return out
