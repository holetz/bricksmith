"""The effective configuration: every key with its value and the layer it came from.

Precedence, lowest to highest: `default` < `user` < `repository` < `branch`, with the `local`
layer (`.agents/quenching.local.json`) placed per key by its registry scope: a `machine` key's
local value wins over every file, an `either` key's only fills a key the repository leaves out,
and a `project` key's is ignored and named in `warnings`. The user and the
repository files are read raw, never through the merged envelope, because the merged result no
longer knows which file a key came from. The `branch` layer is the `quenching` branch's
`quenching.json`; it only counts when the repository declares `backend: git`, and only for
`BRANCH_CONFIG_KEYS`, exactly as `specs.config` reads it.
"""
from __future__ import annotations

from quenching.codex.config import DEFAULTS as CODEX_DEFAULTS
from quenching.common.config import (NAMESPACES, _load_local_layer, _load_user_layer,
                                     find_repo_root, repository_document)
from quenching.config.autonomy import DEFAULTS as AUTONOMY_DEFAULTS, coerce as coerce_autonomy
from quenching.config.host import DEFAULTS as WORK_HOST_DEFAULTS
from quenching.config.schema import SLOTS, local_decision, registry_leaves
from quenching.session.commands.context import BUDGETS
from quenching.session.cachebar import DEFAULTS as CACHE_BAR

# Namespace keys whose defaults live in code as data: the key is shown leaf by leaf, because the
# owning front completes a partial block leaf by leaf.
DEFAULT_BLOCKS = {("shared", "codex"): CODEX_DEFAULTS, ("shared", "autonomy"): AUTONOMY_DEFAULTS,
                  ("shared", "worktreeSlots"): SLOTS, ("shared", "workHost"): WORK_HOST_DEFAULTS,
                  ("shared", "contextBudgets"): BUDGETS, ("shared", "cache"): CACHE_BAR}


def _read_repository(root: str) -> tuple[dict, dict]:
    document, found, refusal = repository_document(root)
    info = {"name": "repository", "path": found["path"], "present": found["present"],
            "unparseable": found["unparseable"], "provider": found["name"]}
    if refusal:
        info["refusal"] = refusal["message"]
    return document or {}, info


def _branch_layer(root: str, repository: dict) -> tuple[dict, dict]:
    info = {"name": "branch", "path": "quenching:quenching.json", "present": False,
            "unparseable": None, "applies": repository.get("backend") == "git"}
    if not info["applies"]:
        return {}, info
    from quenching.specs.backends.git import branch_config   # deferred: git imports parse
    from quenching.specs.config import BRANCH_CONFIG_KEYS
    values = branch_config(root)
    kept = {key: values[key] for key in BRANCH_CONFIG_KEYS if key in values}
    info["present"] = bool(values)
    return kept, info


def _leaves(prefix: str, value, defaults: dict):
    """Flatten a block onto the default tree's leaves."""
    for name, default in defaults.items():
        sub = value.get(name) if isinstance(value, dict) else None
        if isinstance(default, dict):
            yield from _leaves(f"{prefix}.{name}", sub, default)
        else:
            yield f"{prefix}.{name}", sub, default


def effective(root: str) -> dict:
    """The layers read and one row per effective key, sorted by key."""
    repository, repo_info = _read_repository(root)
    user = _load_user_layer()
    user_info = {"name": "user", "path": user["path"], "present": user["present"],
                 "unparseable": user["unparseable"]}
    branch, branch_info = _branch_layer(root, repository)

    declared: dict[str, tuple[object, str]] = {}   # "ns.key" -> (value, layer)
    for layer, source in (("user", user["namespaces"]), ("repository", repository)):
        for name in NAMESPACES:
            block = source.get(name)
            if isinstance(block, dict):
                for key, value in block.items():
                    declared[f"{name}.{key}"] = (value, layer)
    if "backend" in repository:
        declared["backend"] = (repository["backend"], "repository")
    for key, value in branch.items():
        declared[f"specs.{key}"] = (value, "branch")

    rows: dict[str, dict] = {}
    warnings: list[str] = []
    for key, (value, layer) in declared.items():
        rows[key] = {"key": key, "value": value, "source": layer}
    for (name, key), defaults in DEFAULT_BLOCKS.items():
        base = f"{name}.{key}"
        block, layer = declared.pop(base, (None, "default"))
        rows.pop(base, None)
        if (name, key) == ("shared", "autonomy"):
            checked, found = coerce_autonomy(block)
            warnings.extend(found)
            if isinstance(block, dict):
                block = {leaf: given for leaf, given in block.items() if checked.get(leaf) == given}
        for leaf, given, default in _leaves(base, block, defaults):
            rows[leaf] = ({"key": leaf, "value": given, "source": layer} if given is not None
                          else {"key": leaf, "value": default, "source": "default"})
    local = _load_local_layer(find_repo_root(root))
    local_info = {"name": "local", "path": local["path"], "present": local["present"],
                  "unparseable": local["unparseable"]}
    for key, value in registry_leaves(local["namespaces"]):
        declared_here = rows.get(key, {}).get("source") in ("repository", "branch")
        ok, why = local_decision(key, declared_here)
        if ok:
            rows[key] = {"key": key, "value": value, "source": "local"}
        elif why:
            warnings.append(f"local layer: {why} (ignored)")
    return {"layers": [{"name": "default", "path": None, "present": True, "unparseable": None},
                       user_info, repo_info, local_info, branch_info],
            "values": [rows[key] for key in sorted(rows)], "warnings": warnings}
