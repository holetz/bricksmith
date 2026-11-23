"""One `files:` entry as the repository path git reports — the single normalisation every
consumer of `files:` compares through (`cq specs parallel`, the Codex pillar)."""
from __future__ import annotations

import posixpath
import re

NEW_ANNOTATION_RE = re.compile(r"\s*\(new\)\s*$")


def repo_path(entry: str) -> str:
    """`./docs/x.md (new)` → `docs/x.md`; `src/../a.py` → `a.py`; `.github/x` keeps its dot.

    Only the reserved `(new)` annotation is dropped: `(operational)` carries no path, and any
    other trailing parenthetical is refused upstream by `specs.parse.tasks`."""
    path = NEW_ANNOTATION_RE.sub("", entry.strip()).replace("\\", "/")
    return posixpath.normpath(path).lstrip("/")


def escapes_repo(path: str) -> bool:
    """A normalised path that climbs out of the repository (`..`, `../x`)."""
    return path == ".." or path.startswith("../")
