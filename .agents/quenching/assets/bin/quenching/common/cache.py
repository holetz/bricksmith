"""The one directory every quenching cache lives under.

Every cache (`git`, `github`, `azure`, `azure-sync`, `mirrors`) is `cache_root() / <name>`, so an
antivirus policy or a cleanup names one path instead of five. Resolution order:

1. `$XDG_CACHE_HOME/quenching` — whoever declared it wins, on every platform (the suites isolate
   with it).
2. Windows: `%LOCALAPPDATA%\\quenching`.
3. `~/.cache/quenching`.

On Windows, a legacy `~/.cache/quenching` is moved to the new root the first time the root is
resolved and the new one does not exist yet. The move is best-effort: nothing in a cache is
authoritative, so a move that fails leaves the old directory alone and the cache starts empty.
"""
from __future__ import annotations

import os
import sys

NAME = "quenching"


def _legacy_root(env) -> str:
    return os.path.join(env.get("HOME") or os.path.expanduser("~"), ".cache", NAME)


def resolve_cache_root(environ=None, platform: str | None = None) -> str:
    """The cache root for `environ` and `platform`, with no side effect."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_CACHE_HOME")
    if base:
        return os.path.join(base, NAME)
    if (platform or sys.platform).lower().startswith("win") and env.get("LOCALAPPDATA"):
        return os.path.join(env["LOCALAPPDATA"], NAME)
    return _legacy_root(env)


def migrate_legacy(root: str, environ=None) -> bool:
    """Move a legacy `~/.cache/quenching` to `root` when only the legacy one exists."""
    legacy = _legacy_root(os.environ if environ is None else environ)
    if os.path.normcase(os.path.abspath(legacy)) == os.path.normcase(os.path.abspath(root)):
        return False
    if os.path.exists(root) or not os.path.isdir(legacy):
        return False
    try:
        os.makedirs(os.path.dirname(root), exist_ok=True)
        os.replace(legacy, root)
    except OSError:
        return False
    return True


def cache_root() -> str:
    """The cache root for this process; on Windows a legacy root is migrated, silently."""
    root = resolve_cache_root()
    if sys.platform.startswith("win"):
        migrate_legacy(root)
    return root


def cache_path(*parts: str) -> str:
    """A path under the cache root."""
    return os.path.join(cache_root(), *parts)
