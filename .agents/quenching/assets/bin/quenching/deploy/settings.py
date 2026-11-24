"""The merge of quenching's own hooks and `attribution` into a target's `.claude/settings.json`.

Pure: it takes the current text (or None) and the wanted entries and returns the new text, or
refuses. The file belongs to the user; only the entries whose command points under
`.agents/quenching/assets/hooks/` are quenching's, and that path is the marker that identifies them
even when no deploy manifest exists.

The GitHub Copilot harness reads this same file, and on Windows it runs the command through
PowerShell or cmd, not bash. So the command is one polyglot line — a Python prefix and `-I -c "…"`
whose code holds no `$`, no backtick and no double quote — that every one of those shells hands to
Python unchanged. Every hook declared on one `(event, matcher)` pair shares one entry: the
dispatcher reads stdin once and runs each body, so a shell call pays one interpreter start.
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
DISPATCH = "dispatch"  # the body every entry runs; the declared bodies are its arguments
_SAFE = re.compile(r"[A-Za-z0-9_-]+")


class SettingsRefusal(ValueError):
    """The existing file cannot be merged safely; nothing is written."""


@dataclass
class Merged:
    text: str | None
    attribution: list[str] = field(default_factory=list)   # keys the deploy owns, still ""
    mcpjson: bool = False  # the deploy wrote `quenching` into `enabledMcpjsonServers`
    warnings: list[str] = field(default_factory=list)


def _word(part: str) -> str:
    return f'"{part}"' if any(c.isspace() for c in part) else part


def dispatch_command(names: list[str], prefix: list[str] | tuple[str, ...] = PYTHON) -> str:
    """The one line bash, PowerShell and cmd all read the same way: the root from the
    `CLAUDE_PROJECT_DIR` both harnesses export (else the working directory), `{}` when the
    dispatcher is not installed, else the dispatcher run as `__main__` with the body names as its
    arguments — every body of one matcher in one interpreter."""
    for name in names:
        if not _SAFE.fullmatch(name) or name == DISPATCH:
            raise ValueError(f"hook name {name!r} must match [A-Za-z0-9_-]+ and not be {DISPATCH!r}")
    code = ("import os,runpy,sys;"
            f"p=os.path.join(os.environ.get('CLAUDE_PROJECT_DIR') or os.getcwd(),'{MARKER}{DISPATCH}.py');"
            "os.path.isfile(p) or (print('{}'),sys.exit(0));"
            "runpy.run_path(p,run_name='__main__')")
    return " ".join(_word(part) for part in prefix) + f' -I -c "{code}" ' + " ".join(names)


def desired_entries(declared: list[dict], prefix: list[str] | tuple[str, ...] = PYTHON) -> list[dict]:
    """The wanted entries, from `owned.hooks` declarations: ONE per `(event, matcher)`, running the
    dispatcher over every body declared there, in declaration order, with the widest timeout."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for hook in declared:
        groups.setdefault((hook["event"], hook["matcher"]), []).append(hook)
    return [{"name": DISPATCH, "event": event, "matcher": matcher,
             "hooks": [h["name"] for h in hooks],
             "timeout": max(h["timeout"] for h in hooks),
             "command": dispatch_command([h["name"] for h in hooks], prefix)}
            for (event, matcher), hooks in groups.items()]


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
    # An entry of the old one-per-body layout names a body; the dispatcher entry that now runs it
    # takes its place, so the user's ordering survives the convergence.
    for w in wanted:
        for body in w.get("hooks", ()):
            pending.setdefault((w["event"], w["matcher"], body), w)
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
                    for key in [k for k, v in pending.items() if v is want]:
                        del pending[key]
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
    for (event, matcher, name), want in pending.items():
        if name != want["name"]:
            continue  # an alias of a body; the entry itself is keyed by its own name
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


SERVER = "quenching"  # the `.mcp.json` server the deploy registers, approved by name


def _names(doc: dict, key: str) -> list:
    names = doc.get(key)
    if names is None:
        return []
    if not isinstance(names, list):
        _refuse(f"`{key}` must be a list")
    return names


def _merge_mcpjson(doc: dict, wanted: bool, recorded: bool, out: Merged) -> None:
    """Approve the registered server in `enabledMcpjsonServers`, unless the user disabled it;
    take it out again only when the deploy wrote it."""
    enabled = _names(doc, "enabledMcpjsonServers")
    if wanted:
        if SERVER in _names(doc, "disabledMcpjsonServers"):
            out.warnings.append(f"disabledMcpjsonServers names {SERVER} in .claude/settings.json; "
                                "left as the user wrote it, so the server stays off")
            out.mcpjson = recorded and SERVER in enabled
            return
        if SERVER not in enabled:
            doc["enabledMcpjsonServers"] = [*enabled, SERVER]
            out.mcpjson = True
        else:
            out.mcpjson = recorded
    elif recorded and SERVER in enabled:
        rest = [name for name in enabled if name != SERVER]
        if rest:
            doc["enabledMcpjsonServers"] = rest
        else:
            doc.pop("enabledMcpjsonServers", None)


def merge(text: str | None, wanted: list[dict], attribution: dict | None = None,
          recorded: list[str] | None = None, mcpjson: bool = False,
          mcpjson_recorded: bool = False) -> Merged:
    """The settings text after the merge. `text` None means the file does not exist. `mcpjson`
    asks for the registered server's approval; `mcpjson_recorded` says a past deploy wrote it."""
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
    _merge_mcpjson(doc, mcpjson, mcpjson_recorded, out)
    if doc == original:
        return out
    out.text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    return out
