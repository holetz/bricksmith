"""Build the toolchain front's static cross-file inventory.

The inventory reads well-known root artifacts and parses their declaration shape. It never
imports a target package or launches a package manager, compiler, formatter or test runner.
"""
from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from quenching.toolchain.model import Artifact, ToolchainInventory
from quenching.toolchain.probe import LANGUAGE_PINS, LOCKS, MANIFESTS, probe_toolchain


_PACKAGE_TOOL_KEYS = frozenset({
    "babel",
    "babelrc",
    "browserslist",
    "eslintConfig",
    "jest",
    "prettier",
    "release",
    "stylelint",
    "volta",
})


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    text = _read_text(path)
    if text is None:
        return None, "unreadable"
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None, "invalid-json"
    if not isinstance(value, dict):
        return None, "not-object"
    return value, None


def _read_toml(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    text = _read_text(path)
    if text is None:
        return None, "unreadable"
    try:
        import tomllib
        value = tomllib.loads(text)
    except (ImportError, TypeError, ValueError):
        return None, "invalid-toml"
    if not isinstance(value, dict):
        return None, "not-object"
    return value, None


def _names(value: Any) -> list[str]:
    if isinstance(value, dict):
        return sorted(str(key) for key in value)
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str):
                result.append(item.split(" ", 1)[0])
        return sorted(set(result))
    return []


def _string_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in sorted(value.items())
            if isinstance(item, (str, int, float, bool))}


def _status(error: str | None) -> dict[str, str]:
    return {"parse": "ok" if error is None else error}


def _arbiter_entries(text: str | None) -> list[dict[str, str]]:
    if not text:
        return []
    match = re.search(r"quenching-arbiters-data (\{.*\})", text)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return [
        {"key": item["key"], "arbiter": item["arbiter"]}
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("key"), str)
        and isinstance(item.get("arbiter"), str)
    ]


def _split_requirement(item: str) -> tuple[str, str]:
    """Split a PEP 508 requirement into its bare name and its specifier."""
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*(.*)", item)
    if not match:
        return "", ""
    return match.group(1), match.group(2).split(";", 1)[0].strip()


def _pyproject(path: Path, root: Path) -> tuple[Artifact, list[Artifact]]:
    data, error = _read_toml(path)
    text = _read_text(path)
    project = data.get("project", {}) if data else {}
    build = data.get("build-system", {}) if data else {}
    tool = data.get("tool", {}) if data else {}
    groups = data.get("dependency-groups", {}) if data else {}
    dev_specs = {}
    if isinstance(groups, dict):
        for group, values in groups.items():
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, str):
                        name, specifier = _split_requirement(item)
                        if name:
                            dev_specs[name] = specifier
    semantic_keys = []
    if isinstance(project, dict) and isinstance(project.get("scripts"), dict):
        semantic_keys.append("project.scripts")
    if isinstance(tool, dict):
        semantic_keys.extend(f"tool.{key}" for key in ("coverage", "pytest")
                             if isinstance(tool.get(key), dict))
    arbiter_entries = _arbiter_entries(text)
    canonical = json.dumps({"artifact": "pyproject.toml", "entries": sorted(
        arbiter_entries, key=lambda item: item["key"])},
        sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    digest_match = re.search(r"quenching-arbiters-sha256 ([0-9a-f]{64})", text or "")
    details = {
        **_status(error),
        "ecosystem": "python",
        "name": project.get("name") if isinstance(project, dict) else None,
        "version": project.get("version") if isinstance(project, dict) else None,
        "requiresPython": project.get("requires-python") if isinstance(project, dict) else None,
        "dependencies": _names(project.get("dependencies")) if isinstance(project, dict) else [],
        "devDependencies": sorted(dev_specs),
        "devDependencySpecs": dev_specs,
        "optionalDependencies": sorted(project.get("optional-dependencies", {}))
        if isinstance(project, dict) and isinstance(project.get("optional-dependencies"), dict) else [],
        "buildBackend": build.get("build-backend") if isinstance(build, dict) else None,
        "toolKeys": sorted(tool) if isinstance(tool, dict) else [],
        "uvPackage": tool.get("uv", {}).get("package")
        if isinstance(tool, dict) and isinstance(tool.get("uv"), dict) else None,
        "semanticKeys": sorted(semantic_keys),
        "arbiterEntries": arbiter_entries,
        "arbiterDigest": digest_match.group(1) if digest_match else None,
        "arbiterDigestExpected": digest,
    }
    tools = []
    if isinstance(tool, dict):
        tools.append(Artifact(_relative(root, path), "pyproject-tool", {
            **_status(error), "keys": sorted(tool)
        }))
    return Artifact(_relative(root, path), "pyproject", details), tools


def _package_json(path: Path, root: Path) -> tuple[Artifact, list[Artifact]]:
    data, error = _read_json(path)
    data = data or {}
    tools = sorted(key for key in data if key in _PACKAGE_TOOL_KEYS)
    details = {
        **_status(error),
        "ecosystem": "node",
        "name": data.get("name"),
        "version": data.get("version"),
        "packageManager": data.get("packageManager"),
        "engines": _string_map(data.get("engines")),
        "dependencies": _names(data.get("dependencies")),
        "devDependencies": _names(data.get("devDependencies")),
        "devDependencySpecs": data.get("devDependencies", {})
        if isinstance(data.get("devDependencies"), dict) else {},
        "peerDependencies": _names(data.get("peerDependencies")),
        "scripts": sorted(data.get("scripts", {})) if isinstance(data.get("scripts"), dict) else [],
        "toolKeys": tools,
    }
    tool_artifacts = [Artifact(_relative(root, path), "package-tool", {
        **_status(error), "keys": tools
    })] if tools else []
    return Artifact(_relative(root, path), "package-json", details), tool_artifacts


def _setup_cfg(path: Path, root: Path) -> tuple[Artifact, list[Artifact]]:
    parser = configparser.ConfigParser(interpolation=None)
    error = None
    text = _read_text(path)
    if text is None:
        error = "unreadable"
    else:
        try:
            parser.read_string(text)
        except configparser.Error:
            error = "invalid-ini"
    sections = sorted(parser.sections()) if error is None else []
    metadata = parser["metadata"] if parser.has_section("metadata") else {}
    options = parser["options"] if parser.has_section("options") else {}
    details = {
        **_status(error),
        "ecosystem": "python",
        "name": metadata.get("name"),
        "version": metadata.get("version"),
        "requiresPython": options.get("python_requires"),
        "sections": sections,
        "toolKeys": [section for section in sections if section.startswith("options.")],
        "semanticKeys": [],
    }
    tool_artifacts = [Artifact(_relative(root, path), "setup-tool", {
        **_status(error), "keys": details["toolKeys"]
    })] if details["toolKeys"] else []
    return Artifact(_relative(root, path), "setup-cfg", details), tool_artifacts


def _cargo(path: Path, root: Path) -> tuple[Artifact, list[Artifact]]:
    data, error = _read_toml(path)
    package = data.get("package", {}) if data else {}
    workspace = data.get("workspace", {}) if data else {}
    details = {
        **_status(error),
        "ecosystem": "rust",
        "name": package.get("name") if isinstance(package, dict) else None,
        "version": package.get("version") if isinstance(package, dict) else None,
        "rustVersion": package.get("rust-version") if isinstance(package, dict) else None,
        "dependencies": _names(data.get("dependencies")) if data else [],
        "devDependencies": _names(data.get("dev-dependencies")) if data else [],
        "buildDependencies": _names(data.get("build-dependencies")) if data else [],
        "workspaceMembers": workspace.get("members", []) if isinstance(workspace, dict) else [],
        "toolKeys": sorted(key for key in (data or {}) if key not in {
            "package", "workspace", "dependencies", "dev-dependencies", "build-dependencies",
        }),
    }
    return Artifact(_relative(root, path), "cargo-toml", details), []


def _manifest(path: Path, root: Path) -> tuple[Artifact, list[Artifact]]:
    if path.name == "pyproject.toml":
        return _pyproject(path, root)
    if path.name == "package.json":
        return _package_json(path, root)
    if path.name == "setup.cfg":
        return _setup_cfg(path, root)
    return _cargo(path, root)


_TEXT_LOCK_VERSION = {
    "yarn.lock": re.compile(r"^# yarn lockfile (v\d+)|^  version: (\S+)", re.MULTILINE),
    "pnpm-lock.yaml": re.compile(r"^lockfileVersion:\s*['\"]?([^'\"\s]+)", re.MULTILINE),
}


def _text_lock(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    if text is None:
        return {"parse": "unreadable", "lockfileVersion": None}
    if not text.strip():
        return {"parse": "empty", "lockfileVersion": None}
    match = _TEXT_LOCK_VERSION[path.name].search(text)
    version = next((group for group in match.groups() if group), None) if match else None
    return {"parse": "ok", "lockfileVersion": version}


def _lock(path: Path, root: Path) -> Artifact:
    details: dict[str, Any]
    if path.name in _TEXT_LOCK_VERSION:
        details = _text_lock(path)
    elif path.suffix == ".json":
        data, error = _read_json(path)
        data = data or {}
        packages = data.get("packages")
        dependencies = data.get("dependencies")
        details = {
            **_status(error),
            "lockfileVersion": data.get("lockfileVersion"),
            "packageCount": len(packages) if isinstance(packages, dict) else 0,
            "dependencyCount": len(dependencies) if isinstance(dependencies, dict) else 0,
        }
    else:
        data, error = _read_toml(path)
        details = {
            **_status(error),
            "metadataKeys": sorted(data.get("metadata", {}))
            if isinstance(data, dict) and isinstance(data.get("metadata"), dict) else [],
            "packageCount": len(data.get("package", []))
            if isinstance(data, dict) and isinstance(data.get("package"), list) else 0,
        }
    return Artifact(_relative(root, path), path.name, details)


def _pin(path: Path, root: Path) -> Artifact:
    text = _read_text(path)
    value = next((line.strip() for line in (text or "").splitlines()
                  if line.strip() and not line.lstrip().startswith("#")), None)
    return Artifact(_relative(root, path), path.name, {
        "parse": "ok" if text is not None else "unreadable",
        "value": value,
    })


def build_inventory(root: str) -> tuple[ToolchainInventory | None, dict]:
    """Return one inventory for an applicable target, or ``None`` when not applicable."""
    repo_root = Path(os.path.abspath(root))
    applicability = probe_toolchain(str(repo_root))
    if applicability.state != "applicable":
        return None, {}

    manifests: list[Artifact] = []
    locks: list[Artifact] = []
    pins: list[Artifact] = []
    tool_configurations: list[Artifact] = []
    for name in MANIFESTS:
        path = repo_root / name
        if path.is_file():
            manifest, tools = _manifest(path, repo_root)
            manifests.append(manifest)
            tool_configurations.extend(tools)
    for name in LOCKS:
        path = repo_root / name
        if path.is_file():
            locks.append(_lock(path, repo_root))
    for name in LANGUAGE_PINS:
        path = repo_root / name
        if path.is_file():
            pins.append(_pin(path, repo_root))
    return ToolchainInventory(
        str(repo_root), tuple(manifests), tuple(locks), tuple(pins), tuple(tool_configurations)
    ), {}
