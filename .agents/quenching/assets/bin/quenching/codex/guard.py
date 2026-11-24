"""The git guard: what a Codex executor run must leave as it found it, read without writing anything.

Codex runs with `--dangerously-bypass-approvals-and-sandbox`, so its bounds are proved by code
here, before and after the run. `refs` are the worktree's `HEAD`, branch, reflog length and stash
ref: the guard proves them unchanged in the executor's worktree, never a neighbouring worktree's
`HEAD` or a ref of the shared repository, which a parallel worker moves legitimately. The one
shared thing it does watch is the base checkout (the main worktree of `git worktree list`) and the
primary branch ref: `base_state` and `base_moves` accept a fast-forward there (the new value
descends from the old one) and bar a rewind or rewrite, a switch of the base `HEAD`'s branch, a
detached base `HEAD` and the removal of the ref. `touched` is every path `git status` reports, so
the executor can prove its diff stays inside the task's `files:`. A git command of the base read, a
read of the worktree's refs, or a `git status` that fails, is `GuardError`: the guard fails closed.
"""
from __future__ import annotations

import subprocess

from quenching.common.paths import native_path

BYPASS = "--dangerously-bypass-approvals-and-sandbox"
SECRET_NAMES = ("GH_TOKEN", "GITHUB_TOKEN")
SECRET_PREFIXES = ("AWS_", "AZURE_", "GOOGLE_")


def clean_env(env: dict) -> dict:
    """The environment a bypassed Codex gets: no forge token and no cloud credential."""
    return {k: v for k, v in env.items()
            if k not in SECRET_NAMES and not k.startswith(SECRET_PREFIXES)}


def git(worktree: str, *args: str) -> str:
    result = subprocess.run(["git", "-C", worktree, *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    return result.stdout if result.returncode == 0 else ""


class GuardError(Exception):
    """A git command the guard depends on failed, so nothing can be proved."""


def git_strict(worktree: str, *args: str) -> str:
    result = subprocess.run(["git", "-C", worktree, *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode != 0:
        raise GuardError(f"git {' '.join(args)} failed ({result.returncode}): "
                         f"{result.stderr.strip()}")
    return result.stdout


def status(worktree: str) -> list[tuple[str, str]]:
    """Every `(XY, path)` `git status` reports, rename sources included. A failing `git status`
    is `GuardError`, never an empty tree."""
    entries = git_strict(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0")
    out: list[tuple[str, str]] = []
    source = None
    for entry in entries:
        if source is not None:
            out.append((source, entry))
            source = None
            continue
        if len(entry) > 3:
            out.append((entry[:2], entry[3:]))
            source = entry[:2] if "R" in entry[:2] or "C" in entry[:2] else None
    return out


def touched(worktree: str) -> set[str]:
    return {path for _, path in status(worktree)}


def _state(worktree: str, *args: str) -> str:
    """Strict read of a query whose exit 1 with no message means "absent" (a detached HEAD has no
    symbolic ref, a repository without a stash has no `refs/stash`): that is state, not failure."""
    result = subprocess.run(["git", "-C", worktree, *args], capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode == 1 and not result.stderr.strip():
        return ""
    if result.returncode != 0:
        raise GuardError(f"git {' '.join(args)} failed ({result.returncode}): "
                         f"{result.stderr.strip()}")
    return result.stdout


def refs(worktree: str) -> dict:
    """The refs of this worktree Codex must not move. Fails closed: a detached `HEAD` and an
    absent stash read as empty, and any other failing read is `GuardError`."""
    return {"head": git_strict(worktree, "rev-parse", "HEAD").strip(),
            "branch": _state(worktree, "symbolic-ref", "-q", "HEAD").strip(),
            "reflogLength": len(git_strict(worktree, "reflog", "--format=%H").splitlines()),
            "stash": _state(worktree, "rev-parse", "-q", "--verify", "refs/stash").strip()}


def _ancestor(worktree: str, old: str, new: str) -> bool:
    """Whether `new` descends from (or equals) `old`; any git failure other than "no" is fatal."""
    result = subprocess.run(["git", "-C", worktree, "merge-base", "--is-ancestor", old, new],
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode not in (0, 1):
        raise GuardError(f"git merge-base --is-ancestor failed ({result.returncode}): "
                         f"{result.stderr.strip()}")
    return result.returncode == 0


def _primary(worktree: str, base: str) -> str:
    """The primary branch ref: `origin/HEAD`'s target, else the base checkout's branch, else
    `main` or `master`, whichever exists."""
    head = _state(worktree, "symbolic-ref", "-q", "refs/remotes/origin/HEAD").strip()
    if head.startswith("refs/remotes/origin/"):
        return "refs/heads/" + head[len("refs/remotes/origin/"):]
    branch = _state(base, "symbolic-ref", "-q", "HEAD").strip()
    if branch:
        return branch
    for name in ("main", "master"):
        if _state(worktree, "rev-parse", "-q", "--verify", f"refs/heads/{name}").strip():
            return f"refs/heads/{name}"
    return ""


def base_state(worktree: str, primary: str | None = None) -> dict:
    """The base checkout's `HEAD` and branch and the primary branch ref's value. Fails closed."""
    listing = git_strict(worktree, "worktree", "list", "--porcelain")
    base = native_path(next((line[len("worktree "):] for line in listing.splitlines()
                             if line.startswith("worktree ")), ""))
    if not base:
        raise GuardError("git worktree list names no base checkout")
    primary = primary if primary is not None else _primary(worktree, base)
    return {"path": base, "primary": primary,
            "head": _state(base, "rev-parse", "-q", "--verify", "HEAD").strip(),
            "branch": _state(base, "symbolic-ref", "-q", "HEAD").strip(),
            "primaryRef": (_state(worktree, "rev-parse", "-q", "--verify", primary).strip()
                           if primary else "")}


def base_moves(worktree: str, before: dict, after: dict) -> list[str]:
    """What moved in the base checkout or the primary ref that is not a fast-forward."""
    out: list[str] = []
    if before["branch"] != after["branch"]:
        out.append("base branch switched" if after["branch"] else "base HEAD detached")
    elif not after["branch"] and before["head"] != after["head"]:
        out.append("base HEAD detached")
    if before["head"] != after["head"] and after["head"] and before["head"] and \
            not _ancestor(worktree, before["head"], after["head"]):
        out.append("base HEAD rewound")
    if before["primaryRef"]:
        if not after["primaryRef"]:
            out.append("primary ref removed")
        elif before["primaryRef"] != after["primaryRef"] and \
                not _ancestor(worktree, before["primaryRef"], after["primaryRef"]):
            out.append("primary ref rewound")
    return out
