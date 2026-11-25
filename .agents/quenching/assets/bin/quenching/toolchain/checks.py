"""Static finding checks over one toolchain inventory."""
from __future__ import annotations

import re
from pathlib import Path

from quenching.common.front import register_checks
from quenching.toolchain.model import Artifact, Finding, ToolchainInventory


_LOCK_MANIFEST = {
    "uv.lock": "pyproject.toml",
    "poetry.lock": "pyproject.toml",
    "package-lock.json": "package.json",
    "yarn.lock": "package.json",
    "pnpm-lock.yaml": "package.json",
    "Cargo.lock": "Cargo.toml",
}
_ECOSYSTEM_MANIFEST = {"python": "pyproject.toml", "node": "package.json"}
_ARBITER_KEYS = {"project.scripts", "tool.coverage", "tool.pytest"}


def _finding(code: str, message: str, path: str) -> Finding:
    band = "Mechanical" if code in {
        "tc-lock-stale", "tc-config-duplicate", "tc-generated-stale"
    } else "Structural"
    return Finding(code, "error", message, path=path, band=band)


def check_lock_stale(inventory: ToolchainInventory) -> list[Finding]:
    manifests = {item.path for item in inventory.manifests}
    return [
        _finding("tc-lock-stale", f"lock `{lock.path}` is missing, invalid or has no matching manifest",
                 lock.path)
        for lock in inventory.locks
        if lock.details.get("parse") != "ok"
        or _LOCK_MANIFEST.get(Path(lock.path).name) not in manifests
    ]


def check_config_duplicate(inventory: ToolchainInventory) -> list[Finding]:
    owners: dict[str, list[str]] = {}
    for artifact in inventory.manifests:
        for key in artifact.details.get("semanticKeys", []):
            owners.setdefault(key, []).append(artifact.path)
    for artifact in inventory.tool_configurations:
        for key in artifact.details.get("keys", []):
            owners.setdefault(str(key), []).append(artifact.path)
    return [
        _finding("tc-config-duplicate", f"configuration key `{key}` is declared in multiple homes",
                 paths[0])
        for key, paths in sorted(owners.items())
        if len(paths) > 1
    ]


def check_generated_stale(inventory: ToolchainInventory) -> list[Finding]:
    findings = []
    for artifact in inventory.manifests:
        declared = artifact.details.get("arbiterDigest")
        expected = artifact.details.get("arbiterDigestExpected")
        if declared and declared != expected:
            findings.append(_finding(
                "tc-generated-stale", "generated configuration arbitration block is stale",
                artifact.path))
    return findings


def _version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"\d+(?:\.\d+)?", value)
    return match.group(0) if match else None


def _declared(value: object) -> tuple[str, str] | None:
    """A manifest value as `(version, kind)`: led by `>=` or `>` it is a floor the project
    accepts; anything else is the exact version it names, as before."""
    version = _version(value)
    if version is None:
        return None
    return version, "floor" if str(value).lstrip().startswith(">") else "exact"


def _key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _runtime(inventory: ToolchainInventory) -> dict[str, list[tuple[str, str, str]]]:
    values: dict[str, list[tuple[str, str, str]]] = {}
    for artifact in inventory.manifests:
        details = artifact.details
        if artifact.kind in {"pyproject", "setup-cfg"}:
            group, raw = "python", details.get("requiresPython")
        elif artifact.kind == "package-json":
            group, raw = "node", details.get("engines", {}).get("node")
        elif artifact.kind == "cargo-toml":
            group, raw = "rust", details.get("rustVersion")
        else:
            continue
        declared = _declared(raw)
        if declared:
            values.setdefault(group, []).append((artifact.path, *declared))
    for pin in inventory.language_pins:
        group = "python" if pin.path == ".python-version" else "node"
        value = _version(pin.details.get("value"))
        if value:
            values.setdefault(group, []).append((pin.path, value, "exact"))
    return values


def _disagree(declarations: list[tuple[str, str, str]]) -> bool:
    exact = {value for _, value, kind in declarations if kind == "exact"}
    floors = {value for _, value, kind in declarations if kind == "floor"}
    if len(exact) > 1 or len(floors) > 1:
        return True
    return bool(exact and floors) and _key(next(iter(exact))) < _key(next(iter(floors)))


def check_runtime_drift(inventory: ToolchainInventory) -> list[Finding]:
    findings = []
    for declarations in _runtime(inventory).values():
        if _disagree(declarations):
            path = declarations[-1][0]
            rendered = ", ".join(f"{source}={'>=' if kind == 'floor' else ''}{value}"
                                 for source, value, kind in declarations)
            findings.append(_finding("tc-runtime-drift", f"runtime declarations disagree: {rendered}", path))
    return findings


def _has_lock(inventory: ToolchainInventory, ecosystem: str) -> bool:
    manifest = _ECOSYSTEM_MANIFEST.get(ecosystem, "Cargo.toml")
    return any(_LOCK_MANIFEST.get(Path(item.path).name) == manifest for item in inventory.locks)


def _is_exact_pin(ecosystem: object, spec: str) -> bool:
    if re.fullmatch(r"\s*(?:==|=)\s*\d+(?:\.\d+){1,2}\s*", spec):
        return True
    return ecosystem == "node" and re.fullmatch(r"\s*\d+\.\d+\.\d+\s*", spec) is not None


def check_tool_unpinned(inventory: ToolchainInventory) -> list[Finding]:
    findings = []
    for artifact in inventory.manifests:
        ecosystem = artifact.details.get("ecosystem")
        specs = artifact.details.get("devDependencySpecs", {})
        if not isinstance(specs, dict) or not specs or _has_lock(inventory, ecosystem):
            continue
        unpinned = [name for name, spec in specs.items()
                    if not _is_exact_pin(ecosystem, str(spec))]
        if unpinned:
            findings.append(_finding("tc-tool-unpinned",
                                     f"development tools lack exact pins: {', '.join(sorted(unpinned))}",
                                     artifact.path))
    return findings


def check_build_backend(inventory: ToolchainInventory) -> list[Finding]:
    findings = []
    for artifact in inventory.manifests:
        if artifact.kind != "pyproject" or artifact.details.get("buildBackend"):
            continue
        if artifact.details.get("uvPackage") is False:
            continue
        findings.append(_finding("tc-build-backend-missing",
                                 "buildable pyproject.toml has no build backend", artifact.path))
    return findings


def check_key_unarbitered(inventory: ToolchainInventory) -> list[Finding]:
    findings = []
    for artifact in inventory.manifests:
        semantic = set(artifact.details.get("semanticKeys", []))
        declared = {item.get("key") for item in artifact.details.get("arbiterEntries", [])}
        for key in sorted(semantic & _ARBITER_KEYS - declared):
            findings.append(_finding("tc-key-unarbitered",
                                     f"shared configuration key `{key}` has no arbiter", artifact.path))
    return findings


def run_checks(inventory: ToolchainInventory) -> list[Finding]:
    return CHECK_REGISTRY.run(inventory)


CHECK_REGISTRY = register_checks(
    ("lock-stale", check_lock_stale),
    ("config-duplicate", check_config_duplicate),
    ("generated-stale", check_generated_stale),
    ("runtime-drift", check_runtime_drift),
    ("tool-unpinned", check_tool_unpinned),
    ("build-backend", check_build_backend),
    ("key-unarbitered", check_key_unarbitered),
)
