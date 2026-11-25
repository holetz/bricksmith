"""The operations front's declared root, registry, and canonical router.

`opsRoot`, `router`, and the optional `registry` path live beside the specs settings in
`.agents/quenching.json`, but their meaning belongs to this front. The shared loader reads the
required declarations as data; this module is the one boundary that turns the paths into
repository-relative paths and refuses to guess when either required declaration is absent.
"""
from __future__ import annotations

import os

from quenching.common.config import CONFIG_FILE, find_repo_root, load_config, namespace


OPS_CONFIG_KEYS = ("opsRoot", "router")
REGISTRY_CONFIG_KEY = "registry"


def _declared_registry(ops_config: dict) -> str | None:
    """Read the optional registry override from the operations namespace."""
    value = ops_config.get(REGISTRY_CONFIG_KEY)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _repo_path(repo_root: str, declared: str) -> str:
    """Resolve one configured path without changing the declaration's spelling."""
    return os.path.abspath(os.path.join(repo_root, declared))


def _find_repo_root(root: str) -> str:
    """Compatibility export for callers that imported the old ops-local search."""
    return find_repo_root(root)


def load_ops_config(root: str) -> tuple[dict | None, dict]:
    """Return the resolved operations configuration, or the one refusal it can carry.

    The ordinary `load_config` result remains available to every specs caller.  This wrapper
    deliberately does not add a default for `opsRoot` or `router`: an operations tree and a
    router are target-repository decisions, and selecting `scripts/` or a guessed build entry
    point would make the verifier inspect the wrong surface while appearing healthy.
    """
    repo_root = _find_repo_root(root)
    cfg = load_config(repo_root, detect_provider_info=False)
    if cfg.get("migrationRefusal"):
        return None, cfg["migrationRefusal"]
    ops = namespace(cfg, "ops")
    missing = [key for key in OPS_CONFIG_KEYS if not ops.get(key)]
    if missing:
        missing_text = ", ".join(f"`{key}`" for key in missing)
        return None, {
            "code": "op-config-missing",
            "exit": 2,
            "config": CONFIG_FILE,
            "missing": missing,
            "message": f"{CONFIG_FILE} must declare both `opsRoot` and `router`; "
                       f"missing {missing_text} — the ops front refuses to guess its "
                       "operations tree or canonical router",
        }

    ops_root = _repo_path(repo_root, ops["opsRoot"])
    router = _repo_path(repo_root, ops["router"])
    declared_registry = _declared_registry(ops)
    registry = (_repo_path(repo_root, declared_registry) if declared_registry
                else os.path.join(ops_root, "README.md"))
    return {
        "config": cfg["path"],
        "repoRoot": repo_root,
        "opsRoot": ops_root,
        "router": router,
        "registry": registry,
        "declaredOpsRoot": ops["opsRoot"],
        "declaredRouter": ops["router"],
        "declaredRegistry": declared_registry,
    }, {}


def resolve_ops_config(root: str) -> tuple[dict | None, dict]:
    """Alias named for callers that need to make the resolution boundary explicit."""
    return load_ops_config(root)
