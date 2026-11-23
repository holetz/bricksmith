"""The version lockstep's mechanical half — the version artifacts and the changelog
entry check, followed by the two-pass
bump that moves all of them or none.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from quenching.common.git import _git
from quenching.common.io import read_text, write_text
from quenching.common.modules import distribution_findings, load_manifest, release_artifacts


# --------------------------------------------------------------------------- #
# release — the mechanical half of /docs/standards/ci-cd/versioning-release.md
# --------------------------------------------------------------------------- #
# Compatibility export for readers of the bundled contract. Real releases derive the
# target repository's manifest instead, so an installed tool cannot miss a new consumer.
RELEASE_ARTIFACTS = release_artifacts(json.loads(
    (Path(__file__).resolve().parents[3] / "modules.json").read_text(encoding="utf-8")))
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_RELEASE_JSON_VERSION_RE = re.compile(r'("version"\s*:\s*")([^"]+)(")')
_RELEASE_PY_VERSION_RE = re.compile(r'^(VERSION\s*=\s*["\'])([^"\']+)(["\'])', re.M)
_CHANGELOG_ENTRY_RE = re.compile(r"^##\s+([^\s#]+)\s*$", re.MULTILINE)


def repo_release_artifacts(repo_root: str) -> tuple[tuple[str, str], ...]:
    return release_artifacts(load_manifest(Path(repo_root)))


def bump_release_artifacts(repo_root: str, new_version: str) -> dict:
    """Require a changelog entry, then move every artifact or change NOTHING.

    The changelog gate and the first pass only READ: every artifact's current version is
    collected before anything is written, so a lockstep that is ALREADY drifted — one
    artifact disagreeing with the rest — is refused outright rather than compounded with
    a fifth value. The second pass writes only once every artifact was read
    successfully and every one of them agreed."""
    changelog = read_text(os.path.join(repo_root, "CHANGELOG.md"))
    if changelog is None or new_version not in _CHANGELOG_ENTRY_RE.findall(changelog):
        return {"ok": False,
                "error": f"CHANGELOG.md has no release entry: ## {new_version}",
                "artifacts": []}
    reads: list[dict] = []
    try:
        manifest = load_manifest(Path(repo_root))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "error": f"module manifest refused: {exc}", "artifacts": []}
    findings = distribution_findings(Path(repo_root), manifest)
    if findings:
        return {"ok": False, "error": "; ".join(findings), "artifacts": []}
    for rel, kind in release_artifacts(manifest):
        path = os.path.join(repo_root, rel)
        text = read_text(path)
        if text is None:
            return {"ok": False, "error": f"missing or unreadable: {rel}", "artifacts": []}
        if kind == "plain":
            old = text.strip()
        else:
            pat = _RELEASE_PY_VERSION_RE if kind == "py" else _RELEASE_JSON_VERSION_RE
            found = [m.group(2) for m in pat.finditer(text)]
            if not found:
                return {"ok": False, "error": f"no version found in: {rel}", "artifacts": []}
            if kind == "jsonall":
                try:
                    entries = json.loads(text).get("plugins", [])
                except ValueError:
                    return {"ok": False, "error": f"not valid JSON: {rel}", "artifacts": []}
                expected = {p["name"] for p in manifest.get("plugins", []) if p["marketplace"]}
                if (len(entries) != len(expected)
                        or {entry.get("name") for entry in entries} != expected):
                    return {"ok": False, "artifacts": [],
                            "error": f"{rel}: entries disagree with the marketplace plugins"}
                if len(found) != len(entries):
                    return {"ok": False, "artifacts": [],
                            "error": f"{rel}: {len(entries)} plugin entries but "
                                     f"{len(found)} versions — an entry has no version"}
                if len(set(found)) > 1:
                    return {"ok": False, "artifacts": [],
                            "error": f"{rel}: plugin entries disagree: {', '.join(found)}"}
            old = found[0]
        reads.append({"rel": rel, "kind": kind, "path": path, "text": text, "old": old})

    disagreeing = sorted({r["old"] for r in reads})
    if len(disagreeing) > 1:
        detail = ", ".join(f"{r['rel']}={r['old']}" for r in reads)
        return {"ok": False,
                "error": f"the lockstep already disagrees before this release: {detail}",
                "artifacts": []}
    old_version = disagreeing[0]
    if old_version == new_version:
        return {"ok": False, "error": f"already at {new_version}", "artifacts": []}

    artifacts = []
    for r in reads:
        if r["kind"] == "plain":
            new_text = new_version + ("\n" if r["text"].endswith("\n") else "")
        else:
            pat = _RELEASE_PY_VERSION_RE if r["kind"] == "py" else _RELEASE_JSON_VERSION_RE
            # every plugin entry of the marketplace, never just the first
            new_text = pat.sub(rf"\g<1>{new_version}\g<3>", r["text"],
                               count=0 if r["kind"] == "jsonall" else 1)
        write_text(r["path"], new_text)
        artifacts.append({"path": r["rel"], "old": r["old"], "new": new_version})
    return {"ok": True, "oldVersion": old_version, "newVersion": new_version,
            "artifacts": artifacts, "error": None}


_REGENERATE_STEPS = (
    # Last, so it copies sources already at the new version; only where a surface was deployed.
    ("deploy_surface", ("quenching/assets/bin/cq", "deploy", "--target", ".", "--write"),
     ("quenching/assets/bin/cq", ".agents/.quenching-deploy.json")),
)


def dirty_paths(repo: str) -> list[str]:
    """Paths `git status --porcelain` reports, untracked ones included."""
    paths = []
    for line in _git(repo, "status", "--porcelain", "--untracked-files=all").splitlines():
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path:
            paths.append(path)
    return paths


def regenerate_copies(repo: str) -> str | None:
    """Run each step whose guard paths all exist (by default its script); the failing step's
    name, else None."""
    for name, argv, *guard in _REGENERATE_STEPS:
        if not all(os.path.isfile(os.path.join(repo, p)) for p in (guard[0] if guard else argv[:1])):
            continue
        proc = subprocess.run([sys.executable, *argv], cwd=repo, capture_output=True, text=True)
        if proc.returncode != 0:
            return f"{name}: {(proc.stderr or proc.stdout).strip()}"
    return None
