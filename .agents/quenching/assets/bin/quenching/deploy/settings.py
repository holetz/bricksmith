"""The merge of quenching's own hooks and `attribution` into a target's `.claude/settings.json`.

Pure: it takes the current text (or None) and the wanted entries and returns the new text, or
refuses. The file belongs to the user; only the entries whose command points under
`.agents/quenching/assets/hooks/` are quenching's, and that path is the marker that identifies them
even when no deploy manifest exists.

The GitHub Copilot harness reads this same file, and on Windows it runs the command through
PowerShell or cmd, not bash. So the command is one polyglot line — a Python prefix and `-I -c "…"`
whose code holds no `$`, no backtick and no double quote — that every one of those shells hands to
Python unchanged.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field

MARKER = ".agents/quenching/assets/hooks/"
_NAME = re.compile(re.escape(MARKER) + r"([A-Za-z0-9_-]+)\.(?:sh|py)\b")
PYTHON = ("python3",)  # the prefix when the repository declares no `shared.mcp.command`
ATTRIBUTION = {"commit": "", "pr": ""}


class SettingsRefusal(ValueError):
    """The existing file cannot be merged safely; nothing is written."""


@dataclass
class Merged:
    text: str | None
    attribution: list[str] = field(default_factory=list)   # keys the deploy owns, still ""
    warnings: list[str] = field(default_factory=list)


def _word(part: str) -> str:
    return f'"{part}"' if any(c.isspace() for c in part) else part


def hook_command(name: str, prefix: list[str] | tuple[str, ...] = PYTHON) -> str:
    """The one line bash, PowerShell and cmd all read the same way: the root from the
    `CLAUDE_PROJECT_DIR` both harnesses export (else the working directory), `{}` when the body is
    not installed, else the body run as `__main__`."""
    code = ("import os,runpy,sys;"
            f"p=os.path.join(os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd(),'{MARKER}{name}.py');"
            "os.path.isfile(p) or (print('{}'),sys.exit(0));"
            "runpy.run_path(p,run_name='__main__')")
    return " ".join(_word(part) for part in prefix) + f' -I -c "{code}"'


def desired_entries(declared: list[dict], prefix: list[str] | tuple[str, ...] = PYTHON) -> list[dict]:
    """The wanted hooks, from `owned.hooks` declarations: name, event, matcher, timeout."""
    return [{"name": h["name"], "event": h["event"], "matcher": h["matcher"],
             "timeout": h["timeout"], "command": hook_command(h["name"], prefix)} for h in declared]


def _own_name(hook) -> str | None:
    if not isinstance(hook, dict) or hook.get("type") != "command":
        return None
    found = _NAME.search(str(hook.get("command", "")))
    return found.group(1) if found else None


def _refuse(why: str):
    raise SettingsRefusal(why)


def _merge_hooks(doc: dict, wanted: list[dict]) -> None:
    hooks = doc.get("hooks")
    if hooks is None:
        hooks = {}
    elif not isinstance(hooks, dict):
        _refuse("`hooks` must be an object")
    pending = {(w["event"], w["matcher"], w["name"]): w for w in wanted}
    emptied_events = []
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            _refuse(f"`hooks.{event}` must be a list")
        kept = []
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list) or not any(_own_name(h) for h in inner):
                kept.append(group)
                continue
            matcher = group.get("matcher", "")
            rest = []
            for hook in inner:
                name = _own_name(hook)
                if name is None:
                    rest.append(hook)
                    continue
                want = pending.pop((event, matcher, name), None)
                if want is not None:
                    hook = {**hook, "type": "command", "command": want["command"],
                            "timeout": want["timeout"]}
                    rest.append(hook)
            if rest:
                kept.append({**group, "hooks": rest})
        if kept:
            hooks[event] = kept
        else:
            emptied_events.append(event)
    for event in emptied_events:
        del hooks[event]
    for (event, matcher, _name), want in pending.items():
        hooks.setdefault(event, []).append(
            {"matcher": matcher, "hooks": [{"type": "command", "command": want["command"],
                                            "timeout": want["timeout"]}]})
    if hooks:
        doc["hooks"] = hooks
    else:
        doc.pop("hooks", None)


def _merge_attribution(doc: dict, wanted: dict | None, recorded: list[str], out: Merged) -> None:
    current = doc.get("attribution")
    if current is None:
        current = {}
    elif not isinstance(current, dict):
        _refuse("`attribution` must be an object")
    if wanted is None:
        for key in recorded:
            if current.get(key) == "":
                del current[key]
        if current:
            doc["attribution"] = current
        else:
            doc.pop("attribution", None)
        return
    for key, value in wanted.items():
        if key not in current:
            current[key] = value
            out.attribution.append(key)
        elif current[key] == value:
            if key in recorded:
                out.attribution.append(key)
        else:
            out.warnings.append(f"attribution.{key} is {current[key]!r} in .claude/settings.json; "
                                "left as the user wrote it")
    doc["attribution"] = current


def merge(text: str | None, wanted: list[dict], attribution: dict | None = None,
          recorded: list[str] | None = None) -> Merged:
    """The settings text after the merge. `text` None means the file does not exist."""
    out = Merged(text=text)
    if text is None:
        original: dict = {}
    else:
        try:
            original = json.loads(text)
        except ValueError as exc:
            raise SettingsRefusal(f"not valid JSON: {exc}") from exc
        if not isinstance(original, dict):
            _refuse("the root must be an object")
    doc = copy.deepcopy(original)
    _merge_hooks(doc, wanted)
    _merge_attribution(doc, attribution, list(recorded or []), out)
    if doc == original:
        return out
    out.text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    return out
