"""The seed of `.agents/quenching.json` (or the legacy `.claude/quenching.json` it finds): every adjustable default spelled out.

The document is built from the owning modules' own constants, so it cannot drift from the code.
Two defaults live where the `quenching-specs` module cannot import them (`knowledge` is not in
that tree; the chat model is a literal in `specs/portal.py`); they are restated here and pinned to
their owners by `test_config_seed.py`. A default of `None` (`workHost.target`, `dockerContext`,
`runner`) is left out: the loader warns on a null, so those keys stay optional.
"""
from __future__ import annotations

import copy

from quenching.codex.config import DEFAULTS as CODEX
from quenching.common.config import detect_profile, find_repo_root, repository_document
from quenching.config.autonomy import DEFAULTS as AUTONOMY
from quenching.config.host import DEFAULTS as WORK_HOST
from quenching.session.commands.context import BUDGETS
from quenching.specs.chat import CHAT_TIMEOUT_SECONDS, DEFAULT_BUDGET_USD

OKF_VALIDATE = {"warnAsError": False, "ignoreGlobs": []}
CHAT = {"model": "sonnet", "budgetUsd": DEFAULT_BUDGET_USD, "timeoutSeconds": CHAT_TIMEOUT_SECONDS}


def seed_document(root: str) -> dict:
    """The full seed for the repository at `root`."""
    profile = detect_profile(find_repo_root(root), "git")
    document = {
        "backend": "git",
        "shared": {
            "autonomy": AUTONOMY,
            "codex": CODEX,
            "contextBudgets": BUDGETS,
            "okfValidate": OKF_VALIDATE,
            "profiles": {"installed": profile},
            "workHost": {k: v for k, v in WORK_HOST.items() if v is not None},
        },
        "specs": {"chat": CHAT, "estimate": {"writeOnly": []}},
    }
    return copy.deepcopy(document)


def flatten(value, prefix: str = "") -> dict:
    """Dotted leaves of a nested document; lists and scalars are leaves."""
    if not isinstance(value, dict):
        return {prefix: value}
    out: dict = {}
    for key, sub in value.items():
        out.update(flatten(sub, f"{prefix}.{key}" if prefix else key))
    return out


def missing_keys(document: dict, root: str) -> list[str]:
    """Seed leaves that `document` (a parsed `quenching.json`) does not declare, sorted."""
    declared = flatten(document)
    return sorted(key for key in flatten(seed_document(root)) if key not in declared)


def read_repository(root: str) -> dict | None:
    """The repository document through the active provider, `None` when absent or not a JSON object."""
    return repository_document(root)[0]


def set_leaf(document: dict, key: str, value) -> object:
    """Set `key` (dotted) in `document` in place, creating the blocks on its path; every other
    key keeps its place. Returns the previous value (None when absent)."""
    parts = key.split(".")
    node = document
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    previous = node.get(parts[-1])
    node[parts[-1]] = value
    return previous


def unset_leaf(document: dict, key: str) -> tuple[bool, object]:
    """Remove `key` from `document` in place, then every block it leaves empty.
    `(removed, previous)`."""
    parts = key.split(".")
    trail, node = [], document
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            return False, None
        trail.append((node, part))
        node = node[part]
    if parts[-1] not in node:
        return False, None
    previous = node.pop(parts[-1])
    for parent, part in reversed(trail):
        if parent[part]:
            break
        del parent[part]
    return True, previous
