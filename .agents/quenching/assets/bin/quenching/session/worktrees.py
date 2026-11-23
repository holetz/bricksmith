"""Worktrees of a repository, read from git's own files — no process is opened.

`cq git stale` reports on what git registers; this module answers "which worktrees does this
repository have, and which transcript folder does each one own" for callers that may not spawn
`git` (a portal GET)."""
from __future__ import annotations

import os

from quenching.session.transcript import encode_cwd


def worktree_gitdir(git_file: str) -> str | None:
    """The real gitdir a linked worktree's `.git` file points at."""
    try:
        with open(git_file, encoding="utf-8") as stream:
            value = next((line[len("gitdir:"):].strip()
                          for line in stream if line.startswith("gitdir:")), "")
    except OSError:
        return None
    if not value:
        return None
    if not os.path.isabs(value):
        value = os.path.join(os.path.dirname(git_file), value)
    return os.path.realpath(value)


def worktree_branch(gitdir: str) -> str | None:
    """The branch a gitdir's HEAD names, or None when HEAD is detached or unreadable."""
    try:
        with open(os.path.join(gitdir, "HEAD"), encoding="utf-8") as stream:
            head = stream.read().strip()
    except OSError:
        return None
    prefix = "ref: refs/heads/"
    return head[len(prefix):] if head.startswith(prefix) else None


def _checkout_root(start: str) -> str | None:
    """The nearest directory at or above `start` that holds a `.git` entry."""
    current = os.path.realpath(start)
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _common_dir(root: str) -> str | None:
    checkout = _checkout_root(root)
    if checkout is None:
        return None
    dot_git = os.path.join(checkout, ".git")
    if os.path.isdir(dot_git):
        return os.path.realpath(dot_git)
    gitdir = worktree_gitdir(dot_git)
    if not gitdir:
        return None
    try:
        with open(os.path.join(gitdir, "commondir"), encoding="utf-8") as stream:
            value = stream.read().strip()
    except OSError:
        return gitdir
    return os.path.realpath(value if os.path.isabs(value) else os.path.join(gitdir, value))


def _main_path(common: str, root: str) -> str | None:
    """The main checkout of a common dir: its parent when named `.git`, else `core.worktree`,
    else the checkout discovery started from when it holds the common dir itself."""
    if os.path.basename(common) == ".git":
        return os.path.dirname(common)
    try:
        with open(os.path.join(common, "config"), encoding="utf-8") as stream:
            for line in stream:
                key, _, value = line.partition("=")
                if key.strip() == "worktree" and value.strip():
                    return os.path.realpath(os.path.join(common, value.strip()))
    except OSError:
        pass
    checkout = _checkout_root(root)
    if checkout and worktree_gitdir(os.path.join(checkout, ".git")) == common:
        return checkout
    return None


def list_worktrees(root: str) -> list[dict]:
    """`[{path, branch, main}]` for the repository `root` belongs to, main worktree first.

    `root` may be the main checkout or any linked worktree. An entry whose gitdir file is
    unreadable or whose directory is gone is skipped: reporting orphans is `cq git stale`'s."""
    common = _common_dir(os.fspath(root))
    if not common:
        return []
    main = _main_path(common, os.fspath(root))
    found = [{"path": main, "branch": worktree_branch(common), "main": True}] if main else []
    base = os.path.join(common, "worktrees")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    linked = []
    for name in names:
        entry = os.path.join(base, name)
        try:
            with open(os.path.join(entry, "gitdir"), encoding="utf-8") as stream:
                pointer = stream.read().strip()
        except OSError:
            continue
        if not pointer:
            continue
        path = os.path.dirname(os.path.normpath(os.path.join(entry, pointer)))
        if os.path.isdir(path):
            linked.append({"path": path, "branch": worktree_branch(entry), "main": False})
    return found + sorted(linked, key=lambda row: row["path"])


def session_slug(path) -> str:
    """The `~/.claude/projects/` folder name for a cwd — the direct direction only."""
    return encode_cwd(path)


def with_slugs(worktrees: list[dict]) -> list[dict]:
    return [{**row, "slug": session_slug(row["path"])} for row in worktrees]
