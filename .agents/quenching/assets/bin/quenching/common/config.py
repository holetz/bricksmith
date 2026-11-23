"""Read the repository-wide configuration envelope.

The file is shared by every front, while each namespace remains owned by its adapter. This
module only discovers the configuration home, parses the envelope and derives the provider; it
does not interpret a front's values.
"""
from __future__ import annotations

import json
import os
from typing import Protocol

from quenching.common.git import _git
from quenching.common.remote import remote_host as _remote_host
from quenching.common.io import read_text, write_text


CONFIG_FILE = os.path.join(".agents", "quenching.json")
# The home before the generic `.agents/` layer: while it exists it is the file read, exactly as
# before the layer existed, so an upgrade never swaps a repository onto a stale `.agents/` copy;
# `migrate` is what moves it, and a home beside it is reported as shadowed.
CLAUDE_CONFIG_FILE = os.path.join(".claude", "quenching.json")
CONFIG_FILES = (CONFIG_FILE, CLAUDE_CONFIG_FILE)
LEGACY_CONFIG_FILE = "config.json"
USER_CONFIG_ENV = "QUENCHING_USER_CONFIG"
LOCAL_CONFIG_FILE = os.path.join(".agents", "quenching.local.json")
NAMESPACES = ("shared", "specs", "ops", "proof", "delivery")
PROVIDER_ENV = "QUENCHING_CONFIG_PROVIDER"
DEFAULT_PROVIDER = "file"

# Root-level keys accepted by the pre-envelope format. They are diagnostic-only now: a front
# adapter must refuse to interpret them because doing so makes a half-migrated repository look
# healthy while one namespace silently wins over another.
LEGACY_KEYS = frozenset({
    "worktreeSetup", "sharedPaths", "specsBranch", "azureStates", "hooks", "profiles",
    "azurePlacement", "azureColumns", "subjects", "tagCatalog", "workItemTypes",
    "fanoutMinComplexity", "opsRoot", "router", "registry", "proofRoot", "layers",
    "measuredRoots", "proofExclusions", "ratchetPath", "gitConventions",
})

LEGACY_DESTINATIONS = {
    "worktreeSetup": "shared",
    "sharedPaths": "shared",
    "hooks": "shared",
    "profiles": "shared",
    "gitConventions": "shared",
    "specsBranch": "specs",
    "azureStates": "specs",
    "azurePlacement": "specs",
    "azureColumns": "specs",
    "subjects": "specs",
    "tagCatalog": "specs",
    "workItemTypes": "specs",
    "fanoutMinComplexity": "specs",
    "opsRoot": "ops",
    "router": "ops",
    "registry": "ops",
    "proofRoot": "proof",
    "layers": "proof",
    "measuredRoots": "proof",
    "proofExclusions": "proof",
    "ratchetPath": "proof",
}


def config_path(repo_root: str) -> str:
    """The configuration file a repository is read from: the legacy file while it exists, else
    the home, which is also where a writer creates one."""
    legacy = os.path.join(repo_root, CLAUDE_CONFIG_FILE)
    return legacy if os.path.isfile(legacy) else os.path.join(repo_root, CONFIG_FILE)


def shadowed_config_path(repo_root: str) -> str | None:
    """The home nobody reads, when the legacy file still exists beside it."""
    home = os.path.join(repo_root, CONFIG_FILE)
    if os.path.isfile(home) and os.path.isfile(os.path.join(repo_root, CLAUDE_CONFIG_FILE)):
        return home
    return None


class ConfigProvider(Protocol):
    """Where the repository layer of the configuration comes from.

    `read` returns `(document, info)`: the parsed repository envelope, or `None` when there is
    none or it could not be parsed, and `{name, path, present, unparseable}` describing the read.
    A provider only changes the origin of the document; the merge with the user layer, the legacy
    refusal and the meaning of every namespace stay in `load_config` and in each front.
    """

    name: str

    def read(self, repo_root: str) -> tuple[dict | None, dict]: ...


class FileProvider:
    """The file read: the legacy `.claude/quenching.json` at the repository root while it
    exists, else `.agents/quenching.json`."""

    name = "file"

    def read(self, repo_root: str) -> tuple[dict | None, dict]:
        path = config_path(repo_root)
        info = {"name": self.name, "path": path, "present": os.path.isfile(path),
                "unparseable": None, "shadowedPath": shadowed_config_path(repo_root)}
        if not info["present"]:
            return None, info
        try:
            obj = json.loads(read_text(path) or "")
        except json.JSONDecodeError as exc:
            info["unparseable"] = str(exc)
            return None, info
        if not isinstance(obj, dict):
            info["unparseable"] = f"top level is {type(obj).__name__}, not an object"
            return None, info
        return obj, info


_PROVIDERS: dict[str, ConfigProvider] = {DEFAULT_PROVIDER: FileProvider()}


def register_provider(provider: ConfigProvider) -> None:
    """Make `provider` selectable by `QUENCHING_CONFIG_PROVIDER=<provider.name>`."""
    _PROVIDERS[provider.name] = provider


def unregister_provider(name: str) -> None:
    """Drop a registered provider; the `file` provider cannot be dropped."""
    if name != DEFAULT_PROVIDER:
        _PROVIDERS.pop(name, None)


def active_provider() -> tuple[ConfigProvider | None, dict | None]:
    """The provider `QUENCHING_CONFIG_PROVIDER` names (absent means `file`), or the exit-2
    refusal naming the registered ones. An unknown name never falls back to the file."""
    name = (os.environ.get(PROVIDER_ENV) or "").strip() or DEFAULT_PROVIDER
    provider = _PROVIDERS.get(name)
    if provider is not None:
        return provider, None
    return None, {
        "code": "cq-config-provider-unknown",
        "exit": 2,
        "provider": name,
        "registered": sorted(_PROVIDERS),
        "message": f"{PROVIDER_ENV}={name} names no configuration provider; "
                   f"registered: {', '.join(sorted(_PROVIDERS))}",
        "remedy": f"unset {PROVIDER_ENV} to read {CONFIG_FILE}, or name a registered provider",
    }


def repository_document(root: str) -> tuple[dict | None, dict, dict | None]:
    """The repository layer raw, through the active provider: `(document, info, refusal)`.

    For the readers that need the repository's own declaration before any merge. An unknown
    provider yields no document and the refusal; it never reads the file instead.
    """
    provider, refusal = active_provider()
    if provider is None:
        return None, {"name": None, "path": None, "present": False, "unparseable": None}, refusal
    document, info = provider.read(find_repo_root(root))
    return document, info, None


def find_repo_root(specs_root: str) -> str:
    """Find the repository containing the shared configuration home."""
    current = os.path.abspath(specs_root)
    while True:
        if any(os.path.isfile(os.path.join(current, name)) for name in CONFIG_FILES):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    top = (_git(specs_root, "rev-parse", "--show-toplevel").strip()
           if os.path.isdir(specs_root) else "")
    if top:
        return top
    return os.path.dirname(os.path.abspath(specs_root))


def detect_provider(root: str) -> tuple[str | None, str | None]:
    """Derive the provider from the repository's origin URL."""
    repo = find_repo_root(root)
    remote = _git(repo, "remote", "get-url", "origin").strip()
    if not remote:
        return None, None
    host = _remote_host(remote)
    if host == "github.com" or host.endswith(".github.com"):
        return "github", host
    if (host == "dev.azure.com" or host.endswith(".dev.azure.com")
            or host == "visualstudio.com" or host.endswith(".visualstudio.com")):
        return "azure-boards", host
    return None, host


def infer_base_branch(cfg: dict, origin_head: str | None, init_default: str | None) -> str:
    """Resolve the unstamped base branch from already-collected git facts.

    The branch chain is shared by the git and specs fronts. Keeping it here lets git consume
    the common envelope without importing the specs adapter just for this pure decision.
    ``cfg`` remains in the signature for callers that pass the full configuration envelope;
    no configuration key participates in the fallback chain.
    """
    if origin_head:
        return origin_head
    if init_default:
        return init_default
    return "main"


def _migration_refusal(path: str, obj: dict, legacy_keys: list[str]) -> dict | None:
    """Describe the exit-2 refusal for a flat or mixed pre-envelope document."""
    if not legacy_keys:
        return None
    destinations = [
        {"key": key, "namespace": LEGACY_DESTINATIONS[key]}
        for key in legacy_keys
    ]
    shape = "mixed" if any(name in obj for name in NAMESPACES) else "flat"
    moved = ", ".join(f"`{row['key']}` → `{row['namespace']}`" for row in destinations)
    return {
        "code": "sp-config-unscoped",
        "exit": 2,
        "config": path,
        "shape": shape,
        "keys": legacy_keys,
        "destinations": destinations,
        "message": f"{CONFIG_FILE} uses {shape} legacy configuration: {moved}; "
                   "the front refuses to read unscoped declarations",
        "remedy": f"move every listed key into its destination namespace in {CONFIG_FILE}; "
                  "do not mix flat and namespaced declarations",
    }


def user_config_path() -> str:
    """The user layer's file: `$QUENCHING_USER_CONFIG`, else `~/.config/quenching/config.json`."""
    override = os.environ.get(USER_CONFIG_ENV)
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".config", "quenching", "config.json")


def _load_user_layer() -> dict:
    """Read the user layer: the same envelope, namespaces only.

    The root `backend` and the pre-envelope flat keys describe one repository, so they are
    ignored here and listed in `ignoredKeys`. An unreadable file is reported, never fatal.
    """
    path = user_config_path()
    out = {"path": path, "present": os.path.isfile(path), "unparseable": None,
           "namespaces": {}, "ignoredKeys": []}
    if not out["present"]:
        return out
    try:
        obj = json.loads(read_text(path) or "")
    except json.JSONDecodeError as exc:
        out["unparseable"] = str(exc)
        return out
    if not isinstance(obj, dict):
        out["unparseable"] = f"top level is {type(obj).__name__}, not an object"
        return out
    for key, value in obj.items():
        if key in NAMESPACES and isinstance(value, dict):
            out["namespaces"][key] = value
        else:
            out["ignoredKeys"].append(key)
    out["ignoredKeys"].sort()
    return out


def local_config_path(repo_root: str) -> str:
    """The local layer: this repository on this machine, kept out of git."""
    return os.path.join(repo_root, LOCAL_CONFIG_FILE)


def _load_local_layer(repo_root: str) -> dict:
    """Read `.agents/quenching.local.json`: namespaces only, an unreadable file reported."""
    path = local_config_path(repo_root)
    out = {"path": path, "present": os.path.isfile(path), "unparseable": None, "namespaces": {}}
    if not out["present"]:
        return out
    try:
        obj = json.loads(read_text(path) or "")
    except json.JSONDecodeError as exc:
        out["unparseable"] = str(exc)
        return out
    if not isinstance(obj, dict):
        out["unparseable"] = f"top level is {type(obj).__name__}, not an object"
        return out
    out["namespaces"] = {k: v for k, v in obj.items() if k in NAMESPACES and isinstance(v, dict)}
    out["ignoredKeys"] = sorted(k for k in obj if k not in out["namespaces"])
    return out


def _has_path(document: dict, parts: list[str]) -> bool:
    node = document
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _apply_local_layer(out: dict, layer: dict, repo: dict) -> None:
    """Set each local leaf the key's scope lets through: `machine` over everything, `either` only
    where the repository is silent, `project` never (listed in `localConfig.ignored`)."""
    from quenching.config.schema import local_decision, registry_leaves   # registry imports this

    applied, ignored = [], []
    for name, values in layer.items():
        if name in out["invalidNamespaces"]:
            continue
        for key, value in registry_leaves({name: values}):
            parts = key.split(".")
            ok, why = local_decision(key, _has_path(repo, parts))
            if not ok:
                if why:
                    ignored.append({"key": key, "reason": why})
                continue
            merged = dict(out[name])
            node = merged
            for part in parts[1:-1]:
                node[part] = dict(node[part]) if isinstance(node.get(part), dict) else {}
                node = node[part]
            node[parts[-1]] = value
            out[name] = merged
            out["namespaces"][name] = merged
            if isinstance(out["data"], dict):
                out["data"] = {**out["data"], name: merged}
            applied.append(key)
    out["localConfig"].update(applied=sorted(applied), ignored=ignored)


def load_config(root: str, *, detect_provider_info: bool = True) -> dict:
    """Return the parsed envelope and metadata without interpreting any namespace."""
    repo = find_repo_root(root)
    path = config_path(repo)
    provider, provider_host = (detect_provider(root) if detect_provider_info
                               else (None, None))
    out = {
        "path": path,
        "present": os.path.isfile(path),
        "unparseable": None,
        "shadowedPath": None,
        "provider": provider,
        "providerHost": provider_host,
        "unknownProvider": provider_host if provider is None else None,
        "data": {},
        "namespaces": {name: {} for name in NAMESPACES},
        "shared": {},
        "specs": {},
        "ops": {},
        "proof": {},
        "delivery": {},
        "legacyKeys": [],
        "migrationRefusal": None,
        "unknownKeys": [],
        "unknownNamespaces": [],
        "invalidNamespaces": [],
        "legacyPath": os.path.join(root, LEGACY_CONFIG_FILE)
        if os.path.isfile(os.path.join(root, LEGACY_CONFIG_FILE)) else None,
        "userConfig": {"path": None, "present": False, "unparseable": None,
                       "ignoredKeys": [], "keys": {}},
        "localConfig": {"path": None, "present": False, "unparseable": None,
                        "applied": [], "ignored": []},
    }
    local = _load_local_layer(repo)
    out["localConfig"].update(path=local["path"], present=local["present"],
                              unparseable=local["unparseable"])
    user = _load_user_layer()
    out["userConfig"] = {"path": user["path"], "present": user["present"],
                         "unparseable": user["unparseable"],
                         "ignoredKeys": user["ignoredKeys"],
                         "keys": {name: sorted(value)
                                  for name, value in user["namespaces"].items()}}
    provider, refusal = active_provider()
    out["source"] = provider.name if provider else None
    out["providerRefusal"] = refusal
    obj, info = provider.read(repo) if provider else (None, {
        "name": None, "path": path, "present": False, "unparseable": None})
    out["path"] = info["path"]
    out["present"] = info["present"]
    out["unparseable"] = info["unparseable"]
    out["shadowedPath"] = info.get("shadowedPath")
    if obj is None:
        if not info["unparseable"]:
            _apply_user_layer(out, user["namespaces"], {})
            _apply_local_layer(out, local["namespaces"], {})
        return out

    out["data"] = obj
    for name in NAMESPACES:
        value = obj.get(name)
        if isinstance(value, dict):
            out[name] = value
            out["namespaces"][name] = value
        elif name in obj:
            out["invalidNamespaces"].append(name)
    _apply_user_layer(out, user["namespaces"], obj)
    _apply_local_layer(out, local["namespaces"], obj)
    out["legacyKeys"] = sorted(key for key in obj if key in LEGACY_KEYS)
    envelope_keys = {"backend", *NAMESPACES}
    out["unknownKeys"] = sorted(
        key for key in obj if key not in envelope_keys and key not in LEGACY_KEYS
    )
    out["unknownNamespaces"] = sorted(
        key for key, value in obj.items()
        if isinstance(value, dict) and key not in envelope_keys and key not in LEGACY_KEYS
    )
    out["migrationRefusal"] = _migration_refusal(path, obj, out["legacyKeys"])
    return out


def _apply_user_layer(out: dict, layer: dict, repo: dict) -> None:
    """Fill each namespace with the user's keys the repository does not declare.

    The repository wins key by key: a key present in the repository's namespace replaces the
    user's value whole, never merges into it. A namespace the repository declares as a
    non-object stays invalid and the user layer does not paper over it.
    """
    for name, values in layer.items():
        if name in out["invalidNamespaces"] or (name in repo and not isinstance(repo[name], dict)):
            continue
        merged = {**values, **(repo.get(name) or {})}
        out[name] = merged
        out["namespaces"][name] = merged
        if isinstance(out["data"], dict):
            out["data"] = {**out["data"], name: merged}


def namespace(config: dict, name: str) -> dict:
    """Return one parsed namespace without letting a caller fall back to another one.

    The envelope is the shared loader's boundary; this small accessor keeps adapters from
    reaching through it with a flat-key fallback.  A malformed or absent namespace is an empty
    declaration and remains the owning adapter's decision to accept or refuse.
    """
    namespaces = config.get("namespaces")
    if not isinstance(namespaces, dict):
        return {}
    value = namespaces.get(name)
    return value if isinstance(value, dict) else {}


def detect_profile(root: str, backend: str | None = None) -> list[str]:
    """Detect the fronts with a local signal for a first-use profile.

    This is deliberately a conservative signal inventory, not an align implementation. It
    only names a front when the same inexpensive filesystem evidence that `/align` documents is
    present. The provider-owned `specs` axis is included when a backend is known, because its
    external document store has no local directory to probe.
    """
    repo = os.path.abspath(root)

    def has_executable(directory: str) -> bool:
        path = os.path.join(repo, directory)
        if not os.path.isdir(path):
            return False
        try:
            with os.scandir(path) as entries:
                return any(entry.is_file() and os.access(entry.path, os.X_OK)
                           for entry in entries)
        except OSError:
            return False

    def has_file(directory: str) -> bool:
        path = os.path.join(repo, directory)
        if not os.path.isdir(path):
            return False
        try:
            with os.scandir(path) as entries:
                return any(entry.is_file() for entry in entries)
        except OSError:
            return False

    detected: list[str] = []
    if os.path.isdir(os.path.join(repo, "docs")):
        detected.append("knowledge")
    if any(os.path.exists(os.path.join(repo, name))
           for name in (".design", "PRODUCT.md", "DESIGN.md")):
        detected.append("design")
    if any(has_file(name) for name in (os.path.join(".claude", "commands"),
                                       os.path.join(".agents", "skills"), "commands")):
        detected.append("components")
    if any(has_executable(name) for name in ("scripts", "tools", "bin", "script")):
        detected.append("ops")
    if any(has_file(name) for name in ("tests", "test")):
        detected.append("proof")
    if any(os.path.isfile(os.path.join(repo, name)) for name in (
            "pyproject.toml", "setup.cfg", "package.json", "Cargo.toml", "uv.lock",
            "poetry.lock", "package-lock.json", "Cargo.lock", ".python-version", ".nvmrc")):
        detected.append("toolchain")
    if any(has_file(name) for name in (os.path.join(".github", "workflows"),
                                       ".azure-pipelines", ".gitlab")):
        detected.append("delivery")
    if backend:
        # `specs` is not part of LOCAL_FRONT_ORDER because it has no local align row. Keeping it
        # beside knowledge still makes the generated profile stable and human-readable.
        detected.insert(1 if detected and detected[0] == "knowledge" else 0, "specs")
    return detected


def prepare_minimal_config(root: str) -> tuple[dict | None, dict]:
    """Prepare the first-use envelope without writing it or clobbering an existing one."""
    repo = os.path.abspath(find_repo_root(root))
    path = config_path(repo)
    if os.path.isfile(path):
        return None, {
            "code": "cq-config-present",
            "exit": 2,
            "config": path,
            "message": f"{CONFIG_FILE} already exists; setup will not overwrite it",
            "remedy": f"use the existing {CONFIG_FILE}, or edit it explicitly before rerunning doctor",
        }
    backend, host = detect_provider(repo)
    if not backend:
        return None, {
            "code": "cq-setup-provider-missing",
            "exit": 2,
            "config": path,
            "provider": host,
            "message": "first-use setup could not detect a GitHub or Azure DevOps provider",
            "remedy": "configure an origin remote for GitHub or Azure DevOps, then rerun `cq doctor --setup`",
        }
    profile = detect_profile(repo, backend)
    document = {
        "backend": backend,
        "shared": {"profiles": {"installed": profile}},
    }
    return {"path": path, "backend": backend, "profile": profile, "document": document}, {}


def write_minimal_config(root: str) -> tuple[dict | None, dict]:
    """Write the prepared first-use envelope after the caller's explicit confirmation."""
    plan, error = prepare_minimal_config(root)
    if error:
        return None, error
    assert plan is not None
    os.makedirs(os.path.dirname(plan["path"]), exist_ok=True)
    write_text(plan["path"], json.dumps(plan["document"], indent=2, ensure_ascii=False) + "\n")
    return {**plan, "written": True}, {}


def setting(root: str, ns: str, path: str, default, check) -> tuple[object, str | None]:
    """Read one optional leaf of a namespace: `(value, warning)`.

    `path` is dotted inside the namespace (`contextBudgets.worker`). An absent leaf, or a block
    along the way that is absent, returns `default` and no warning; a leaf `check` rejects returns
    `default` and a warning naming the ignored value, so a typo never changes a limit silently.
    """
    node: object = namespace(load_config(root, detect_provider_info=False), ns)
    for part in path.split("."):
        if not isinstance(node, dict):
            return default, f"{ns}.{path} is under a value that is not an object; {default!r} applies"
        if part not in node:
            return default, None
        node = node[part]
    if check(node):
        return node, None
    return default, f"{ns}.{path} has an invalid value {node!r}; {default!r} applies"
