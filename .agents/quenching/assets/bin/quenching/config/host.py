"""The `shared.workHost` block: the user's remote host for heavy processing, and its dispatch plan.

The block is optional: absent, the host is disabled and nothing changes. A value of the wrong
shape falls back to its default and is named in `warnings`. The plan is text: this module never
opens a connection, so the `cq` holds no SSH grant.
"""
from __future__ import annotations

import shlex

from quenching.common.config import load_config, namespace

DEFAULTS = {
    "target": None,
    "dockerContext": None,
    "runner": None,
    "remoteRoot": "~/work",
    "syncFlags": ["-az", "--checksum"],
    "syncExclude": [".git", "node_modules", ".venv", "target"],
    "pullBack": [],
    "protectedPaths": [],
    "minFreeGb": 10,
}
_TEXT = ("target", "dockerContext", "runner", "remoteRoot")
_LISTS = ("syncFlags", "syncExclude", "pullBack", "protectedPaths")


def load_settings(root: str) -> tuple[dict, list[str]]:
    """Return the effective settings (with the derived `enabled`) and the warnings."""
    block = namespace(load_config(root, detect_provider_info=False), "shared").get("workHost")
    settings = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS.items()}
    warnings: list[str] = []
    if block is not None and not isinstance(block, dict):
        warnings.append("shared.workHost is not an object; defaults apply")
        block = None
    for key, value in (block or {}).items():
        if key in _TEXT:
            if isinstance(value, str) and value.strip():
                settings[key] = value.strip()
            else:
                warnings.append(f"shared.workHost.{key} must be a non-empty string; "
                                f"{DEFAULTS[key]!r} applies")
        elif key in _LISTS:
            if isinstance(value, list) and all(isinstance(i, str) and i for i in value):
                settings[key] = list(value)
            else:
                warnings.append(f"shared.workHost.{key} must be a list of strings; "
                                f"{DEFAULTS[key]!r} applies")
        elif key == "minFreeGb":
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                settings[key] = value
            else:
                warnings.append("shared.workHost.minFreeGb must be a non-negative integer; "
                                f"{DEFAULTS[key]} applies")
        else:
            warnings.append(f"shared.workHost.{key} is not a known key; ignored")
    settings["enabled"] = settings["target"] is not None
    return settings, warnings


def build_plan(settings: dict, project: str, command: list[str]) -> tuple[list[dict], list[str]]:
    """The dispatch steps for `command`, as argv lists. Raises ValueError when it must be refused."""
    if not settings["enabled"]:
        raise ValueError("shared.workHost.target is not set; the work host is disabled")
    if not command:
        raise ValueError("no command to dispatch")
    joined = " ".join(command)
    for path in settings["protectedPaths"]:
        if path in joined:
            raise ValueError(f"the command touches a protected path: {path}")
    target = settings["target"]
    remote = f"{settings['remoteRoot'].rstrip('/')}/{project}"
    notes: list[str] = []
    if not settings["pullBack"]:
        notes.append("pullBack is empty: generated files will not come back")
    push = ["rsync", *settings["syncFlags"]]
    push += [f"--exclude={e}" for e in settings["syncExclude"]]
    push += ["./", f"{target}:{remote}/"]
    if settings["runner"]:
        run = [settings["runner"], *command]
    else:
        run = ["ssh", target, f"cd {shlex.quote(remote)} && {shlex.join(command)}"]
    steps = [
        {"step": "disk", "argv": ["ssh", target,
                                   f"test $(df -BG --output=avail {shlex.quote(remote.rsplit('/', 1)[0])}"
                                   f" | tail -1 | tr -dc 0-9) -ge {settings['minFreeGb']}"]},
        {"step": "push", "argv": push},
        {"step": "run", "argv": run},
    ]
    for pattern in settings["pullBack"]:
        steps.append({"step": "pull", "argv": ["rsync", *settings["syncFlags"],
                                               f"{target}:{remote}/{pattern}", "./"]})
    return steps, notes
