"""Build the operations front's read-only inventory.

The walk is intentionally the only place that discovers files.  Python files are parsed with
``ast`` and never imported; shell and Windows entry points are represented by path and lifecycle
only.  Checks consume the resulting :class:`~quenching.ops.model.Inventory` rather than walking
the tree again.
"""
from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from quenching.ops.config import load_ops_config
from quenching.ops.model import EntryPoint, Inventory, Router


_NON_PYTHON_SUFFIXES = frozenset({
    ".bash", ".bat", ".cmd", ".fish", ".ps1", ".sh", ".zsh",
})
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__",
})
_WRITE_METHODS = frozenset({
    "dump", "dump_json", "to_csv", "to_json", "to_pickle", "write", "write_bytes",
    "write_text",
})
_ROUTER_KINDS = {
    "justfile": "justfile",
    "justfile.toml": "justfile",
    "pyproject.toml": "python-console-script",
    "setup.cfg": "python-console-script",
    "setup.py": "python-console-script",
    "taskfile.yml": "Taskfile.yml",
    "taskfile.yaml": "Taskfile.yml",
}


def _path(root: str, absolute: str) -> str:
    return os.path.relpath(absolute, root).replace(os.sep, "/")


def _module_name(relative: str) -> str:
    stem = relative[:-3] if relative.endswith(".py") else relative
    parts = stem.split("/")
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(part for part in parts if part)


def _invocation(relative: str) -> str:
    stem = relative.rsplit(".", 1)[0] if "." in relative.rsplit("/", 1)[-1] else relative
    return "/" + stem


def _purpose(tree: ast.AST) -> str:
    doc = ast.get_docstring(tree, clean=False) or ""
    return doc.splitlines()[0].strip() if doc.splitlines() else ""


def _literal_string(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _flags(tree: ast.AST) -> tuple[str, ...]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        function = node.func
        if not isinstance(function, ast.Attribute) or function.attr != "add_argument":
            continue
        for argument in node.args:
            value = _literal_string(argument)
            if value and value.startswith("-"):
                found.add(value)
    return tuple(sorted(found))


def _is_write_call(node: ast.Call) -> bool:
    function = node.func
    if isinstance(function, ast.Attribute):
        return function.attr in _WRITE_METHODS or function.attr in {"copy", "remove", "unlink"}
    if isinstance(function, ast.Name) and function.id == "open":
        mode = _literal_string(node.args[1]) if len(node.args) > 1 else None
        return mode is None or any(flag in mode for flag in ("w", "a", "x", "+"))
    return False


def _writes_outside_repo(tree: ast.AST) -> bool:
    """Return a conservative write signal for the inventory.

    A write whose destination is not statically knowable is still reported as a possible external
    write.  The doctor must not call a target operation merely to discover where it writes.
    """
    return any(isinstance(node, ast.Call) and _is_write_call(node) for node in ast.walk(tree))


def _lifecycle(relative: str, registry: dict[str, str]) -> str | None:
    if relative in registry:
        return registry[relative]
    if any(part == "_archive" for part in relative.split("/")):
        return "archived"
    return None


_STATE_REASON = r"(active|archived)(?:\s*(?:[(:,;\u2014\u2013-].*|\d.*|since\b.*)?)"
_STATE_CELL = r"(active|archived)\b.*"
_SCRIPT_SUFFIXES = (".py", ".sh", ".bash", ".cmd", ".bat", ".ps1")


def _plain(text: str) -> str:
    """Drop code-span and emphasis marks and case so a state reads the same in any styling."""
    return re.sub(r"[`*_]", "", text).strip().lower()


def _registry_lifecycle(root: str, registry_path: str | None = None) -> dict[str, str]:
    """Read lifecycle declarations without imposing a registry format on a missing registry."""
    path = registry_path or os.path.join(root, "registry.md")
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    out: dict[str, str] = {}
    column: int | None = None
    in_zone = False
    in_table = False
    for line in text.splitlines():
        if "quenching-ops-registry-start" in line:
            in_zone = True
        elif "quenching-ops-registry-end" in line:
            in_zone = False
            continue
        if in_zone:
            continue
        is_row = line.lstrip().startswith("|")
        if is_row and not in_table:
            column = None
        in_table = is_row
        if is_row:
            cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
            lowered = [cell.lower() for cell in cells]
            if "lifecycle" in lowered:
                column = lowered.index("lifecycle")
                continue
            if column is None or column >= len(cells):
                continue
            cell = re.fullmatch(_STATE_CELL, _plain(cells[column]))
            if not cell:
                continue
            state = cell.group(1)
            match = next(
                (m for c in cells for m in re.finditer(r"`([^`]+)`", c)
                 if m.group(1).replace("\\", "/").endswith(_SCRIPT_SUFFIXES)),
                None,
            )
        else:
            declared = re.fullmatch(
                r"\s*(?:[-*+]\s+)?`([^`]+)`\s*[—–-]+\s*(.*)", line
            )
            if not declared:
                continue
            tail = re.fullmatch(_STATE_REASON, _plain(declared.group(2)))
            if not tail:
                continue
            match, state = declared, tail.group(1)
        if match:
            candidate = match.group(1).replace("\\", "/").removeprefix("./")
            if candidate.endswith(_SCRIPT_SUFFIXES):
                out[candidate] = state
    return out


def _router_entries(path: str, kind: str | None) -> tuple[str, ...]:
    if not kind:
        return ()
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ()
    entries: set[str] = set()
    if kind == "python-console-script":
        try:
            import tomllib
            if path.endswith("pyproject.toml"):
                data = tomllib.loads(text)
                scripts = data.get("project", {}).get("scripts", {})
                if isinstance(scripts, dict):
                    entries.update(str(name) for name in scripts)
        except (ImportError, OSError, TypeError, ValueError):
            pass
        if path.endswith("setup.cfg"):
            in_scripts = False
            for line in text.splitlines():
                if line.strip().lower() == "[options.entry_points]":
                    in_scripts = False
                    continue
                if line.strip().lower().startswith("console_scripts"):
                    in_scripts = True
                    continue
                if in_scripts:
                    match = re.match(r"^\s*([A-Za-z0-9_.-]+)\s*=", line)
                    if match:
                        entries.add(match.group(1))
    elif kind == "justfile":
        entries.update(match.group(1) for match in re.finditer(
            r"^\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:[^:]*):", text, re.MULTILINE
        ))
    elif kind == "Taskfile.yml":
        in_tasks = False
        for line in text.splitlines():
            if line.strip() == "tasks:":
                in_tasks = True
                continue
            if in_tasks:
                match = re.match(r"^  ([A-Za-z][A-Za-z0-9_-]*):\s*$", line)
                if match:
                    entries.add(match.group(1))
    return tuple(sorted(entries))


def _router(root: str, declared_router: str) -> Router:
    relative = _path(root, declared_router)
    name = os.path.basename(declared_router).lower()
    kind = _ROUTER_KINDS.get(name)
    return Router(relative, kind, os.path.isfile(declared_router),
                  _router_entries(declared_router, kind))


def _candidates(root: str) -> list[str]:
    found: list[str] = []
    for directory, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if name not in _SKIP_DIRS)
        for filename in sorted(filenames):
            path = os.path.join(directory, filename)
            relative = _path(root, path)
            suffix = Path(filename).suffix.lower()
            if suffix == ".py" or suffix in _NON_PYTHON_SUFFIXES:
                found.append(relative)
            elif not suffix and os.access(path, os.X_OK):
                found.append(relative)
    return sorted(found)


def build_inventory(root: str) -> tuple[Inventory | None, dict]:
    """Return one inventory for the configured operations root, or a refusal."""
    cfg, err = load_ops_config(root)
    if err:
        return None, err
    assert cfg is not None
    ops_root = cfg["opsRoot"]
    if not os.path.isdir(ops_root):
        return None, {
            "code": "op-root-missing",
            "exit": 2,
            "path": cfg["declaredOpsRoot"],
            "message": f"configured opsRoot `{cfg['declaredOpsRoot']}` does not exist",
        }

    registry = _registry_lifecycle(ops_root, cfg["registry"])
    entries: list[EntryPoint] = []
    for relative in _candidates(ops_root):
        absolute = os.path.join(ops_root, relative)
        language = "python" if relative.endswith(".py") else "non-python"
        if language == "python":
            try:
                source = Path(absolute).read_text(encoding="utf-8")
                tree = ast.parse(source, filename=absolute)
            except (OSError, UnicodeDecodeError, SyntaxError):
                tree = ast.Module(body=[], type_ignores=[])
            entry = EntryPoint(
                path=relative,
                module=_module_name(relative),
                invocation=_invocation(relative),
                purpose=_purpose(tree),
                flags=_flags(tree),
                writes_outside_repo=_writes_outside_repo(tree),
                lifecycle=_lifecycle(relative, registry),
                language=language,
            )
        else:
            entry = EntryPoint(
                path=relative,
                module=None,
                invocation=_invocation(relative),
                purpose="",
                lifecycle=_lifecycle(relative, registry),
                language=language,
            )
        entries.append(entry)
    return Inventory(ops_root, _router(ops_root, cfg["router"]), tuple(entries)), {}


def inventory(root: str) -> tuple[Inventory | None, dict]:
    """Public spelling for callers that treat the traversal as an operation."""
    return build_inventory(root)
