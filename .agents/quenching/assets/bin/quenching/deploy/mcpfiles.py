"""The quenching MCP entry in the harness files a target already owns, and the Python prefix it calls.

Pure: each function takes the current text (or None) and returns the new text, or None when the
file is not safe to touch. `.mcp.json`, `.codex/config.toml` and `opencode.json` belong to the user;
only the `quenching` entry is the deploy's, so every other entry survives a write and a removal.
"""
from __future__ import annotations

import json
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


def _json_merge(text: str | None, section: str, entry: dict, schema: bool) -> str | None:
    document = _load(text, section)
    if document is None:
        return None
    if schema:
        document.setdefault("$schema", "https://opencode.ai/config.json")
    document.setdefault(section, {})[NAME] = entry
    return _dump(document)


def _json_remove(text: str | None, section: str) -> str | None:
    """The file without the entry; `""` when nothing else is left; None when it is not safe."""
    document = _load(text, section)
    if document is None:
        return None
    servers = document.get(section, {})
    servers.pop(NAME, None)
    if not servers:
        document.pop(section, None)
    if not set(document) - {"$schema"}:
        return ""
    return _dump(document)


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


def merged(rel: str, text: str | None, argv: list[str]) -> str | None:
    """The file with the quenching entry written, or None when it is not safe to touch."""
    if rel == CODEX:
        return toml_merge(text, argv)
    if rel == OPENCODE:
        entry = {"type": "local", "command": list(argv), "enabled": True}
        return _json_merge(text, "mcp", entry, schema=True)
    return _json_merge(text, "mcpServers", {"command": argv[0], "args": list(argv[1:])}, False)


def removed(rel: str, text: str | None) -> str | None:
    """The file without the quenching entry; `""` means nothing else is left."""
    if rel == CODEX:
        return toml_remove(text)
    return _json_remove(text, "mcp" if rel == OPENCODE else "mcpServers")


def harness_files(harnesses: list[str]) -> list[str]:
    wanted = {"claude": CLAUDE, "codex": CODEX, "opencode": OPENCODE}
    return [wanted[harness] for harness in harnesses if harness in wanted]
