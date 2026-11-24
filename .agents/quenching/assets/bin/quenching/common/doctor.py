"""The top-level environment doctor.

`cq doctor` is the short pre-flight check for a first session.  It owns only prerequisites
shared by more than one front: the Python floor, provider CLI and the documentation runner.
Front-specific health remains with each front's own `doctor` command.

The profile is a conduction scope, not a second dependency declaration.  An absent profile has
the documented meaning "all local fronts are eligible"; a declared profile narrows that set.
The provider is read from the repository remote first, with a valid declared backend used only
when no remote provider is available.  This keeps the check useful before the repository has
completed its provider setup while never making a GitHub repository look like Azure.
"""
from __future__ import annotations

import glob
import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

from quenching.common.cache import cache_root
from quenching.common.git import COMMAND_TIMEOUT_S, _git
from quenching.common.config import (active_provider, detect_profile, load_config,
                                     prepare_minimal_config,
                                     write_minimal_config)
from quenching.common.gitcache import untracked_cache_line, untracked_cache_report
from quenching.common.io import read_text
from quenching.common.output import OK, emit, refuse


PYTHON_FLOOR = (3, 11)
LOCAL_PROFILES = (
    "knowledge", "design", "components", "ops", "proof", "toolchain", "delivery", "opensource",
)
BACKEND_TO_TOOL = {"github": "gh", "azure-boards": "az"}
TOOL_ORDER = ("gh", "az", "uv", "zensical")


def _version_info() -> tuple[int, int, str]:
    major, minor = sys.version_info[:2]
    return major, minor, f"{major}.{minor}.{sys.version_info[2]}"


def _profile(config: dict) -> dict:
    """Return the effective local profile and preserve whether it was declared."""
    data = config.get("data")
    shared = data.get("shared") if isinstance(data, dict) else None
    declaration = shared.get("profiles") if isinstance(shared, dict) else None
    installed = declaration.get("installed") if isinstance(declaration, dict) else None
    valid = (isinstance(installed, list)
             and all(isinstance(name, str) and name.strip() for name in installed))
    if valid:
        names = [name.strip() for name in installed]
        return {"declared": True, "installed": names, "effective": names, "valid": True}
    # An invalid declaration is not silently used as a partial profile.  The owning front's
    # doctor will name its malformed shape; this pre-flight check retains the safe documented
    # default so a bad profile cannot make a required dependency disappear.
    return {
        "declared": declaration is not None,
        "installed": None,
        "effective": list(LOCAL_PROFILES),
        "valid": declaration is None,
    }


def _backend(config: dict) -> tuple[str | None, str]:
    data = config.get("data")
    declared = data.get("backend") if isinstance(data, dict) else None
    provider = config.get("provider")
    if provider in BACKEND_TO_TOOL:
        return provider, "repository remote"
    if declared in BACKEND_TO_TOOL:
        return declared, "declared backend"
    return None, "not configured"


def _uses_uv(root: str) -> bool:
    path = Path(root)
    if (path / "uv.lock").is_file():
        return True
    text = read_text(str(path / "pyproject.toml")) or ""
    return "[tool.uv]" in text


def required_tools(root: str, config: dict | None = None) -> tuple[str | None, dict, list[dict]]:
    """Resolve the external tools that the selected backend/profile can actually use."""
    config = config or load_config(root)
    backend, _backend_source = _backend(config)
    profile = _profile(config)
    reasons: dict[str, str] = {}
    if backend in BACKEND_TO_TOOL:
        reasons[BACKEND_TO_TOOL[backend]] = f"backend `{backend}`"
    effective = (set(profile["effective"]) if profile["declared"]
                 else set(detect_profile(root, backend)))
    if "knowledge" in effective:
        reasons["zensical"] = "profile `knowledge`"
        if _uses_uv(root):
            reasons["uv"] = "profile `knowledge` and the target declares uv"

    tools = []
    for name in TOOL_ORDER:
        if name in reasons:
            tools.append({"name": name, "reason": reasons[name]})
    return backend, profile, tools


def inspect_environment(root: str, *, which: Callable[[str], str | None] | None = None,
                        config: dict | None = None) -> dict:
    """Collect a side-effect-free environment report."""
    root = os.path.abspath(root)
    # `which` finds `az.cmd` on Windows; the actual spawn resolves that launcher past
    # `cmd.exe` (`common.tools.spawn_argv`), so both agree on whether `az` is available.
    which = which or shutil.which
    major, minor, version = _version_info()
    backend, profile, requirements = required_tools(root, config)
    for item in requirements:
        item["found"] = which(item["name"])
        item["available"] = bool(item["found"])
    missing = [item["name"] for item in requirements if not item["available"]]
    return {
        "root": root,
        "python": {
            "version": version,
            "required": f">={PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]}",
            "available": (major, minor) >= PYTHON_FLOOR,
        },
        "backend": backend,
        "profile": profile,
        "requirements": requirements,
        "missing": missing,
    }


def _missing_payload(report: dict) -> dict:
    missing = report["missing"]
    names = ", ".join(f"`{name}`" for name in missing)
    first = missing[0]
    return {
        "code": f"cq-{first}-missing",
        "root": report["root"],
        "python": report["python"],
        "backend": report["backend"],
        "profile": report["profile"],
        "requirements": report["requirements"],
        "missing": missing,
        "message": f"required dependency {names} is not on PATH",
        "remedy": f"install {names}, then run `cq doctor` again before starting work",
    }


def config_report(root: str) -> dict | None:
    """`{present, missing}` for the seeded keys, or `None` where this module ships no seed.

    Reports only: a missing key is information, never a failed prerequisite.
    """
    # By name, not by statement: only the modules that ship the `config` package can seed, and
    # the others must report nothing instead of failing the pre-flight check.
    try:
        seed = importlib.import_module("quenching.config.seed")
    except ImportError:
        return None
    missing_keys, read_repository = seed.missing_keys, seed.read_repository
    document = read_repository(root)
    if document is None:
        provider, refusal = active_provider()
        report = {"present": False, "missing": []}
        if refusal:
            report["refusal"] = refusal["message"]
        elif provider.name != "file":
            report["provider"] = provider.name
        return report
    return {"present": True, "missing": missing_keys(document, root)}


def _config_line(config: dict | None) -> str | None:
    if config is None:
        return None
    if config.get("refusal"):
        return f"config: {config['refusal']}"
    if not config["present"] and config.get("provider") not in (None, "file"):
        return f"config: served by the `{config['provider']}` provider — no document returned"
    if not config["present"]:
        return "config: no .agents/quenching.json — `cq config init --write` seeds it"
    if config["missing"]:
        return (f"config: {len(config['missing'])} seeded key(s) missing "
                f"({', '.join(config['missing'][:3])}{', …' if len(config['missing']) > 3 else ''})"
                " — `cq config check` lists them")
    return "config: every seeded key is declared"


def _run_setup(as_json: bool, root: str, write: bool) -> int:
    result, error = (write_minimal_config(root) if write else prepare_minimal_config(root))
    if error:
        return refuse(error, as_json)
    assert result is not None
    action = "written" if write else "preview"
    human = (f"cq doctor setup — {result['path']}\n"
             f"  backend: {result['backend']}\n"
             f"  profile: {', '.join(result['profile']) or '(none)'}\n"
             f"  {action}: {'yes' if write else 'no — rerun with `--setup --write` to confirm'}")
    payload = {"ok": True, "action": f"setup-{action}", **result}
    emit(as_json, payload, human)
    return OK


ANTIVIRUS_CAVEAT = (
    "Nothing here is applied: excluding a path from the antivirus is a security decision for "
    "you or your IT. Under Tamper Protection or a managed policy (Intune, Trend Micro Apex One) "
    "the change is refused locally; hand the plain list to whoever owns the central console.")
UV_TIMEOUT_S = 10


def _owns(candidate: str, common: str) -> bool:
    """`candidate` is a checkout of `common`: its `.git` is that git dir, or a `gitdir:` file to it."""
    dot_git = os.path.join(candidate, ".git")
    if os.path.isfile(dot_git):
        head = read_text(dot_git).strip()
        if not head.startswith("gitdir:"):
            return False
        target = os.path.join(candidate, head[len("gitdir:"):].strip())
        return _canonical(target) == _canonical(common)
    if not os.path.isdir(dot_git) or _canonical(dot_git) != _canonical(common):
        return False
    # A bare `.git` parent (`--separate-git-dir`) carries the git dir but none of its files: a
    # checkout is proven by one tracked file the index recorded there (its inode, size and mtime,
    # so uncommitted edits elsewhere do not matter), or by every tracked file carrying the index's
    # content (a clean checkout whose stat is stale). `diff` refreshes the index it reads, so
    # both read a throwaway copy and the real index is never written.
    index = os.path.join(common, "index")
    if not os.path.isfile(index):
        return False
    try:
        with tempfile.TemporaryDirectory() as scratch:
            env = {**os.environ, "GIT_INDEX_FILE": shutil.copyfile(
                index, os.path.join(scratch, "index"))}
            listed = subprocess.run(["git", "--git-dir", common, "ls-files", "-z", "--debug"],
                                    capture_output=True, cwd=candidate, timeout=COMMAND_TIMEOUT_S,
                                    env=env)
            if listed.returncode != 0 or not listed.stdout:
                return False
            if _stat_matches(candidate, listed.stdout):
                return True
            argv = ["git", "--git-dir", common, "--work-tree", candidate, "diff", "--quiet",
                    "--no-ext-diff", "--no-textconv", "--ignore-submodules"]
            return subprocess.run(argv, capture_output=True, cwd=candidate,
                                  timeout=COMMAND_TIMEOUT_S, env=env).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


_INDEX_ENTRY = re.compile(
    rb"(?:\A|\n)([^\0\n]+)\0  ctime: [^\n]*\n  mtime: (\d+):(\d+)\n  dev: \d+\tino: (\d+)\n"
    rb"  uid: [^\n]*\n  size: (\d+)\t")


def _stat_matches(candidate: str, listing: bytes) -> bool:
    """One entry of `ls-files -z --debug` is the file in `candidate`: same inode, size and mtime.

    An inode of 0 (not recorded, as on Git for Windows) proves nothing: a same-named copy could
    match size and mtime."""
    mask = 0xFFFFFFFF
    for match in _INDEX_ENTRY.finditer(listing):
        rel, sec, nsec, ino, size = match.groups()
        if not int(ino):
            continue
        try:
            st = os.lstat(os.path.join(candidate, os.fsdecode(rel)))
        except OSError:
            continue
        if ((st.st_ino & mask) == int(ino) and (st.st_size & mask) == int(size)
                and (st.st_mtime_ns // 10**9) & mask == int(sec)
                and (not int(nsec) or st.st_mtime_ns % 10**9 == int(nsec))):
            return True
    return False


def _primary_root(root: str) -> tuple[str | None, bool]:
    """The primary worktree, proven: never the parent of a git dir only by its name.

    In the primary worktree it is the toplevel; in a slot it is the first candidate that owns the
    shared git dir — the sibling without `-slot-<n>`, then the parent of a `.git` common dir —
    else the slot's own toplevel, narrower and never broader, with `False` beside it: the
    fallback is no primary. `(None, False)` outside a git repository: the raw root is never
    offered as the repository."""
    top = _git(root, "rev-parse", "--show-toplevel").strip()
    if not top:
        return None, False
    top = os.path.normpath(top)
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    git_dir = _git(root, "rev-parse", "--absolute-git-dir").strip()
    if not common or _canonical(git_dir) == _canonical(common):
        return top, True
    candidates = []
    stem = re.sub(r"-slot-\d+$", "", os.path.basename(top))
    if stem != os.path.basename(top):
        candidates.append(os.path.join(os.path.dirname(top), stem))
    if os.path.basename(common) == ".git":
        candidates.append(os.path.dirname(common))
    for candidate in candidates:
        if _owns(candidate, common):
            return os.path.normpath(candidate), True
    return top, False


def _canonical(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _too_broad(path: str) -> bool:
    """A disk root, the home or an ancestor of it, or the system temp or an ancestor of it."""
    target = _canonical(path)
    if os.path.dirname(target) == target:
        return True
    for anchor in (os.path.expanduser("~"), tempfile.gettempdir()):
        anchor = _canonical(anchor)
        if target == anchor or anchor.startswith(target.rstrip(os.sep) + os.sep):
            return True
    return False


def _uv_cache_dir(which: Callable[[str], str | None]) -> str | None:
    uv = which("uv")
    if not uv:
        return None
    try:
        out = subprocess.run([uv, "cache", "dir"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=UV_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    path = out.stdout.strip()
    return path if out.returncode == 0 and path else None


def antivirus_report(root: str, *, which: Callable[[str], str | None] | None = None) -> dict:
    """The paths and processes an on-access scanner should spare for this repository.

    Read-only: it resolves paths and runs `uv cache dir` when `uv` is on PATH, nothing else."""
    which = which or shutil.which
    primary, proven = _primary_root(os.path.abspath(root))
    if primary is None:
        return {"refusal": {
            "code": "cq-antivirus-no-repo",
            "root": os.path.abspath(root),
            "message": f"not inside a git repository: {os.path.abspath(root)}",
            "remedy": "run `cq doctor --antivirus` inside the repository (rode dentro do repositório)",
        }}
    if _too_broad(primary):
        return {"refusal": {
            "code": "cq-antivirus-unsafe-root",
            "root": primary,
            "message": f"the repository root is too broad to exclude: {primary} is a disk root, "
                       "the home, the system temp or an ancestor of one",
            "remedy": "keep the repository in a folder of its own, then run `cq doctor --antivirus` there",
        }}
    if proven:
        parent, name = os.path.split(primary)
        paths = [{"path": primary, "what": "repository (its .git holds the spec store)"}]
        pattern = os.path.join(parent, f"{name}-slot-*")
        paths.append({"path": pattern, "what": "worktree slots (wildcard)"})
        paths += [{"path": slot, "what": "worktree slot"}
                  for slot in sorted(glob.glob(pattern)) if os.path.isdir(slot)]
    else:
        paths = [{"path": primary, "what": "worktree (its primary worktree could not be proven)"}]
    paths.append({"path": cache_root(), "what": "quenching cache"})
    venv = os.path.join(primary, ".venv")
    if os.path.isdir(venv):
        paths.append({"path": venv, "what": "virtual environment"})
    uv_cache = _uv_cache_dir(which)
    if uv_cache:
        paths.append({"path": uv_cache, "what": "uv cache"})
    processes = [{"path": os.path.abspath(sys.executable), "what": "python"}]
    git = which("git")
    if git:
        processes.append({"path": os.path.abspath(git), "what": "git"})
    informational = [{
        "path": tempfile.gettempdir(),
        "what": "system temp — not printed as an exclusion: a common malware drop location"}]
    informational += [{"path": p["path"], "what": f"{p['what']} — too broad to exclude"}
                      for p in paths if _too_broad(p["path"])]
    common = _git(primary, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    if common and not _canonical(common).startswith(_canonical(primary).rstrip(os.sep) + os.sep):
        informational.append({"path": common, "what": "git dir outside the worktree — not listed: "
                                                      "its folder is not the repository"})
    return {
        "root": primary,
        "paths": [p for p in paths if not _too_broad(p["path"])],
        "processes": processes,
        "informational": informational,
        "caveat": ANTIVIRUS_CAVEAT,
    }


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _antivirus_human(report: dict) -> str:
    lines = [f"cq doctor --antivirus — {report['root']}", "",
             "Microsoft Defender (an elevated PowerShell):"]
    lines += [f"  Add-MpPreference -ExclusionPath {_ps_quote(p['path'])}" for p in report["paths"]]
    lines += [f"  Add-MpPreference -ExclusionProcess {_ps_quote(p['path'])}"
              for p in report["processes"]]
    lines += ["", "Plain list (a central console such as Trend Micro):"]
    lines += [f"  path     {p['path']}  ({p['what']})" for p in report["paths"]]
    lines += [f"  process  {p['path']}  ({p['what']})" for p in report["processes"]]
    lines += ["", "Not listed:"]
    lines += [f"  {p['path']}  ({p['what']})" for p in report["informational"]]
    lines += ["", report["caveat"]]
    return "\n".join(lines)


def run_antivirus(as_json: bool, root: str) -> int:
    report = antivirus_report(root)
    if "refusal" in report:
        return refuse(report["refusal"], as_json)
    emit(as_json, {"ok": True, **report}, _antivirus_human(report))
    return OK


def run(as_json: bool, root: str, *, setup: bool = False, write: bool = False,
        antivirus: bool = False) -> int:
    """Render the top-level doctor and preserve the CQ refusal door for missing prerequisites."""
    resolved_root = os.path.abspath(root)
    if not os.path.isdir(resolved_root):
        return refuse({
            "code": "cq-root-missing",
            "root": resolved_root,
            "message": f"target root does not exist: {resolved_root}",
            "remedy": "pass `--root` a directory that contains the target repository",
        }, as_json)
    if setup:
        return _run_setup(as_json, resolved_root, write)
    if antivirus:
        if write:
            return refuse({
                "code": "cq-setup-write-without-setup",
                "message": "`--write` is valid only with `--setup`; `--antivirus` writes nothing",
                "remedy": "run `cq doctor --antivirus` and apply what it prints yourself",
            }, as_json)
        return run_antivirus(as_json, resolved_root)
    if write:
        return refuse({
            "code": "cq-setup-write-without-setup",
            "message": "`--write` is valid only with `--setup`",
            "remedy": "run `cq doctor --setup --write` when you intend to create the config",
        }, as_json)
    report = inspect_environment(resolved_root)
    if not report["python"]["available"]:
        return refuse({
            "code": "cq-python-floor",
            "root": report["root"],
            "python": report["python"],
            "message": (f"Python {PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]} or newer is required; "
                         f"found {report['python']['version']}"),
            "remedy": "install or select a supported Python runtime before starting work",
        }, as_json)
    if report["missing"]:
        return refuse(_missing_payload(report), as_json)

    tools = ", ".join(f"{item['name']} ({item['found']})" for item in report["requirements"])
    profile = report["profile"]
    profile_text = ", ".join(profile["effective"]) or "(none)"
    human = (f"cq doctor — {report['root']}\n"
             f"  Python: {report['python']['version']} (requires {report['python']['required']})\n"
             f"  backend: {report['backend'] or '(none)'}\n"
             f"  profile: {profile_text}\n"
             f"  dependencies: {tools or '(none required)'}")
    config = config_report(resolved_root)
    if config is not None:
        report = {**report, "config": config}
        human += f"\n  {_config_line(config)}"
    git_cache = untracked_cache_report(resolved_root)
    if git_cache is not None:
        report = {**report, "gitCache": git_cache}
        line = untracked_cache_line(git_cache)
        human += f"\n  {line}" if line else ""
    emit(as_json, {"ok": True, **report}, human)
    return OK
