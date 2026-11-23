"""The single read pass — every whole-tree consumer reads each file exactly once.

Moved verbatim out of the pre-refactor OKF validator script. `_`-prefixed, dot, and asset dirs
are pruned from the whole bundle walk (they hold private/raw sidecar content, never OKF
concepts), and both the per-file conformance checks and the structural-integrity pass
consume the one dict this builds.
"""
from __future__ import annotations

import fnmatch
import os
import pathlib
import time

from quenching.knowledge.schema import ASSET_DIRS


def is_private_name(name: str) -> bool:
    """A path segment that is never published: a dot entry (`.git`) or a `_`-prefixed
    private/raw sidecar (`_curadoria/`, `_drafts/`). The one predicate every walk shares."""
    return name.startswith((".", "_"))


def _is_skipped_dir(name: str) -> bool:
    """A directory pruned from the bundle walk: a private name or a known asset dir."""
    return is_private_name(name) or name in ASSET_DIRS


def _is_ignored_path(rel_path: str, patterns: tuple[str, ...]) -> bool:
    """`rel_path` is bundle-relative, forward-slash. A pattern matches as a
    dir-prefix (`rel_path == pattern` or `rel_path` starts with `pattern + "/"`)
    or, failing that, as an `fnmatch` glob — per `ignoreGlobs` in hooks-config.json
    (regenerable/vendored trees like the gitignored mirrors under
    `external/repositories/` are not authored OKF knowledge)."""
    for pat in patterns:
        pat = str(pat).strip("/")
        if not pat:
            continue
        if rel_path == pat or rel_path.startswith(pat + "/"):
            return True
        if fnmatch.fnmatch(rel_path, pat):
            return True
    return False


def _build_corpus(bundle_root: str, deadline: float | None = None,
                   ignore_globs: tuple[str, ...] = ()):
    """One `os.walk` over the bundle (skipped/ignored dirs pruned), each `.md`
    read ONCE.

    Returns `{abspath: text}` — text is None for an unreadable file — or **None**
    when `deadline` (a `time.monotonic()` instant) expires mid-walk, so hook mode
    can abort silently instead of delaying the turn. CLI mode passes no deadline.
    Both the per-file conformance checks and the structural-integrity pass consume
    this dict; nothing whole-tree reads from disk after it is built.
    """
    corpus: dict[str, str | None] = {}
    root = os.path.abspath(bundle_root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [
            d for d in dirnames
            if not _is_skipped_dir(d)
            and not _is_ignored_path(f"{rel_dir}/{d}" if rel_dir else d, ignore_globs)
        ]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            if deadline is not None and time.monotonic() > deadline:
                return None
            ap = os.path.join(dirpath, fn)
            try:
                corpus[ap] = pathlib.Path(ap).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                corpus[ap] = None
    return corpus
