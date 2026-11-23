"""Where the spec store lives (the repository's to declare) and where its credential comes
from (the person's to name).

The repository layer, `.agents/quenching.json`, may say WHICH organisation, project or
repository: `specs.store.azure.{organization, project}`, `specs.store.github.repository`. It may
never carry anything that authenticates. The user layer, `~/.config/quenching/config.json`, may
say WHERE the credential comes from, `specs.credential.<azure|github>`, and never which store.

The secret travels only in the environment of the one `az`/`gh` subprocess that needs it. It is
not placed in argv, a refusal, a cache or a file; nothing here writes anywhere."""
from __future__ import annotations

import os
import re

from quenching.common.config import CONFIG_FILE, _load_user_layer, repository_document
from quenching.specs.backends.base import BackendRefusal

STORE_KEYS = {"azure": ("organization", "project"), "github": ("repository",)}
SOURCES = ("login", "env")
# tool -> (variable that holds the token, variable that points at the CLI's own config directory)
TOOL_ENV = {"azure": ("AZURE_DEVOPS_EXT_PAT", "AZURE_CONFIG_DIR"),
            "github": ("GH_TOKEN", "GH_CONFIG_DIR")}
_SECRET_KEY = re.compile(r"token|pat|passw|secret|key|credential", re.IGNORECASE)
_ORG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
_VAR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _refusal(code: str, message: str, **extra) -> dict:
    return {"code": code, "exit": 2, "message": message, **extra}


def _repository_specs(cwd: str) -> dict:
    """The repository file's `specs` block, read raw: the merged envelope no longer knows which
    layer a key came from, and that is exactly what a secret check needs to know."""
    obj, _info, _refusal = repository_document(cwd)
    block = obj.get("specs") if isinstance(obj, dict) else None
    return block if isinstance(block, dict) else {}


def _secret_paths(value, prefix: str):
    if isinstance(value, dict):
        for key, sub in value.items():
            path = f"{prefix}.{key}"
            if _SECRET_KEY.search(str(key)):
                yield path
            yield from _secret_paths(sub, path)


def declared_store(cwd: str) -> tuple[dict, dict]:
    """`({"azure": {organization, project}, "github": {repository}}, refusal)`: only the
    tools the repository declares are present, and nothing declared is `({}, {})`."""
    specs = _repository_specs(cwd)
    if "credential" in specs:
        return {}, _refusal(
            "sp-store-secret",
            f"{CONFIG_FILE} declares `specs.credential`; a credential source belongs to the "
            "user layer (~/.config/quenching/config.json), never to the repository. No spec "
            "was read or written", key="specs.credential")
    raw = specs.get("store")
    if raw is None:
        return {}, {}
    if not isinstance(raw, dict):
        return {}, _refusal("sp-store-invalid",
                            f"{CONFIG_FILE} `specs.store` must be an object. No spec was read "
                            "or written")
    for path in _secret_paths(raw, "specs.store"):
        return {}, _refusal(
            "sp-store-secret",
            f"{CONFIG_FILE} declares `{path}`, whose name says it carries a secret; the "
            "repository states only where the store is, and the credential is resolved from "
            "the user layer. No spec was read or written", key=path)
    out: dict = {}
    for tool, block in raw.items():
        if tool not in STORE_KEYS or not isinstance(block, dict):
            return {}, _refusal(
                "sp-store-unknown-key",
                f"{CONFIG_FILE} `specs.store.{tool}` is not recognised; the tools are "
                f"{', '.join(STORE_KEYS)}, each an object. No spec was read or written",
                key=f"specs.store.{tool}")
        for key in block:
            if key not in STORE_KEYS[tool]:
                return {}, _refusal(
                    "sp-store-unknown-key",
                    f"{CONFIG_FILE} `specs.store.{tool}.{key}` is not recognised; the keys "
                    f"are {', '.join(STORE_KEYS[tool])}. No spec was read or written",
                    key=f"specs.store.{tool}.{key}")
        values = {}
        for key in STORE_KEYS[tool]:
            value = block.get(key)
            if not isinstance(value, str) or not value.strip():
                return {}, _refusal(
                    "sp-store-invalid",
                    f"{CONFIG_FILE} `specs.store.{tool}.{key}` must be a non-empty string. "
                    "No spec was read or written", key=f"specs.store.{tool}.{key}")
            values[key] = value.strip()
        if tool == "azure":
            org = values["organization"].rstrip("/")
            if _ORG_NAME.match(org):
                org = f"https://dev.azure.com/{org}"
            elif not re.match(r"^https://\S+$", org):
                return {}, _refusal(
                    "sp-store-invalid",
                    f"{CONFIG_FILE} `specs.store.azure.organization` must be an organisation "
                    "name or an https URL. No spec was read or written")
            values["organization"] = org
        elif not _REPO.match(values["repository"]):
            return {}, _refusal(
                "sp-store-invalid",
                f"{CONFIG_FILE} `specs.store.github.repository` must be `<owner>/<name>`. "
                "No spec was read or written")
        out[tool] = values
    return out, {}


def credential_env(tool: str) -> tuple[dict, dict]:
    """The environment variables to add for `tool` (`azure` | `github`), from the user layer
    alone, and the refusal. Nothing configured is `({}, {})`: the CLI keeps its own login."""
    layer = _load_user_layer()["namespaces"].get("specs")
    block = (layer or {}).get("credential") if isinstance(layer, dict) else None
    entry = block.get(tool) if isinstance(block, dict) else None
    if entry is None:
        return {}, {}
    token_var, config_var = TOOL_ENV[tool]
    if not isinstance(entry, dict) or entry.get("source") not in SOURCES:
        given = entry.get("source") if isinstance(entry, dict) else entry
        return {}, _refusal(
            "sp-credential-source",
            f"user layer `specs.credential.{tool}.source` is {given!r}; the sources are "
            f"{', '.join(SOURCES)}. A keyring is reached through the `{'az' if tool == 'azure' else 'gh'}` "
            "login, which stores the token there. No spec was read or written")
    env: dict = {}
    config_dir = entry.get("configDir")
    if isinstance(config_dir, str) and config_dir.strip():
        env[config_var] = os.path.expanduser(config_dir.strip())
    if entry["source"] == "env":
        name = entry.get("env")
        if not isinstance(name, str) or not _VAR.match(name):
            return {}, _refusal(
                "sp-credential-source",
                f"user layer `specs.credential.{tool}.env` must name an environment variable. "
                "No spec was read or written")
        token = os.environ.get(name)
        if not token:
            return {}, _refusal(
                "sp-credential-env-missing",
                f"user layer `specs.credential.{tool}` takes the token from ${name}, which is "
                "not set. No spec was read or written", variable=name)
        env[token_var] = token
    return env, {}


def subprocess_env(tool: str) -> dict | None:
    """The environment for one `az`/`gh` subprocess: `None` (inherit) when no credential is
    configured, else a copy of ours with the credential variables set. A bad configuration is a
    `BackendRefusal`, the boundary every other backend failure already crosses."""
    extra, err = credential_env(tool)
    if err:
        raise BackendRefusal(err)
    return {**os.environ, **extra} if extra else None
