"""Aggregate the catalog answers of every owner, and the baseline level they reach.

Nothing here probes a control: each owner block is the same ``owner_controls`` the owning front
already embeds. ``present`` stays candidate evidence, never conformance.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from quenching.common.config import load_config
from quenching.controls.evaluate import load_catalog, owner_controls

TEMPLATES = Path(__file__).resolve().parents[3] / "opensource" / "templates"
OWNER = "opensource"
LEVELS = (1, 2, 3)

# Catalog control -> the finding code that names its missing artifact.
CONTROL_CODES = {
    "OSPS-LE-02": "os-license-missing",
    "OSPS-LE-03": "os-license-missing",
    "OSPS-GV-01": "os-governance-missing",
    "OSPS-GV-03": "os-contributing-missing",
    "OSPS-GV-04": "os-governance-missing",
    "COMMUNITY-CONDUCT": "os-conduct-missing",
    "COMMUNITY-PR-TEMPLATE": "os-templates-missing",
    "COMMUNITY-CITATION": "os-citation-missing",
    "COMMUNITY-FUNDING": "os-funding-missing",
    "REUSE-LICENSING": "os-reuse-missing",
}
# Artifacts this front writes that no catalog control names: advisory only.
ARTIFACT_CODES = {
    "support": "os-support-missing",
    "codeowners": "os-codeowners-missing",
    "issue-bug": "os-templates-missing",
    "issue-feature": "os-templates-missing",
}
LANGUAGE = "en"
# README and SECURITY keep their owners: this front reports the hand-off, never writes them.
LANGUAGE_HANDOFFS = (
    {"artifact": "readme", "owner": "knowledge", "command": "/quenching-knowledge align"},
    {"artifact": "security", "owner": "security", "command": "/quenching-security status"},
)
OWNER_COMMANDS = {
    "opensource": "/quenching-opensource align",
    "security": "/quenching-security status",
    "delivery": "/quenching-delivery align",
    "toolchain": "/quenching-toolchain align",
    "knowledge": "/quenching-knowledge align",
    "proof": "/quenching-proof align",
    "git": "/quenching-git branch",
}


# Showcase: what the forge shows of a public repository, measured over the tracked files.
_LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".css": "CSS",
    ".scss": "SCSS", ".less": "Less", ".html": "HTML", ".htm": "HTML", ".vue": "Vue",
    ".svelte": "Svelte", ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby",
    ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++",
    ".cs": "C#", ".kt": "Kotlin", ".swift": "Swift", ".sh": "Shell",
}
# Only a theme or a template set misleads the forge this way; a skew toward a programming language is real.
_ASSET_LANGUAGES = {"JavaScript", "CSS", "SCSS", "Less", "HTML"}
_UI_SUFFIXES = {".html", ".htm", ".jinja", ".jinja2", ".j2", ".vue", ".svelte", ".jsx", ".tsx"}
_VENDOR_SEGMENTS = {"static", "assets", "plugins", "vendors", "lib", "libs", "third_party", "external"}
_TEMPLATE_SEGMENTS = {"templates"}
# Paths the forge's language detector already discards by default.
_DEFAULT_VENDORED = re.compile(r"(^|/)(node_modules|vendor|bower_components)/|\.min\.(js|css)$")
_ATTRIBUTES = ("linguist-vendored", "linguist-generated", "linguist-documentation", "linguist-detectable")
_MEDIA = re.compile(r"!\[[^\]]*\]\(([^)]*)\)|<(img|video|picture)\b([^>]*)>", re.IGNORECASE)
_BADGE = re.compile(r"shields\.io|badge", re.IGNORECASE)


def _git(root: str, argv: list[str], data: str | None = None) -> str | None:
    try:
        run = subprocess.run(["git", "-C", root, *argv], input=data, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return run.stdout if run.returncode == 0 else None


def _tracked(root: str) -> list[str] | None:
    if not os.path.exists(os.path.join(root, ".git")):
        return None
    listing = _git(root, ["ls-files", "-z"])
    return None if listing is None else [path for path in listing.split("\0") if path]


def _excluded(root: str, paths: list[str]) -> set[str]:
    """Paths the declared linguist attributes take out of the forge's language count."""
    output = _git(root, ["check-attr", "-z", "--stdin", *_ATTRIBUTES], "\0".join(paths) + "\0")
    if output is None:
        return set()
    fields = output.split("\0")
    excluded = set()
    for index in range(0, len(fields) - 2, 3):
        path, attribute, value = fields[index:index + 3]
        if (attribute == "linguist-detectable" and value in ("unset", "false")) or (
                attribute != "linguist-detectable" and value in ("set", "true")):
            excluded.add(path)
    return excluded


def _prefix(path: str) -> tuple[str, str] | None:
    parts = path.split("/")[:-1]
    for index, segment in enumerate(parts):
        if segment in _VENDOR_SEGMENTS or segment in _TEMPLATE_SEGMENTS:
            kind = "template" if segment in _TEMPLATE_SEGMENTS else "vendored"
            return "/".join(parts[:index + 1]) + "/", kind
    return None


def _dominant(totals: dict[str, int]) -> str | None:
    return max(sorted(totals), key=lambda name: totals[name]) if totals else None


def _language_skew(root: str, paths: list[str]) -> dict[str, Any] | None:
    """The dominant language changes once the vendored and template directories leave the count."""
    excluded = _excluded(root, paths)
    sized: list[tuple[str, int, tuple[str, str] | None]] = []
    for path in paths:
        language = _LANGUAGES.get(os.path.splitext(path)[1].lower())
        if not language or path in excluded or _DEFAULT_VENDORED.search(path):
            continue
        try:
            sized.append((language, os.path.getsize(os.path.join(root, path)), _prefix(path)))
        except OSError:
            continue
    every: dict[str, int] = {}
    for language, size, _ in sized:
        every[language] = every.get(language, 0) + size
    language = _dominant(every)
    if language not in _ASSET_LANGUAGES:
        return None
    kinds = {prefix[0]: prefix[1] for name, _, prefix in sized if prefix and name == language}
    honest: dict[str, int] = {}
    for name, size, prefix in sized:
        if not (prefix and prefix[0] in kinds):
            honest[name] = honest.get(name, 0) + size
    real = _dominant(honest)
    if real is None or real == language:
        return None
    flagged = sorted(kinds)
    lines = [f"{prefix}** linguist-vendored" if kinds[prefix] == "vendored"
             else f"{prefix}** linguist-detectable=false" for prefix in flagged]
    return {"code": "os-language-skew", "severity": "info", "language": language, "honest": real,
            "paths": flagged, "gitattributes": lines,
            "action": "propose these .gitattributes lines to the owner; apply only with their authorization"}


def _readme_media(root: str, paths: list[str]) -> dict[str, Any] | None:
    if not any(os.path.splitext(path)[1].lower() in _UI_SUFFIXES for path in paths):
        return None
    readmes = sorted(path for path in paths if "/" not in path and path.upper().startswith("README"))
    if not readmes:
        return None
    try:
        text = Path(root, readmes[0]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if any(not _BADGE.search(match.group(0)) for match in _MEDIA.finditer(text)):
        return None
    return {"code": "os-readme-media-missing", "severity": "info", "path": readmes[0],
            "owner": "knowledge", "command": "/quenching-knowledge align",
            "action": ("ask the owner for a screenshot or GIF of the interface made with synthetic data, "
                       "store it under .design/assets/ and reference it from the README")}


def showcase_advisories(root: str) -> list[dict[str, Any]]:
    """The README image and the language statistic the forge shows of the repository."""
    paths = _tracked(root)
    if not paths:
        return []
    return [row for row in (_readme_media(root, paths), _language_skew(root, paths)) if row]


def load_sources(directory: Path = TEMPLATES) -> dict[str, Any]:
    return json.loads((directory / "sources.json").read_text(encoding="utf-8"))


def template_integrity(directory: Path = TEMPLATES) -> list[dict[str, Any]]:
    """Every pinned upstream template still hashes to the sha256 recorded when it was fetched."""
    drift = []
    for entry in load_sources(directory)["templates"]:
        if not entry.get("sha256"):
            continue
        path = directory / entry["path"]
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != entry["sha256"]:
            drift.append({"code": "os-template-drift", "severity": "error", "path": entry["path"],
                          "message": f"pinned template differs from {entry.get('url')}"})
    return drift


def applicability(root: str) -> dict[str, Any]:
    signals = sorted(name for name in os.listdir(root)
                     if name.upper().startswith(("LICENSE", "COPYING")))
    installed = (load_config(root, detect_provider_info=False).get("shared", {})
                 .get("profiles", {}) or {}).get("installed") or []
    if OWNER in installed:
        signals.append("shared.profiles.installed")
    return {"state": "applicable" if signals else "not-applicable", "signals": signals}


def _level(blocks: list[dict[str, Any]], optional: set[str]) -> dict[str, Any]:
    levels: dict[str, Any] = {}
    attained = 0
    climbing = True
    for level in LEVELS:
        states: dict[str, list[str]] = {"missing": [], "unknown": []}
        for block in blocks:
            for control in block["controls"]:
                if control["id"] in optional:
                    continue
                for item in control["requirements"]:
                    if item["state"] == "retired" or level not in item["levels"]:
                        continue
                    if item["state"] == "missing":
                        states["missing"].append(item["id"])
                    elif item["state"] in ("not-measured", "refused"):
                        states["unknown"].append(item["id"])
        state = "unmet" if states["missing"] else "unknown" if states["unknown"] else "met"
        levels[str(level)] = {"state": state, "blocking": states["missing"] + states["unknown"]}
        climbing = climbing and state == "met"
        if climbing:
            attained = level
    return {"level": attained, "levels": levels}


def _artifacts(root: str) -> list[dict[str, Any]]:
    rows, seen = [], set()
    for entry in load_sources()["templates"]:
        if entry["artifact"] in seen:
            continue
        seen.add(entry["artifact"])
        rows.append({"artifact": entry["artifact"], "target": entry["target"],
                     "language": entry["language"],
                     "present": os.path.isfile(os.path.join(root, entry["target"]))})
    return rows


def status(root: str) -> dict[str, Any]:
    catalog = load_catalog()
    blocks = [owner_controls(owner, root, catalog) for owner in catalog["owners"]]
    optional = {control["id"] for control in catalog["controls"] if control.get("optional")}
    findings, advisories = [], []
    for control in blocks[catalog["owners"].index(OWNER)]["controls"]:
        missing = [item["id"] for item in control["requirements"] if item["state"] == "missing"]
        if not missing:
            continue
        code = CONTROL_CODES.get(control["id"], f"os-{control['id'].lower()}-missing")
        row = {"code": code, "control": control["id"], "requirements": missing,
               "severity": "info" if control["optional"] else "error"}
        (advisories if control["optional"] else findings).append(row)
    artifacts = _artifacts(root)
    for row in artifacts:
        code = ARTIFACT_CODES.get(row["artifact"])
        if code and not row["present"]:
            advisories.append({"code": code, "artifact": row["artifact"], "severity": "info"})
    advisories.extend(showcase_advisories(root))
    return {
        "ok": not findings,
        "root": root,
        "readOnly": True,
        "baseline": {"version": catalog.get("baseline", {}).get("version"), **_level(blocks, optional)},
        "owners": [{**block, "command": OWNER_COMMANDS.get(block["owner"])} for block in blocks],
        "artifacts": artifacts,
        "artifactLanguage": {"language": LANGUAGE, "basis": "template-declaration",
                             "contentVerified": False, "handoffs": list(LANGUAGE_HANDOFFS)},
        "findings": findings,
        "advisories": advisories,
    }


def doctor(root: str) -> dict[str, Any]:
    payload = status(root)
    payload["applicability"] = applicability(root)
    payload["templates"] = template_integrity()
    if payload["applicability"]["state"] == "not-applicable":
        payload["findings"], payload["advisories"] = [], []
    payload["findings"] = payload["templates"] + payload["findings"]
    payload["ok"] = not payload["findings"]
    return payload
