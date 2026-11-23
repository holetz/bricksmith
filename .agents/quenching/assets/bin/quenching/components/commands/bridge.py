"""`cq components bridge` — the Claude Code copy of a target's generic skills.

A target repository keeps its skills in `.agents/skills/<name>/`, the one editable source. Claude
Code 2.1.312 does not discover `.agents/skills` (its own importer copies that tree into
`.claude/skills/`), so this verb generates `.claude/skills/<name>/` as a marked copy: every file of
the skill folder byte for byte, executable bit included, with one marker line after the `SKILL.md`
frontmatter. The manifest `.claude/skills/.generated-files.json` records one sha256 per generated
file and the Claude Code version the bridge was verified against; freshness is a byte comparison
against it.

A `.claude/skills/<name>/` the manifest does not list is the human's, and is never overwritten.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from quenching.common.output import FINDINGS, OK, emit, refuse

SOURCE = Path(".agents") / "skills"
DESTINATION = Path(".claude") / "skills"
MANIFEST = DESTINATION / ".generated-files.json"
SKILL_FILE = "SKILL.md"
VERIFIED_CLAUDE_CODE = "2.1.312"


def marker(name: str) -> str:
    return (f"<!-- generated from .agents/skills/{name}/SKILL.md by cq components bridge; "
            "edit the source -->")


def with_marker(text: str, name: str) -> str:
    """Insert the marker right after the frontmatter, or at the top when there is none."""
    nl = "\r\n" if text.startswith("---\r\n") else "\n"
    line = marker(name) + nl
    fence = nl + "---" + nl
    if text.startswith("---" + nl):
        if text.endswith(nl + "---"):
            text += nl
        end = text.find(fence, 3)
        if end >= 0:
            cut = end + len(fence)
            return text[:cut] + line + text[cut:]
    return line + text


def expected_tree(repo: Path) -> dict[str, bytes]:
    """`<name>/<file>` -> the bytes the bridge writes, for every skill of the source."""
    out: dict[str, bytes] = {}
    source = repo / SOURCE
    if not source.is_dir():
        return out
    for skill in sorted(source.iterdir()):
        if not (skill / SKILL_FILE).is_file():
            continue
        for path in sorted(p for p in skill.rglob("*") if p.is_file()):
            rel = path.relative_to(source).as_posix()
            data = path.read_bytes()
            if path.name == SKILL_FILE and path.parent == skill:
                data = with_marker(data.decode("utf-8"), skill.name).encode("utf-8")
            out[rel] = data
    return out


def read_manifest(repo: Path) -> dict[str, str]:
    path = repo / MANIFEST
    if not path.is_file():
        return {}
    try:
        files = json.loads(path.read_text(encoding="utf-8")).get("files", {})
    except (ValueError, AttributeError):
        return {}
    return files if isinstance(files, dict) else {}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mode(path: Path) -> int:
    """The executable bit is part of the copy: a skill script must stay runnable."""
    return path.stat().st_mode & 0o111


def plan(repo: Path) -> dict:
    expected = expected_tree(repo)
    recorded = read_manifest(repo)
    owned = {rel.split("/", 1)[0] for rel in recorded}
    destination = repo / DESTINATION
    conflicts = sorted({rel.split("/", 1)[0] for rel in expected
                        if rel.split("/", 1)[0] not in owned
                        and (destination / rel.split("/", 1)[0]).exists()})
    changes = []
    for rel, data in sorted(expected.items()):
        path = destination / rel
        if not path.is_file():
            changes.append({"path": rel, "change": "create"})
        elif path.read_bytes() != data or _mode(path) != _mode(repo / SOURCE / rel):
            change = "edited" if recorded.get(rel) and sha256(path.read_bytes()) != recorded[rel] \
                else "update"
            changes.append({"path": rel, "change": change})
    for rel in sorted(set(recorded) - set(expected)):
        if (destination / rel).is_file():
            changes.append({"path": rel, "change": "orphan"})
    for skill in sorted(owned):
        folder = destination / skill
        if folder.is_dir():
            changes += [{"path": rel, "change": "orphan"}
                        for rel in sorted(p.relative_to(destination).as_posix()
                                          for p in folder.rglob("*") if p.is_file())
                        if rel not in recorded and rel not in expected]
    return {"expected": expected, "changes": changes, "conflicts": conflicts}


def write(repo: Path, result: dict) -> None:
    destination = repo / DESTINATION
    for change in result["changes"]:
        path = destination / change["path"]
        if change["change"] == "orphan":
            path.unlink()
            parent = path.parent
            while parent != destination and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result["expected"][change["path"]])
        os.chmod(path, 0o755 if _mode(repo / SOURCE / change["path"]) else 0o644)
    manifest = {
        "generator": "cq components bridge",
        "source": SOURCE.as_posix(),
        "verifiedClaudeCode": VERIFIED_CLAUDE_CODE,
        "files": {rel: sha256(data) for rel, data in sorted(result["expected"].items())},
    }
    if result["expected"] or (repo / MANIFEST).exists():
        destination.mkdir(parents=True, exist_ok=True)
        (repo / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def add_arguments(sp) -> None:
    sp.add_argument("--target", default=".", help="the repository whose .agents/skills is bridged")
    mode = sp.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write the Claude Code copy")
    mode.add_argument("--check", action="store_true", help="exit 1 when the copy drifted")
    sp.add_argument("--json", action="store_true", help="machine-readable output")


def cmd_bridge(args, _root: str) -> int:
    repo = Path(os.path.abspath(args.target))
    result = plan(repo)
    if result["conflicts"]:
        return refuse({"code": "ct-bridge-unowned",
                       "conflicts": result["conflicts"],
                       "message": "these .claude/skills/<name>/ folders were not generated by "
                                  "the bridge and would be overwritten: "
                                  + ", ".join(result["conflicts"]),
                       "remedy": "move each into .agents/skills/<name>/ (`cq components migrate`) "
                                 "or rename it, then rerun"}, args.json)
    if args.write:
        write(repo, result)
    pending = [] if args.write else result["changes"]
    payload = {"ok": not (args.check and pending), "target": str(repo),
               "skills": len({rel.split("/", 1)[0] for rel in result["expected"]}),
               "verifiedClaudeCode": VERIFIED_CLAUDE_CODE,
               "changes": result["changes"], "written": bool(args.write)}
    lines = [f"components bridge — {repo}",
             f"  skills: {payload['skills']}  changes: {len(result['changes'])}"
             + ("  (written)" if args.write else "")]
    lines += [f"  {c['change']}: {c['path']}" for c in result["changes"]]
    emit(args.json, payload, "\n".join(lines))
    return FINDINGS if args.check and pending else OK
