"""The quenching MCP entry in the harness files a target already owns, and the Python prefix it calls.

Pure: each function takes the current text (or None) and returns the new text, or None when the
file is not safe to touch. `.mcp.json`, `.codex/config.toml` and `opencode.json` belong to the user;
only the `quenching` entry is the deploy's, so every other entry survives a write and a removal.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

NAME = "quenching"
CLAUDE = ".mcp.json"
CODEX = ".codex/config.toml"
OPENCODE = "opencode.json"
FILES = (CLAUDE, CODEX, OPENCODE)
FALLBACK = ("python3",)
# The prefixes tried in order on this machine; no version is pinned, only a floor (`pyproject.toml`).
CANDIDATES = (("python3",), ("python",), ("py", "-3"))
_PROBE = "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
# Conductor → orchestrator → runner → child: the chain an orchestrated run needs in OpenCode,
# whose own default is 1.
SUBAGENT_DEPTH = 4
_TABLE = re.compile(r"^\s*\[+\s*([^\]]*?)\s*\]+\s*(#.*)?$")


def _runs(prefix: tuple[str, ...]) -> bool:
    if shutil.which(prefix[0]) is None:
        return False
    try:
        done = subprocess.run([*prefix, "-c", _PROBE], capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def python_prefix(declared: list[str] | None, recorded: list[str] | None) -> list[str]:
    """The prefix that finds Python: the declared one, the one the last deploy recorded (so two
    machines never flap), the first that runs here, else `python3`."""
    for chosen in (declared, recorded):
        if chosen:
            return list(chosen)
    for candidate in CANDIDATES:
        if _runs(candidate):
            return list(candidate)
    return list(FALLBACK)


def _through_uv(prefix: list[str] | tuple[str, ...]) -> bool:
    head = re.split(r"[\\/]", prefix[0])[-1].lower() if prefix else ""
    return head.removesuffix(".exe") in {"uv", "uvx"}


def direct_prefix(prefix: list[str], recorded: list[str] | None = None) -> tuple[list[str], str | None]:
    """The prefix the hooks and the MCP server run under, and a warning when it differs.

    A hook runs on every shell call and the MCP server must answer before the harness times out,
    so neither goes through `uv`/`uvx`: `uv run` resolves the project each time and exits 2 on
    failure, which Claude Code reads as a block of the tool call.
    Such a prefix gives way to the one the last deploy recorded for the hooks, else the first of
    `CANDIDATES` that runs here, else `python3`; any other prefix is kept as it is."""
    if not _through_uv(prefix):
        return list(prefix), None
    if recorded and not _through_uv(recorded):
        chosen = list(recorded)
    else:
        chosen = next((list(c) for c in CANDIDATES if _runs(c)), list(FALLBACK))
    return chosen, (f"`{' '.join(prefix)}` runs through uv, which resolves the project on every "
                    f"call and exits 2 on failure, blocking every shell call; the hooks and the MCP "
                    f"server run `{' '.join(chosen)}` directly")


def _load(text: str | None, section: str) -> dict | None:
    if text is None or not text.strip():
        return {}
    try:
        document = json.loads(text)
    except ValueError:
        return None
    if not isinstance(document, dict) or not isinstance(document.get(section, {}), dict):
        return None
    return document


def _dump(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _object(document: dict, key: str) -> dict | None:
    """`document[key]` when it is an object or absent (created), else None: not ours to touch."""
    value = document.setdefault(key, {})
    return value if isinstance(value, dict) else None


def _depth_wanted(current, depth: int) -> bool:
    """The user's depth is missing or lower, so the deploy has to write its own."""
    return not (isinstance(current, int) and not isinstance(current, bool) and current >= depth)


def written_extras(text: str | None, extras: dict) -> dict:
    """What merging `extras` into `text` changes — the only part the manifest may record, so that
    removal never deletes a depth or an allow the user already had. A key already holding the
    wanted value is the user's and is left out."""
    try:
        document = json.loads(text) if text and text.strip() else {}
    except ValueError:
        document = {}
    document = document if isinstance(document, dict) else {}
    depth = extras.get("subagentDepth")
    experimental = document.get("experimental")
    current = experimental.get("subagent_depth") if isinstance(experimental, dict) else None
    permission = document.get("permission")
    directories = permission.get("external_directory") if isinstance(permission, dict) else None
    directories = directories if isinstance(directories, dict) else {}
    return {"subagentDepth": depth if depth is not None and _depth_wanted(current, depth) else None,
            "externalDirectory": [p for p in extras.get("externalDirectory") or []
                                  if directories.get(p) != "allow"]}


def _opencode_extras(document: dict, extras: dict) -> bool:
    """Write `experimental.subagent_depth` and the `external_directory` allows; False when a key
    holds a shape the merge cannot extend."""
    depth = extras.get("subagentDepth")
    if depth is not None:
        experimental = _object(document, "experimental")
        if experimental is None:
            return False
        current = experimental.get("subagent_depth")
        if _depth_wanted(current, depth):
            experimental["subagent_depth"] = depth
    patterns = extras.get("externalDirectory") or []
    if patterns:
        permission = _object(document, "permission")
        if permission is None:
            return False
        directories = _object(permission, "external_directory")
        if directories is None:
            return False
        for pattern in patterns:
            directories[pattern] = "allow"
    return True


def _prune(document: dict, *path: str) -> None:
    """Drop the empty objects along `path`, innermost first."""
    for depth in range(len(path), 0, -1):
        parent = document
        for key in path[:depth - 1]:
            parent = parent.get(key, {})
        if isinstance(parent, dict) and parent.get(path[depth - 1]) == {}:
            del parent[path[depth - 1]]


def _opencode_strip(document: dict, extras: dict) -> None:
    """Take out what the deploy wrote: the depth while it still holds the recorded value, and the
    recorded patterns while they still say `allow`."""
    depth = extras.get("subagentDepth")
    experimental = document.get("experimental")
    if depth is not None and isinstance(experimental, dict) \
            and experimental.get("subagent_depth") == depth:
        del experimental["subagent_depth"]
        _prune(document, "experimental")
    permission = document.get("permission")
    directories = permission.get("external_directory") if isinstance(permission, dict) else None
    if isinstance(directories, dict):
        for pattern in extras.get("externalDirectory") or []:
            if directories.get(pattern) == "allow":
                del directories[pattern]
        _prune(document, "permission", "external_directory")


def _json_merge(text: str | None, section: str, entry: dict, schema: bool,
                extras: dict | None = None) -> str | None:
    document = _load(text, section)
    if document is None:
        return None
    if schema:
        document.setdefault("$schema", "https://opencode.ai/config.json")
    document.setdefault(section, {})[NAME] = entry
    if extras and not _opencode_extras(document, extras):
        return None
    return _dump(document)


def _json_remove(text: str | None, section: str, extras: dict | None = None) -> str | None:
    """The file without the entry; `""` when nothing else is left; None when it is not safe."""
    document = _load(text, section)
    if document is None:
        return None
    servers = document.get(section, {})
    servers.pop(NAME, None)
    if not servers:
        document.pop(section, None)
    if extras:
        _opencode_strip(document, extras)
    if not set(document) - {"$schema"}:
        return ""
    return _dump(document)


def opencode_strip(text: str | None, extras: dict) -> str | None:
    """The `opencode.json` without the extras a past deploy wrote, its MCP entry kept; None when it
    is not safe to touch."""
    document = _load(text, "mcp")
    if document is None:
        return None
    _opencode_strip(document, extras)
    return _dump(document)


def jsonc_loads(text: str):
    """JSON with `//` and `/* */` comments and trailing commas, as OpenCode reads its config."""
    out, index, size = [], 0, len(text)
    while index < size:
        char = text[index]
        if char == '"':
            end = index + 1
            while end < size and text[end] != '"':
                end += 2 if text[end] == "\\" else 1
            out.append(text[index:end + 1])
            index = end + 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = size if newline < 0 else newline
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = size if close < 0 else close + 2
        else:
            out.append(char)
            index += 1
    return json.loads(re.sub(r",(\s*[}\]])", r"\1", "".join(out)))


def external_patterns(primary: str, home: str | None) -> list[str]:
    """The `external_directory` patterns of the primary checkout's siblings: the slots
    (`<parent>/<repo>-slot-*`) and the store (`<primary>.*`), `~/` when under `home`. OpenCode
    matches `<dir>/*` and its `*` crosses `/`, so each pattern covers every file below."""
    primary = os.path.normpath(primary).replace(os.sep, "/")
    parent, name = primary.rsplit("/", 1)
    patterns = [f"{parent}/{name}-slot-*", f"{primary}.*"]
    if home:
        home = os.path.normpath(home).replace(os.sep, "/").rstrip("/")
        patterns = ["~" + p[len(home):] if p.startswith(home + "/") else p for p in patterns]
    return patterns


GLOBAL_NAMES = ("opencode.json", "opencode.jsonc", "config.json")


def global_config_paths(env: dict) -> list[str]:
    """Where OpenCode looks for the user's config: `OPENCODE_CONFIG`, then
    `$XDG_CONFIG_HOME/opencode/` (default `~/.config/opencode/`)."""
    paths = [env["OPENCODE_CONFIG"]] if env.get("OPENCODE_CONFIG") else []
    home = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return [*paths, *(os.path.join(home, "opencode", name) for name in GLOBAL_NAMES)]


def global_experimental(env: dict) -> tuple[dict[str, list[str]], list[str]]:
    """`({path: experimental keys}, unreadable paths)` across the user's config files."""
    found: dict[str, list[str]] = {}
    unreadable: list[str] = []
    for path in global_config_paths(env):
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                document = jsonc_loads(handle.read())
        except (OSError, ValueError):
            unreadable.append(path)
            continue
        experimental = document.get("experimental") if isinstance(document, dict) else None
        if isinstance(experimental, dict) and experimental:
            found[path] = sorted(experimental)
    return found, unreadable


def hidden_by_depth(text: str | None, env: dict) -> tuple[dict[str, list[str]], list[str]]:
    """The global `experimental` keys a project block would hide (anomalyco/opencode#60410: the
    project block replaces the global one), per file, and the global files that could not be read.
    A key the project block already carries hides nothing new, and neither does `subagent_depth`."""
    document = _load(text, "mcp") or {}
    project = document.get("experimental")
    have = set(project) if isinstance(project, dict) else set()
    found, unreadable = global_experimental(env)
    hidden = {path: [key for key in keys if key not in have and key != "subagent_depth"]
              for path, keys in found.items()}
    return {path: keys for path, keys in hidden.items() if keys}, unreadable


def _block_ends(lines: list[str]) -> tuple[int, int] | None:
    """The span of the `[mcp_servers.quenching]` table and its subtables, if present."""
    head = f"mcp_servers.{NAME}"
    start = end = None
    for index, line in enumerate(lines):
        match = _TABLE.match(line)
        if not match:
            continue
        name = match.group(1).replace('"', "").replace("'", "").replace(" ", "")
        if name == head or name.startswith(head + "."):
            start = index if start is None else start
            end = index + 1
        elif start is not None:
            break
    if start is None:
        return None
    while end < len(lines) and not _TABLE.match(lines[end]):
        end += 1
    return start, end


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def toml_merge(text: str | None, argv: list[str]) -> str:
    block = [f"[mcp_servers.{NAME}]", f"command = {_toml_string(argv[0])}",
             "args = [" + ", ".join(_toml_string(part) for part in argv[1:]) + "]"]
    lines = (text or "").splitlines()
    span = _block_ends(lines)
    if span is None:
        kept = lines
        if kept and kept[-1].strip():
            kept = [*kept, ""]
        return "\n".join([*kept, *block]) + "\n"
    start, end = span
    tail = lines[end:]
    spacer = [""] if tail and tail[0].strip() else []
    return "\n".join([*lines[:start], *block, *spacer, *tail]) + "\n"


def toml_remove(text: str | None) -> str:
    lines = (text or "").splitlines()
    span = _block_ends(lines)
    if span is not None:
        lines = [*lines[:span[0]], *lines[span[1]:]]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def merged(rel: str, text: str | None, argv: list[str], extras: dict | None = None) -> str | None:
    """The file with the quenching entry written, or None when it is not safe to touch. `extras`
    (`opencode.json` only) is `{subagentDepth, externalDirectory}`."""
    if rel == CODEX:
        return toml_merge(text, argv)
    if rel == OPENCODE:
        entry = {"type": "local", "command": list(argv), "enabled": True}
        return _json_merge(text, "mcp", entry, schema=True, extras=extras)
    return _json_merge(text, "mcpServers", {"command": argv[0], "args": list(argv[1:])}, False)


def removed(rel: str, text: str | None, extras: dict | None = None) -> str | None:
    """The file without the quenching entry (and, for `opencode.json`, the recorded `extras`);
    `""` means nothing else is left."""
    if rel == CODEX:
        return toml_remove(text)
    if rel == OPENCODE:
        return _json_remove(text, "mcp", extras)
    return _json_remove(text, "mcpServers")


def harness_files(harnesses: list[str]) -> list[str]:
    wanted = {"claude": CLAUDE, "codex": CODEX, "opencode": OPENCODE}
    return [wanted[harness] for harness in harnesses if harness in wanted]
