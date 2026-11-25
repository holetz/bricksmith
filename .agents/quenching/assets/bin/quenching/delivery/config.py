"""Read the delivery namespace without choosing provider or pipeline policy."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from quenching.common.config import CONFIG_FILE, config_path, load_config


def load_delivery_config(root: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the optional delivery declaration or its exact refusal."""
    repo_root = Path(root).resolve()
    path = Path(config_path(str(repo_root)))
    if not path.is_file():
        return {}, {}

    envelope = load_config(str(repo_root), detect_provider_info=False)
    if envelope["unparseable"]:
        return {}, {
            "code": "delivery-config-invalid",
            "exit": 2,
            "config": str(path),
            "message": f"{CONFIG_FILE} is not valid JSON: {envelope['unparseable']}",
        }
    if envelope["migrationRefusal"]:
        return {}, envelope["migrationRefusal"]
    if "delivery" not in envelope["data"]:
        return {}, {}
    if "delivery" in envelope["invalidNamespaces"]:
        return {}, {
            "code": "delivery-config-invalid",
            "exit": 2,
            "config": str(path),
            "message": f"{CONFIG_FILE} namespace `delivery` must be an object",
        }
    declaration = envelope["delivery"]
    if not isinstance(declaration, dict):
        return {}, {
            "code": "delivery-config-invalid",
            "exit": 2,
            "config": str(path),
            "message": f"{CONFIG_FILE} namespace `delivery` must be an object",
        }
    return {"path": str(path), "declaration": declaration}, {}


def configured_provider(config: dict[str, Any]) -> str | None:
    """Return a declared provider only when the target explicitly supplies one."""
    declaration = config.get("declaration")
    if not isinstance(declaration, dict):
        return None
    provider = declaration.get("provider")
    return provider.strip() if isinstance(provider, str) and provider.strip() else None
