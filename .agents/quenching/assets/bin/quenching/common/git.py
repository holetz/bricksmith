"""The two ways this tool asks git a question — one that reads absence as a fact,
and one that reads git's own message and exit code.

Moved verbatim out of the pre-refactor specs script."""
from __future__ import annotations

import os

COMMAND_TIMEOUT_S = 60


def _git(cwd: str, *argv: str) -> str:
    """Stdout of one git command, or "" for every way it can fail — no git on PATH, not a
    repo, a nonzero exit. Every caller treats absence as "this repo has no git facts",
    which is a real state and never an error."""
    import subprocess
    try:
        out = subprocess.run(["git", *argv], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=COMMAND_TIMEOUT_S,
                             cwd=cwd if os.path.isdir(cwd) else ".")
        return out.stdout if out.returncode == 0 else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


_REMOTE_URLS: dict[tuple[str, str], str] = {}


def remember_remote_url(repo: str, name: str, url: str) -> None:
    """Keep a remote's URL already read in this process, so the next reader spawns no git.
    An empty URL is not kept: absence is asked again, never assumed."""
    if url:
        _REMOTE_URLS[(os.path.abspath(repo), name)] = url


def remembered_remote_url(repo: str, name: str) -> str | None:
    """The URL `remember_remote_url` kept for this repository and remote, or None."""
    return _REMOTE_URLS.get((os.path.abspath(repo), name))


_ORIGIN_PREFIX = "refs/remotes/origin/"
_HEADS_PREFIX = "refs/heads/"


def origin_head_branch(cwd: str) -> str | None:
    """The branch `origin/HEAD` points at, whole — `refs/remotes/origin/team/main` is
    `team/main`. The prefix is stripped, never cut at the last `/`: a branch name may carry
    slashes. None when `origin/HEAD` is absent or points outside `origin`."""
    ref = _git(cwd, "symbolic-ref", "refs/remotes/origin/HEAD").strip()
    if not ref.startswith(_ORIGIN_PREFIX):
        return None
    return ref[len(_ORIGIN_PREFIX):] or None


def short_branch(name: str) -> str:
    """`refs/heads/<name>` as `<name>`, whole; a name without that prefix is returned as is."""
    return name[len(_HEADS_PREFIX):] if name.startswith(_HEADS_PREFIX) else name


def _git_run(cwd: str, *argv: str, stdin: str | None = None,
             env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Exit code, stdout AND stderr of one git command — the two halves `_git` throws away.

    A SIBLING of `_git`, never a change to it: every existing caller reads `""` as "this repo
    has no git facts", which is a real state and never an error. Creating a worktree needs the
    opposite reading — the difference between "the branch is not there" and "git could not
    answer" is what decides whether a branch gets created — and it needs git's own message to
    quote back in a refusal.

    A cwd that does not exist is `127` and no subprocess, where `_git` falls back to `"."`.
    That fallback is harmless when the answer is only ever read as a fact; here it would run
    `git worktree add` in whatever directory the process happens to sit in."""
    import subprocess
    if not os.path.isdir(cwd):
        return 127, "", f"not a directory: {cwd}"
    try:
        out = subprocess.run(["git", *argv], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=COMMAND_TIMEOUT_S,
                             cwd=cwd, input=stdin, env=env)
        return out.returncode, out.stdout, out.stderr
    except (OSError, ValueError, subprocess.SubprocessError) as e:   # noqa: BLE001
        return 127, "", str(e)
