"""Whether git's untracked cache is on, for `cq doctor` to recommend on Windows.

Every `git status` of a slot (`cq git worktree release` opens ~1.7k files through it) walks the
whole checkout for untracked files unless `core.untrackedCache` is on; under a real-time antivirus
each of those opens is scanned. Git for Windows already enables `core.fscache`, not this one.

Report only: nothing here writes the user's git config. The doctor prints the command to run.
"""
from __future__ import annotations

import os
import subprocess

COMMAND = "git config core.untrackedCache true"


def _get(root: str, key: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", root, "config", "--get", key],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip().lower() if done.returncode == 0 else None


def untracked_cache_report(root: str, *, platform: str | None = None) -> dict | None:
    """`{enabled, command}` on Windows inside a git work tree; `None` anywhere else."""
    if (platform or os.name) != "nt":
        return None
    try:
        inside = subprocess.run(["git", "-C", root, "rev-parse", "--is-inside-work-tree"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None
    value = _get(root, "core.untrackedCache")
    # `feature.manyFiles` turns the cache on unless the key itself says otherwise.
    enabled = value == "true" or (value is None and _get(root, "feature.manyFiles") == "true")
    return {"enabled": enabled, "command": None if enabled else COMMAND}


def untracked_cache_line(report: dict | None) -> str | None:
    if report is None or report["enabled"]:
        return None
    return (f"git: core.untrackedCache is off — every `git status` walks the whole checkout; "
            f"run `{report['command']}` (not applied)")
