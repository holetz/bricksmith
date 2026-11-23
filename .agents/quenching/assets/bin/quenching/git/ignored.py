"""The ignored content a worktree may discard: the built-in regenerable list, the matching rules
and the one read of git's ignored entries.

`worktree retire` and `git stale` ask the same question, so both consume this module: a `.venv`
is a cache the repository rebuilds, while a `.tmp` or an ignored notebook may be work. The
built-in list covers the unambiguous Python caches, so a target that declares nothing still lets
them go; `shared.regenerablePaths` adds to it and never removes a default."""
from __future__ import annotations

import fnmatch
import os
import stat

from quenching.common.config import namespace
from quenching.common.git import _git_run
from quenching.git.audit import SAFE

DEFAULT_REGENERABLE = (
    ".venv", "venv", "__pycache__", "*.py[cod]", ".pytest_cache", ".ruff_cache",
    ".mypy_cache", ".pyright", ".tox", ".nox", "htmlcov", ".coverage", ".coverage.*",
    "*.egg-info", ".eggs", "pip-wheel-metadata", ".cache", ".ipynb_checkpoints",
    ".hypothesis", "node_modules",
)


def _is_link(path: str) -> bool:
    """A symlink, or on Windows a directory junction (a reparse point `islink` misses before 3.12)."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is not None:
        return isjunction(path)
    try:
        attrs = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def declared_regenerable(config: dict) -> list[str]:
    """The built-in list followed by `shared.regenerablePaths`, each entry relative and without `..`.

    A declaration adds to the defaults; an absent, malformed or empty list never removes one."""
    values = namespace(config, "shared").get("regenerablePaths")
    rules = list(DEFAULT_REGENERABLE)
    if not isinstance(values, list):
        return rules
    return rules + [
        os.path.normpath(v.strip()) + ("/" if v.strip().endswith("/") else "")
        for v in values if isinstance(v, str) and v.strip()
        and not os.path.isabs(v.strip()) and ".." not in v.strip().split("/")
    ]


def is_regenerable(entry: str, rules: list[str]) -> bool:
    """Whether an ignored entry is covered by one of `rules`.

    An entry without a slash names a component at any depth and a `*` in it matches with
    `fnmatch` (`*.egg-info`, `*.py[cod]`); an entry with a slash, inner or trailing, is anchored
    at the repository root — literal, itself or anything under it."""
    parts = os.path.normpath(entry.rstrip("/")).split(os.sep)
    for rule in rules:
        if os.sep in rule:
            anchored = rule.rstrip(os.sep)
            width = len(anchored.split(os.sep))
            if os.sep.join(parts[:width]) == anchored:
                return True
        elif "*" in rule:
            if any(fnmatch.fnmatch(part, rule) for part in parts):
                return True
        elif rule in parts:
            return True
    return False


def ignored_entries(path: str) -> list[str] | None:
    """The ignored entries `path` holds, symlinks excluded, or None when git cannot answer.

    The status is bounded (`--untracked-files=normal --ignored=matching`), so a wholly ignored
    directory arrives as one entry instead of its whole expansion. A shared path `worktree link`
    materialised is a symlink into a store: it survives the removal and is not this worktree's
    content to count. None is a refusal, never an empty worktree."""
    code, out, _err = _git_run(path, *SAFE, "status", "--porcelain",
                               "--untracked-files=normal", "--ignored=matching")
    if code != 0:
        return None
    entries = [line[3:] for line in out.splitlines() if line.startswith("!! ")]
    return [e for e in entries if not _is_link(os.path.join(path, e.rstrip("/")))]


def classify(path: str, entries: list[str], rules: list[str]) -> tuple[list[str], list[str]]:
    """Split `entries` into the two classes the report names: covered and left over.

    A wholly ignored directory is opened to its files, so a cache inside a directory nobody
    declared — `output/.coverage` — is reported as regenerable instead of vanishing from both
    lists, and an unrecognized file inside it is named by its own path."""
    regenerable, unrecognized = [], []
    for entry in entries:
        if is_regenerable(entry, rules):
            regenerable.append(entry)
        elif entry.endswith("/"):
            walked = list(os.walk(os.path.join(path, entry)))
            files = [os.path.relpath(os.path.join(root, name), path)
                     for root, _dirs, names in walked for name in names]
            if not files:
                # No file to lose: the directory is regenerable when every empty leaf it holds is
                # (`.quenching/` holding only an emptied `.quenching/coverage`); an empty unknown
                # directory is still a leaf nobody declared.
                leaves = [os.path.relpath(root, path) for root, dirs, _names in walked if not dirs]
                covered = bool(leaves) and all(is_regenerable(leaf, rules) for leaf in leaves)
                (regenerable if covered else unrecognized).append(entry)
            for name in files:
                (regenerable if is_regenerable(name, rules) else unrecognized).append(name)
        else:
            unrecognized.append(entry)
    return regenerable, unrecognized


def undeclared(path: str, entries: list[str], rules: list[str]) -> list[str]:
    """The ignored entries no rule covers — the view `worktree retire` refuses on."""
    return classify(path, entries, rules)[1]
