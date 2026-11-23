"""Where a session transcript lives, and how a cwd is spelled to find it.

Moved verbatim out of the pre-refactor session script."""
from __future__ import annotations

import os
from pathlib import Path

# Claude Code encodes a project's cwd by replacing \ / : . with '-'. chr(92) IS the
# backslash, spelled this way so no quoting layer can eat the escape. Same rule as
# /quenching-knowledge import-memory uses to find `memory/` — one encoding, not two.
PUNCT = set(chr(92) + "/:.")


def encode_cwd(path) -> str:
    return "".join("-" if c in PUNCT else c for c in str(path))


def projects_root() -> Path:
    """Return an explicitly configured transcript root or the active platform's root."""
    configured = os.environ.get("QUENCHING_TRANSCRIPTS_ROOT")
    if configured:
        return Path(configured).expanduser()
    # Codex writes rollouts under sessions/AAAA/MM/DD/, not projects/.
    roots = (Path.home() / ".claude" / "projects", Path.home() / ".codex" / "sessions")
    return next((root for root in roots if root.is_dir()), roots[0])


def resolve_project_dir(cwd: Path) -> tuple[Path | None, str]:
    """The transcript directory for `cwd`, nearest ancestor first.

    A git worktree or a subdirectory has no directory of its own, and falls back to the
    checkout it was cut from — the same nearest-first walk /quenching-knowledge import-memory does.
    """
    root = projects_root()
    if not root.is_dir():
        return None, f"no transcript root at {root}"
    # .lower(): a Windows drive letter's case is not stable (c:\ vs C:\); the rest is exact.
    have = {d.name.lower(): d for d in root.glob("*") if d.is_dir()}
    for p in (cwd, *cwd.parents):
        hit = have.get(encode_cwd(p).lower())
        if hit is not None:
            return hit, "exact" if p == cwd else f"ancestor {p}"
    return None, f"no transcript directory for {encode_cwd(cwd)}"


def resolve_transcript(arg: str | None, cwd: Path) -> tuple[Path | None, str]:
    """An explicit path, a bare session id, or the most recent session for `cwd`."""
    if arg:
        p = Path(arg).expanduser()
        if p.is_file():
            return p, "explicit path"
        d, how = resolve_project_dir(cwd)
        if d is not None:
            byid = d / f"{arg}.jsonl"
            if byid.is_file():
                return byid, f"session id in {how} project dir"
        # A sibling worktree has no project dir of its own and no ancestor that is the checkout's,
        # so a session id is also looked up across every project dir: ids are unique.
        root = projects_root()
        hits = sorted(root.glob(f"*/{arg}.jsonl")) if root.is_dir() and "/" not in arg else []
        if len(hits) == 1:
            return hits[0], "session id across project dirs"
        if len(hits) > 1:
            return None, f"session id {arg} is ambiguous across project dirs"
        return None, f"no transcript at {arg}"
    d, how = resolve_project_dir(cwd)
    if d is None:
        return None, how
    files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return None, f"no transcript files in {d}"
    return files[0], f"most recent in {how} project dir"
