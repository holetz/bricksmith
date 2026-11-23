"""The project registry — `~/.config/quenching/projects.json`, written only by `cq portal`.

Servers read it; nothing but these verbs writes it. The orca import opens one file per profile
(`profiles/*/orca-data.json`) and keeps `repos[].path` and `repos[].displayName` — every other
field, tokens and credentials included, is dropped the moment it is parsed.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess

from quenching.common.io import read_text, write_text

SCHEMA_VERSION = 1


class PortalRefusal(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def registry_path() -> pathlib.Path:
    return pathlib.Path(os.path.expanduser("~")) / ".config" / "quenching" / "projects.json"


def load() -> dict:
    path = registry_path()
    text = read_text(str(path))
    if text is None:
        return {"schemaVersion": SCHEMA_VERSION, "projects": []}
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if (not isinstance(data, dict) or data.get("schemaVersion") != SCHEMA_VERSION
            or not isinstance(data.get("projects"), list)):
        raise PortalRefusal("portal-registry-corrupt",
                            f"{path} is unreadable or has an unknown schemaVersion; not overwritten")
    return data


def save(data: dict) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(str(path), json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def repo_root(folder: str) -> str:
    """The repository's main root: the parent of the git common dir, never a worktree."""
    folder = os.path.abspath(os.path.expanduser(folder))
    if not os.path.isdir(folder):
        raise PortalRefusal("portal-not-a-repo", f"not a directory: {folder}")
    run = subprocess.run(["git", "-C", folder, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"], capture_output=True, text=True)
    common = run.stdout.strip()
    if run.returncode != 0 or not common:
        raise PortalRefusal("portal-not-a-repo", f"not inside a git repository: {folder}")
    common_path = pathlib.Path(common).resolve()
    if common_path.name != ".git":
        raise PortalRefusal("portal-not-a-repo", f"bare repository, no working root: {folder}")
    return str(common_path.parent)


def add(folder: str, name: str | None = None) -> dict:
    root = repo_root(folder)
    data = load()
    return _add_root(data, root, name, save_now=True)


def _add_root(data: dict, root: str, name: str | None, save_now: bool) -> dict:
    projects = data["projects"]
    existing = next((p for p in projects if p.get("path") == root), None)
    if existing is not None:
        if name and existing.get("name") != name:
            _check_name(projects, name, root)
            existing["name"] = name
            if save_now:
                save(data)
        return {"added": False, "project": dict(existing)}
    chosen = name or os.path.basename(root)
    _check_name(projects, chosen, root)
    entry = {"name": chosen, "path": root}
    projects.append(entry)
    if save_now:
        save(data)
    return {"added": True, "project": dict(entry)}


def _check_name(projects: list, name: str, root: str) -> None:
    if any(p.get("name") == name and p.get("path") != root for p in projects):
        raise PortalRefusal("portal-name-taken", f"another project is already named {name!r}")


def remove(target: str) -> dict:
    data = load()
    projects = data["projects"]
    by_name = [p for p in projects if p.get("name") == target]
    if len(by_name) > 1:
        raise PortalRefusal("portal-name-ambiguous", f"more than one project is named {target!r}")
    match = by_name[0] if by_name else None
    if match is None:
        try:
            root = repo_root(target)
        except PortalRefusal:
            root = os.path.abspath(os.path.expanduser(target))
        match = next((p for p in projects if p.get("path") == root), None)
    if match is None:
        raise PortalRefusal("portal-project-not-found", f"no registered project matches {target!r}")
    projects.remove(match)
    save(data)
    return {"removed": dict(match)}


def list_projects() -> list[dict]:
    return [dict(p) for p in load()["projects"]]


def _orca_repos(orca_dir: str) -> tuple[list[tuple[str, str | None]], int]:
    found: list[tuple[str, str | None]] = []
    files = sorted(pathlib.Path(orca_dir).glob("profiles/*/orca-data.json"))
    read = 0
    for file in files:
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        read += 1
        repos = doc.get("repos") if isinstance(doc, dict) else None
        for repo in repos if isinstance(repos, list) else []:
            path = repo.get("path") if isinstance(repo, dict) else None
            display = repo.get("displayName") if isinstance(repo, dict) else None
            if isinstance(path, str) and path:
                found.append((path, display if isinstance(display, str) and display else None))
    return found, read


def import_orca(orca_dir: str | None = None) -> dict:
    data = load()
    if data.get("orcaImport"):
        raise PortalRefusal("portal-orca-already-imported",
                            "the orca import is one-time and already happened")
    base = orca_dir or os.path.join(os.path.expanduser("~"), ".config", "orca")
    repos, read = _orca_repos(base)
    if not read:
        raise PortalRefusal("portal-orca-not-found",
                            f"no profiles/*/orca-data.json readable under {base}")
    added, kept, skipped = [], [], []
    for path, display in repos:
        try:
            root = repo_root(path)
            result = _add_root(data, root, display, save_now=False)
        except PortalRefusal as exc:
            skipped.append({"path": path, "reason": exc.code})
            continue
        (added if result["added"] else kept).append(result["project"])
    data["orcaImport"] = {"date": datetime.date.today().isoformat()}
    save(data)
    return {"added": added, "unchanged": kept, "skipped": skipped}
