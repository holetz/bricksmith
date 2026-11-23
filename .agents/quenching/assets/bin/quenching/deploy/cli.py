"""`cq deploy` — plan, check or write the quenching surface in a target repository."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from quenching.common.deploy import MANIFEST, read_manifest
from quenching.common.config import repository_document
from quenching.common.output import FINDINGS, OK, REFUSAL, NoAbbrevParser, emit, refuse
from quenching.common.version import VERSION
from quenching.deploy import mcpfiles
from quenching.deploy import settings as claude_settings
from quenching.deploy import tree as deploy_tree

LEGACY = (".claude/commands", ".claude/references", ".agents/.generated-from.json")
GENERATED_ROOTS = (".agents/quenching/", ".agents/skills/quenching-", ".claude/skills/quenching-",
                   ".claude/agents/quenching-")
MCP_MOUNTS = {"specs"}
SETTINGS = ".claude/settings.json"
# Owned files another cq tool rewrites on purpose: `cq components bridge` restamps its marker with
# its own generator line and hashes. They carry no human work, so a difference is never `edited`.
TOOL_REWRITTEN = {deploy_tree.CLAUDE_SKILLS + "/.generated-files.json"}
HOOKS_PATH = deploy_tree.PAYLOAD + "/assets/githooks"


def _mcp_refusal(message: str, **extra) -> dict:
    return {"code": "ct-deploy-mcp", "message": message, **extra,
            "remedy": "declare `shared.mcp` as {mount: \"specs\", command: [<python prefix>]} "
                      "to override the default, or remove the key"}


def _identity_hooks_warnings(target: Path) -> list[str]:
    """A declared email allowlist with the shipped hooks inactive in this clone. Deploy writes no
    git configuration: activating the hooks stays a per-machine choice."""
    document, _info, _refusal = repository_document(str(target))
    shared = (document or {}).get("shared")
    block = shared.get("gitIdentity") if isinstance(shared, dict) else None
    if not isinstance(block, dict) or "allowedEmails" not in block:
        return []
    done = subprocess.run(["git", "-C", str(target), "config", "--local", "core.hooksPath"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    current = done.stdout.strip().rstrip("/")
    if done.returncode not in (0, 1) or current == HOOKS_PATH:
        return []
    if current:
        return [f"shared.gitIdentity.allowedEmails is declared and core.hooksPath is {current}: "
                "call `.agents/quenching/bin/cq git identity --staged` (pre-commit) and "
                "`--pre-push --remote \"$1\"` (pre-push) from those hooks"]
    return ["shared.gitIdentity.allowedEmails is declared but the identity hooks are not active "
            f"in this clone: git config core.hooksPath {HOOKS_PATH}"]


def _mcp_option(target: Path) -> tuple[dict | None, dict | None]:
    """`shared.mcp` from the repository layer, validated before anything is written.

    Read through `repository_document`, never the merged envelope: `_apply_user_layer` would
    make the generated file vary by machine. Absent is absence, not an error.
    """
    document, _info, refusal = repository_document(str(target))
    if refusal:
        return None, refusal
    shared = (document or {}).get("shared")
    raw = shared.get("mcp") if isinstance(shared, dict) else None
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, _mcp_refusal("`shared.mcp` must be an object with `mount` and `command`")
    unknown = sorted(set(raw) - {"mount", "command"})
    if unknown:
        return None, _mcp_refusal(f"`shared.mcp` has unknown key(s): {', '.join(unknown)}",
                                  keys=unknown)
    mount = raw.get("mount", "specs")
    if mount not in MCP_MOUNTS:
        return None, _mcp_refusal(
            f"`shared.mcp.mount` must be one of: {', '.join(sorted(MCP_MOUNTS))}", mount=mount)
    command = raw.get("command")
    if (not isinstance(command, list) or not command
            or not all(isinstance(part, str) and part for part in command)):
        return None, _mcp_refusal(
            "`shared.mcp.command` must be a non-empty list of strings — the prefix that finds "
            "Python, e.g. [\"uv\", \"run\", \"--no-project\", \"python\"]; the runtime path and "
            "the mount are appended by deploy")
    return {"mount": mount, "command": list(command)}, None


def _split(values: list[str] | None) -> list[str]:
    out: list[str] = []
    for value in values or []:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def _change(change: str, rel: str) -> dict:
    return {"path": rel, "change": change}


def _owned(previous: dict | None) -> set[str]:
    return set(_recorded_hashes(previous))


def _recorded_hashes(previous: dict | None) -> dict[str, str]:
    """What the last deploy owned whole. The MCP harness files are the user's and merged, so a
    manifest that still lists them (written before the merge) no longer owns them."""
    files = (previous or {}).get("files")
    return {rel: digest for rel, digest in files.items()
            if isinstance(digest, str) and rel not in mcpfiles.FILES} \
        if isinstance(files, dict) else {}


def _mcp_recorded(previous: dict | None) -> list[str]:
    """The harness files whose quenching entry the last deploy wrote."""
    listed = (previous or {}).get("mcpFiles")
    if isinstance(listed, list):
        return [rel for rel in listed if rel in mcpfiles.FILES]
    files = (previous or {}).get("files")
    return [rel for rel in mcpfiles.FILES if isinstance(files, dict) and rel in files]


def _canonical_text(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_bytes().decode("utf-8", errors="replace").replace("\r\n", "\n")


def _mcp_merge(target: Path, wanted: dict[str, list[str]], previous: dict | None) -> dict:
    """The quenching entry written into (or taken out of) the user's harness files, unwritten.

    `wanted` maps each file to the argv its entry carries; a file the last deploy wrote and that is
    no longer wanted loses only that entry. Nothing is touched that cannot be read safely.
    """
    files: list[dict] = []
    warnings: list[str] = []
    for rel in sorted({*wanted, *_mcp_recorded(previous)}):
        path = target / rel
        if not (_safe_relative(rel) and _contained(target, path)) or (path.exists() and not path.is_file()):
            warnings.append(f"{rel} is not a regular file inside the target; its MCP entry was not touched")
            continue
        current = _canonical_text(path)
        if rel in wanted:
            new = mcpfiles.merged(rel, current, wanted[rel])
        elif current is not None and mcpfiles.NAME in current:
            new = mcpfiles.removed(rel, current)
        else:
            continue
        if new is None:
            warnings.append(f"{rel} is not a JSON object this deploy can merge; its MCP entry was "
                            "not touched — add the quenching server by hand")
            continue
        if new != current:
            files.append({"rel": rel, "text": new, "delete": new == ""})
    return {"files": files, "warnings": warnings}


def _edited(target: Path, rel: str, recorded: str | None) -> bool:
    """The owned file no longer holds the bytes the last deploy wrote: someone edited it.

    Without a recorded hash (a manifest older than the field) the edit is unknowable, so the
    answer is no — the file is treated as the last deploy's, exactly as before.
    """
    if recorded is None or rel in TOOL_REWRITTEN:
        return False
    data = (target / rel).read_bytes()
    # The raw hash still matches a manifest a Windows deploy recorded before the tree was canonical.
    return recorded not in {hashlib.sha256(data).hexdigest(),
                            hashlib.sha256(deploy_tree.eol_canonical(data)).hexdigest()}


def _same(path: Path, data: bytes) -> bool:
    """The file holds the generated bytes, a CRLF work-tree copy of them included."""
    disk = path.read_bytes()
    return disk == data or deploy_tree.eol_canonical(disk) == data


def _mode_drift(path: Path, mode: int) -> bool:
    """The executable bit differs. Windows derives the bit from the extension, so it carries none."""
    return os.name != "nt" and bool(path.stat().st_mode & 0o111) != bool(mode & 0o111)


def _legacy_refusal(target: Path) -> dict | None:
    for rel in LEGACY:
        if (target / rel).exists():
            return {
                "code": "ct-deploy-legacy",
                "path": rel.removeprefix(".agents/").removeprefix(".claude/"),
                "message": f"{target / rel} is the legacy .claude surface; deploy will not mix "
                           "the two layouts",
                "remedy": "run `cq components migrate --target . --write`, then rerun `cq deploy`",
            }
    return None


def _seed_plan(target: Path, seeds: dict[str, str]) -> tuple[list[dict], list[str]]:
    changes, warnings = [], []
    legacy_config = (target / ".claude/quenching.json").exists()
    for rel in seeds:
        if rel == ".agents/quenching.json" and legacy_config:
            continue
        if not (target / rel).exists():
            changes.append(_change("seed", rel))
    if legacy_config:
        warnings.append("legacy .claude/quenching.json is still read; run `cq components migrate` "
                        "before seeding .agents/quenching.json")
    if (target / "CLAUDE.md").is_file():
        text = (target / "CLAUDE.md").read_text(encoding="utf-8").strip()
        if text == "@AGENTS.md":
            warnings.append("legacy pointer-only CLAUDE.md is present; rerun with --prune-legacy")
        else:
            warnings.append("CLAUDE.md carries its own instructions and shadows AGENTS.md; "
                            "consolidate it with the `quenching-components-harness-align` skill, then remove it")
    return changes, warnings


def recorded(previous: dict | None, key: str, allowed: set[str] | None = None):
    """One recorded selection, or None when absent or malformed.

    The manifest makes `cq deploy --write` an upgrade instead of a fresh `core` install: on a
    target that deployed `--modules all`, the defaults would otherwise build a core tree and
    delete every other module's owned file. An explicit flag still wins; this is only the default.
    """
    value = (previous or {}).get(key)
    if allowed is not None:
        return value if value in allowed else None
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return list(value)
    return None


def plan(target: Path, built: dict, previous: dict | None) -> dict:
    tree, seeds = built["tree"], built["seeds"]
    hashes = _recorded_hashes(previous)
    owned = set(hashes)
    changes: list[dict] = []
    conflicts: list[dict] = []
    for rel, (data, _mode_) in sorted(tree.items()):
        path = target / rel
        if not _contained(target, path):
            conflicts.append({"path": rel, "why": "a symlinked directory resolves it outside "
                                                  "the target"})
        elif not path.exists():
            changes.append(_change("create", rel))
        elif not path.is_file():
            conflicts.append({"path": rel, "why": "a directory occupies a generated file's path"})
        elif rel not in owned:
            if not _same(path, data):
                conflicts.append({"path": rel, "why": "exists but no deploy manifest owns it"})
            elif _mode_drift(path, _mode_):
                changes.append(_change("mode", rel))
            else:
                # Already the generated bytes (a bridge written between deploys): claim it.
                changes.append(_change("adopt", rel))
        elif not _same(path, data):
            # Three-way: the bytes on disk, the bytes the last deploy recorded, the new bytes. A
            # file still holding the recorded bytes is an upstream change; anything else is an
            # edit made in the target, which a write would silently discard.
            changes.append(_change("edited" if _edited(target, rel, hashes.get(rel)) else "update", rel))
        elif _mode_drift(path, _mode_):
            # The executable bit is the only part of the mode git carries and a caller can
            # observe; a checkout under umask 0002 creates every file 0o664, which is not drift.
            changes.append(_change("mode", rel))
    for rel in sorted(owned - set(tree)):
        if (target / rel).is_file():
            changes.append(_change("edited" if _edited(target, rel, hashes.get(rel)) else "delete",
                                   rel))
    seed_changes, warnings = _seed_plan(target, seeds)
    changes.extend(seed_changes)
    merged = built.get("merged")
    if merged:
        if merged["changed"]:
            changes.append(_change("settings", SETTINGS))
        warnings = [*warnings, *merged["warnings"]]
    mcp = built.get("mcpMerge")
    if mcp:
        changes.extend(_change("mcp", item["rel"]) for item in mcp["files"])
        warnings = [*warnings, *mcp["warnings"]]
    if (target / MANIFEST).exists():
        try:
            current = json.loads((target / MANIFEST).read_text(encoding="utf-8"))
        except ValueError:
            current = None
        if current != built["manifest"]:
            changes.append(_change("manifest", MANIFEST))
    else:
        changes.append(_change("create", MANIFEST))
    return {"changes": changes, "conflicts": conflicts, "warnings": warnings}


def _settings_merge(target: Path, built: dict, previous: dict | None) -> dict | None:
    """Merge quenching's own hooks into the user's `.claude/settings.json` without writing.

    Returns None when there is nothing to do, else `{"text": <new text or None>, "delete": bool,
    "warnings": [...]}`; raises `SettingsRefusal` on a file that cannot be merged safely. With no
    Claude harness the wanted set is empty, so only entries a past deploy wrote leave.
    """
    wanted = built.get("settings")
    recorded = (previous or {}).get("claudeSettings")
    recorded = recorded if isinstance(recorded, dict) else None
    if wanted is None and recorded is None:
        return None
    path = target / SETTINGS
    if path.exists() and not path.is_file():
        raise claude_settings.SettingsRefusal("the path is not a regular file")
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    keys = recorded.get("attribution") if recorded else []
    keys = keys if isinstance(keys, list) else []
    merged = claude_settings.merge(current, wanted["hooks"] if wanted else [],
                                   wanted["attribution"] if wanted else None, keys)
    if wanted is not None:
        built["manifest"]["claudeSettings"]["attribution"] = sorted(merged.attribution)
    delete = (wanted is None and current is not None and recorded is not None
              and json.loads(merged.text if merged.text is not None else current) == {})
    return {"text": merged.text, "delete": delete, "warnings": merged.warnings,
            "changed": delete or (merged.text is not None and merged.text != current)}


def _safe_relative(rel: str) -> bool:
    path = Path(rel)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in rel


def _contained(target: Path, path: Path) -> bool:
    """The path resolves inside the target: a symlinked directory cannot carry a write out."""
    root = os.path.realpath(target)
    parent = os.path.realpath(path.parent)
    return parent == root or parent.startswith(root + os.sep)


def _unlink_owned(target: Path, rel: str) -> None:
    path = target / rel
    if not (path.is_file() and _safe_relative(rel) and _contained(target, path)):
        return
    path.unlink()
    parent = path.parent
    while parent != target and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def _write_mcp(target: Path, merge: dict | None) -> None:
    for item in (merge or {}).get("files", []):
        path = target / item["rel"]
        if item["delete"]:
            _unlink_owned(target, item["rel"])
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item["text"].encode("utf-8"))


def write(target: Path, built: dict, result: dict, prune_legacy: bool) -> None:
    tree, seeds = built["tree"], built["seeds"]
    previous = read_manifest(str(target)) or {}
    owned = _owned(previous)
    for rel in sorted(owned - set(tree)):
        _unlink_owned(target, rel)
    for rel, (data, mode) in sorted(tree.items()):
        if not _safe_relative(rel):
            raise ValueError(f"unsafe deploy path: {rel}")
        path = target / rel
        if not _contained(target, path):
            raise ValueError(f"deploy path leaves the target through a symlink: {rel}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or not _same(path, data):
            path.write_bytes(data)
        os.chmod(path, mode)
    legacy_config = (target / ".claude/quenching.json").exists()
    for rel, text in seeds.items():
        if rel == ".agents/quenching.json" and legacy_config:
            continue
        path = target / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    merged = built.get("merged")
    if merged and merged["changed"]:
        path = target / SETTINGS
        if merged["delete"]:
            path.unlink()
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(merged["text"], encoding="utf-8")
    _write_mcp(target, built.get("mcpMerge"))
    if prune_legacy:
        pointer = target / "CLAUDE.md"
        if pointer.is_file() and pointer.read_text(encoding="utf-8").strip() == "@AGENTS.md":
            pointer.unlink()
    (target / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (target / MANIFEST).write_text(json.dumps(built["manifest"], indent=2) + "\n",
                                   encoding="utf-8")


def _human(target: Path, built: dict, result: dict, written: bool) -> str:
    manifest = built["manifest"]
    lines = [f"cq deploy — {target}",
             f"  version: {manifest['version']}  modules: {', '.join(manifest['modules'])}"
             f"  runtime: {manifest['runtime']}  harnesses: {', '.join(manifest['harnesses'])}",
             f"  changes: {len(result['changes'])}  conflicts: {len(result['conflicts'])}"
             + ("  (written)" if written else "")]
    for change in result["changes"]:
        lines.append(f"  {change['change']:<8} {change['path']}")
    for conflict in result["conflicts"]:
        lines.append(f"  conflict: {conflict['path']} — {conflict['why']}")
    for warning in result.get("warnings", []):
        lines.append(f"  warning: {warning}")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    p = NoAbbrevParser(prog="cq deploy",
                       description="install the quenching surface in a target repository")
    p.add_argument("--target", "--root", dest="target", default=".",
                   help="target repository root (default: current directory)")
    p.add_argument("--modules", action="append", default=None,
                   help="modules to deploy: core, all, or a comma list (default: core)")
    p.add_argument("--profiles", action="append", default=None,
                   help="shared.profiles.installed, comma separated (default: derived)")
    p.add_argument("--harness", action="append", default=None,
                   help="claude, codex, opencode, none (default: claude,codex,opencode)")
    p.add_argument("--runtime", choices=("vendor", "uvx"), default=None,
                   help="vendored runtime (offline) or a pinned uvx shim; defaults to the "
                        "manifest's recorded runtime, then vendor")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true", help="write the plan")
    mode.add_argument("--check", action="store_true", help="exit 1 on drift")
    p.add_argument("--force", action="store_true",
                   help="with --write or --remove: replace or delete files edited in the target")
    p.add_argument("--remove", action="store_true",
                   help="plan removing the whole deployed surface; with --write, remove it")
    p.add_argument("--prune-legacy", action="store_true",
                   help="remove a pointer-only CLAUDE.md (content-bearing files are never touched)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--version", action="version", version=VERSION)
    return p


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    target = Path(os.path.abspath(args.target))
    if not target.is_dir():
        return refuse({"code": "ct-deploy-target", "target": str(target),
                       "message": "the target is not a directory"}, args.json)
    refusal = _legacy_refusal(target)
    if refusal:
        return refuse(refusal, args.json)
    previous = read_manifest(str(target))
    if args.remove:
        return _remove_main(target, previous, args)
    harnesses = (_split(args.harness) or recorded(previous, "harnesses")
                 or ["claude", "codex", "opencode"])
    if "none" in harnesses:
        harnesses = [h for h in harnesses if h != "none"]
    for harness in harnesses:
        if harness not in {"claude", "codex", "opencode"}:
            return refuse({"code": "ct-deploy-harness", "harness": harness,
                           "message": f"unknown harness: {harness}"}, args.json)
    runtime = args.runtime or recorded(previous, "runtime", {"vendor", "uvx"}) or "vendor"
    declared, refusal = _mcp_option(target)
    if refusal:
        return refuse(refusal, args.json)
    if declared and runtime != "vendor":
        return refuse(_mcp_refusal(
            "`shared.mcp` needs `--runtime vendor`: the generated command points at "
            "`.agents/quenching/assets/bin/cq`, which a uvx runtime does not carry"), args.json)
    recorded_mcp = (previous or {}).get("mcp")
    python = mcpfiles.python_prefix(
        declared["command"] if declared else None,
        recorded_mcp.get("command") if isinstance(recorded_mcp, dict) else None)
    # Registered by default; `shared.mcp` only overrides the mount and the prefix. A uvx runtime
    # carries no vendored entry point to call, so there the registration is skipped, not refused.
    mcp = ({"mount": declared["mount"] if declared else "specs", "command": python}
           if runtime == "vendor" else None)
    mcp_warnings = ([] if mcp or not harnesses else [
        "the MCP server is not registered: `--runtime uvx` carries no vendored entry point; "
        "use `--runtime vendor` to register it"])
    try:
        manifest = deploy_tree.load_modules(deploy_tree.source_root())
        modules = deploy_tree.normalize_modules(
            _split(args.modules) or recorded(previous, "modules"), manifest)
        profiles = (_split(args.profiles) or recorded(previous, "profiles")
                    or deploy_tree.profiles_for(modules))
        built = deploy_tree.build_tree(deploy_tree.source_root(), target, modules, harnesses,
                                       runtime, profiles, mcp=mcp, python=python)
    except (OSError, ValueError) as exc:
        return refuse({"code": "ct-deploy-source", "message": str(exc),
                       "remedy": "run deploy from a complete quenching checkout or wheel"},
                      args.json)
    try:
        built["merged"] = _settings_merge(target, built, previous)
    except claude_settings.SettingsRefusal as exc:
        return refuse({"code": "ct-deploy-settings", "path": SETTINGS,
                       "message": f"{target / SETTINGS} cannot be merged: {exc}; nothing was written",
                       "remedy": "fix the file by hand, or remove it, then rerun `cq deploy`"},
                      args.json)
    wanted = {rel: deploy_tree.mcp_argv(mcp) for rel in built["manifest"].get("mcpFiles", [])} \
        if mcp else {}
    built["mcpMerge"] = _mcp_merge(target, wanted, previous)
    result = plan(target, built, previous)
    result["warnings"] = [*result.get("warnings", []), *mcp_warnings,
                          *_identity_hooks_warnings(target)]
    installed = (previous or {}).get("version")
    if isinstance(installed, str) and installed != built["manifest"]["version"]:
        result["warnings"] = [f"the target carries quenching {installed}; this source is "
                              f"{built['manifest']['version']}", *result["warnings"]]
    edited = [change["path"] for change in result["changes"] if change["change"] == "edited"]
    if args.write and edited and not args.force and not result["conflicts"]:
        return refuse({"code": "ct-deploy-edited", "paths": edited,
                       "message": "the target edited files the last deploy wrote; a write would "
                                  "discard those edits, so nothing was written",
                       "remedy": "move each edit to a target-owned skill or into the quenching "
                                 "source, or revert it; rerun with --force to discard the edits"},
                      args.json)
    if args.write and result["conflicts"]:
        return refuse({"code": "ct-deploy-unowned", "conflicts": result["conflicts"],
                       "message": "the target holds files no deploy manifest owns; nothing was written",
                       "remedy": "move or rename the listed files, or remove them after review"},
                      args.json)
    written = False
    if args.write:
        write(target, built, result, args.prune_legacy)
        written = True
    payload = {"ok": not result["conflicts"], "target": str(target),
               "version": built["manifest"]["version"],
               "installedVersion": installed if isinstance(installed, str) else None,
               "modules": modules,
               "profiles": profiles, "harnesses": harnesses, "runtime": runtime,
               "changes": result["changes"], "conflicts": result["conflicts"],
               "warnings": result.get("warnings", []), "written": written}
    emit(args.json, payload, _human(target, built, result, written))
    if result["conflicts"]:
        return REFUSAL
    if written:
        return OK
    return FINDINGS if result["changes"] else OK


def remove_plan(target: Path, previous: dict) -> dict:
    """What `--remove` deletes and what it keeps, without writing.

    An owned file still holding the recorded bytes leaves; an edited one is kept and reported, so
    removing the surface never discards work done in the target. Seeds (`AGENTS.md`,
    `.agents/quenching.json`) and every target-owned file are never owned, so never listed.
    """
    hashes = _recorded_hashes(previous)
    changes: list[dict] = []
    for rel in sorted(hashes):
        path = target / rel
        if path.is_file() and _safe_relative(rel):
            changes.append(_change("edited" if _edited(target, rel, hashes[rel]) else "delete", rel))
    merged = _settings_merge(target, {"settings": None, "manifest": {}}, previous)
    if merged and merged["changed"]:
        changes.append(_change("settings", SETTINGS))
    mcp = _mcp_merge(target, {}, previous)
    changes.extend(_change("mcp", item["rel"]) for item in mcp["files"])
    changes.append(_change("delete", MANIFEST))
    return {"changes": changes, "merged": merged, "mcp": mcp}


def _remove_main(target: Path, previous: dict | None, args) -> int:
    if previous is None:
        return refuse({"code": "ct-deploy-remove", "target": str(target),
                       "message": "the target carries no deploy manifest: nothing is owned, so "
                                  "nothing is removed",
                       "remedy": "a surface installed before the manifest existed is removed by "
                                 "reverting the commit that installed it"}, args.json)
    try:
        result = remove_plan(target, previous)
    except claude_settings.SettingsRefusal as exc:
        return refuse({"code": "ct-deploy-settings", "path": SETTINGS,
                       "message": f"{target / SETTINGS} cannot be merged: {exc}; nothing was removed",
                       "remedy": "fix the file by hand, or remove it, then rerun `cq deploy --remove`"},
                      args.json)
    kept = [c["path"] for c in result["changes"] if c["change"] == "edited" and not args.force]
    written = False
    if args.write:
        for change in result["changes"]:
            if (change["path"] in (MANIFEST, SETTINGS) or change["path"] in kept
                    or change["change"] == "mcp"):
                continue
            _unlink_owned(target, change["path"])
        merged = result["merged"]
        if merged and merged["changed"]:
            path = target / SETTINGS
            if merged["delete"]:
                path.unlink()
            elif merged["text"] is not None:
                path.write_text(merged["text"], encoding="utf-8")
        _write_mcp(target, result["mcp"])
        (target / MANIFEST).unlink()
        written = True
    warnings = [f"kept {rel}: edited in the target, so it is now the target's own file"
                for rel in kept] + result["mcp"]["warnings"]
    payload = {"ok": True, "target": str(target), "remove": True,
               "installedVersion": previous.get("version"), "changes": result["changes"],
               "kept": kept, "warnings": warnings, "written": written}
    lines = [f"cq deploy --remove — {target}",
             f"  changes: {len(result['changes'])}  kept: {len(kept)}"
             + ("  (removed)" if written else "")]
    lines += [f"  {c['change']:<8} {c['path']}" for c in result["changes"]]
    lines += [f"  warning: {w}" for w in warnings]
    emit(args.json, payload, "\n".join(lines))
    if written:
        return OK
    return FINDINGS
