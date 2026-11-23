"""Answer the catalog controls one owner holds, without writing the target repository.

``present`` means candidate evidence exists, never conformance: ``humanRequired`` keeps the
interpretation the catalog asks for. Provider facts are never read, so a requirement that needs
one is ``not-measured``.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Callable

CATALOG = Path(__file__).resolve().parents[3] / "opensource" / "controls.json"
STATES = ("present", "missing", "not-measured", "refused", "retired")

_WORKFLOW_SUFFIXES = (".yml", ".yaml")
_USES = re.compile(r"^\s*-?\s*uses\s*:\s*['\"]?([^'\"\s#]+)")
_SHA_REF = re.compile(r"@[0-9a-f]{40}$")
_PR_TARGET = re.compile(r"\bpull_request_target\b")


def load_catalog(path: Path = CATALOG) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _first_file(directory: Path) -> Path | None:
    for current, dirs, files in os.walk(directory):
        if files:
            return Path(current, sorted(files)[0])
        dirs.sort()
    return None


def _match(root: Path, pattern: str) -> str | None:
    """Return the first path the pattern names under ``root``, reading as little as possible."""
    if pattern.endswith("/**"):
        directory = root / pattern[:-3]
        if pattern == ".git/**":
            return ".git" if directory.exists() else None
        if not directory.is_dir():
            return None
        found = _first_file(directory)
        return found.relative_to(root).as_posix() if found else None
    for found in sorted(root.glob(pattern)):
        if found.is_file():
            return found.relative_to(root).as_posix()
    return None


def _workflows(root: Path) -> list[Path]:
    directory = root / ".github" / "workflows"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*")
                  if path.is_file() and path.suffix.lower() in _WORKFLOW_SUFFIXES)


def _read_workflows(root: Path) -> tuple[list[tuple[str, str]], list[str]]:
    texts, unreadable = [], []
    for path in _workflows(root):
        relative = path.relative_to(root).as_posix()
        try:
            texts.append((relative, path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            unreadable.append(relative)
    return texts, unreadable


def pinned_actions(root: Path) -> dict[str, Any]:
    """Every remote ``uses:`` in the workflows is pinned to a full commit hash."""
    texts, unreadable = _read_workflows(root)
    if unreadable:
        return {"state": "refused", "evidence": unreadable, "reason": "unreadable"}
    if not texts:
        return {"state": "missing", "evidence": [], "reason": "no workflow files"}
    unpinned = []
    for relative, text in texts:
        for number, line in enumerate(text.splitlines(), 1):
            found = _USES.match(line)
            if not found:
                continue
            ref = found.group(1)
            if ref.startswith("./") or (ref.startswith("docker://") and "@sha256:" in ref):
                continue
            if not _SHA_REF.search(ref):
                unpinned.append(f"{relative}:{number}")
    if unpinned:
        return {"state": "missing", "evidence": unpinned, "reason": "unpinned uses"}
    return {"state": "present", "evidence": [relative for relative, _ in texts]}


def _strip_yaml_comment(line: str) -> str:
    """The line without its YAML comment; anything ambiguous is kept, so a real trigger still matches."""
    quote = None
    previous = " "
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif quote == '"' and char == "\\":
            escaped = True
        elif quote:
            if char == quote and quote == "'" and line[index + 1:index + 2] == "'":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"" and (previous.isspace() or previous in ":[{,-"):
            quote = char
        elif char == "#" and previous.isspace():
            return line[:index]
        previous = char
    return line


def untrusted_trigger(root: Path) -> dict[str, Any]:
    """No workflow runs on ``pull_request_target``; a YAML comment naming it is not a trigger."""
    texts, unreadable = _read_workflows(root)
    if unreadable:
        return {"state": "refused", "evidence": unreadable, "reason": "unreadable"}
    if not texts:
        return {"state": "missing", "evidence": [], "reason": "no workflow files"}
    hazards = [relative for relative, text in texts
               if _PR_TARGET.search("\n".join(_strip_yaml_comment(line) for line in text.splitlines()))]
    if hazards:
        return {"state": "missing", "evidence": hazards, "reason": "pull_request_target"}
    return {"state": "present", "evidence": [relative for relative, _ in texts]}


PROBES: dict[str, Callable[[Path], dict[str, Any]]] = {
    "SCORECARD-PINNING.01": pinned_actions,
    "OSPS-BR-01.01": untrusted_trigger,
}


def _requirement(root: Path, requirement: dict[str, Any]) -> dict[str, Any]:
    evidence = requirement.get("evidence", {})
    result: dict[str, Any] = {
        "id": requirement["id"],
        "levels": requirement.get("levels", []),
        "humanRequired": bool(evidence.get("human_required")),
    }
    if requirement.get("state") == "retired":
        return {**result, "state": "retired", "evidence": []}
    if requirement.get("provider_required"):
        return {**result, "state": "not-measured", "evidence": [], "reason": "provider-not-read"}
    probe = PROBES.get(requirement["id"])
    if probe:
        return {**result, **probe(root)}
    try:
        matched = [found for found in (_match(root, pattern) for pattern in evidence.get("paths", []))
                   if found]
    except OSError:
        return {**result, "state": "refused", "evidence": [], "reason": "unreadable"}
    return {**result, "state": "present" if matched else "missing", "evidence": matched}


def owner_controls(owner: str, root: str | Path, catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    """The controls the catalog routes to ``owner``, each requirement with its state."""
    data = catalog if catalog is not None else load_catalog()
    repository = Path(root)
    controls = []
    for control in data["controls"]:
        if control.get("owner") != owner:
            continue
        controls.append({
            "id": control["id"],
            "title": control.get("title", ""),
            "optional": bool(control.get("optional")),
            "requirements": [_requirement(repository, item) for item in control["requirements"]],
        })
    counts = Counter(item["state"] for control in controls for item in control["requirements"])
    return {
        "owner": owner,
        "baseline": data.get("baseline", {}).get("version"),
        "readOnly": True,
        "controls": controls,
        "summary": {state: counts.get(state, 0) for state in STATES},
    }


def summary_line(block: dict[str, Any]) -> str:
    summary = block["summary"]
    return (f"controls: {summary['present']} present, {summary['missing']} missing, "
            f"{summary['not-measured']} not-measured")
