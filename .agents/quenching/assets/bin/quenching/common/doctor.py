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

import importlib
import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from quenching.common.config import (active_provider, detect_profile, load_config,
                                     prepare_minimal_config,
                                     write_minimal_config)
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


def run(as_json: bool, root: str, *, setup: bool = False, write: bool = False) -> int:
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
    emit(as_json, {"ok": True, **report}, human)
    return OK
