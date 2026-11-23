"""The spec backends, and the one factory that opens the declared one.

`open_backend` and its cache live in the package `__init__` because they are the package's
whole public surface: every caller wants `from quenching.specs.backends import open_backend`,
and this is the ONLY place that may know the external implementations at once.

This is therefore the one directory under `quenching/` that is a regular package rather than a
PEP 420 namespace one — an `__init__.py` that carries the factory, never an empty file added
for its own sake."""
from __future__ import annotations

from quenching.common.config import load_config
from quenching.specs.backends.azure import open_azure_backend
from quenching.specs.backends.base import SpecBackend
from quenching.specs.backends.git import open_git_backend
from quenching.specs.backends.github import open_github_backend


_BACKEND_CACHE: dict[str, SpecBackend] = {}


def backend_root(root: str, cfg: dict) -> str | None:
    """External backends keep their canonical documents outside the local filesystem."""
    return None


def open_backend(root: str) -> tuple[SpecBackend | None, dict]:
    """Open the provider selected from the repository remote, or return a refusal.

    Memoised per root because resolving it reads the config from disk, and a single command
    asks for it more than once. The cache holds no mutable state — a backend is its root and
    nothing else — so this is a lookup table, not a session."""
    if root in _BACKEND_CACHE:
        return _BACKEND_CACHE[root], {}
    cfg = load_config(root)
    if cfg.get("providerRefusal"):
        # An unknown configuration provider leaves the envelope empty: the declared backend is
        # never read, so going on would infer one from the remote.
        return None, cfg["providerRefusal"]
    name = cfg.get("provider") or cfg.get("backend")
    declared = cfg.get("unknownBackend")
    raw_backend = cfg.get("data", {}).get("backend")
    if not declared and isinstance(raw_backend, str) and raw_backend.strip() \
            and raw_backend.strip() not in ("github", "azure-boards", "git"):
        declared = raw_backend.strip()
    if declared:
        if declared == "files":
            return None, {
                "code": "sp-backend-removed", "exit": 2, "backend": declared,
                "message": "backend 'files' was removed; configure a supported external "
                           "provider instead — no local store was created",
            }
        return None, {
            "code": "sp-config-unknown-backend", "exit": 2, "backend": declared,
            "message": f"backend '{declared}' is not supported; the backend is selected "
                       "from the repository provider",
        }
    if isinstance(raw_backend, str) and raw_backend.strip() == "git":
        # Declared, never derived: the git store follows the code host whatever it is, so it
        # wins over the provider the remote URL implies.
        store, err = open_git_backend(root)
        if err:
            return None, err
        backend = store                                     # type: ignore[assignment]
    elif name == "github":
        gh, err = open_github_backend(root)
        if err:
            return None, err
        backend = gh                                        # type: ignore[assignment]
    elif name == "azure-boards":
        az, err = open_azure_backend(root)
        if err:
            return None, err
        from quenching.specs.sync import wrap_if_enabled   # local: sync imports this package
        backend = wrap_if_enabled(az)                       # type: ignore[assignment]
    else:
        provider = cfg.get("unknownProvider") or "no recognized repository provider"
        return None, {
            "code": "sp-provider-unknown", "exit": 2, "provider": provider,
            "message": f"repository provider '{provider}' is not recognized; supported "
                       "providers are GitHub and Azure DevOps — no spec was read or written",
        }
    _BACKEND_CACHE[root] = backend
    return backend, {}
