"""The live-session reader `cq` lends the specs portal for `cq specs serve --sessions`."""
from __future__ import annotations

import os
from pathlib import Path

from quenching.session.commands.live import _projects
from quenching.session.live import read_live
from quenching.session.worktrees import list_worktrees


def reader(claude_root: Path, projects: Path):
    """`read(root) -> (live, worktrees)` over one Claude root, for the portal's projection."""
    def read(root: str) -> tuple[dict, list[dict]]:
        trees = list_worktrees(root) or [{"path": root, "branch": None, "main": True}]
        live = read_live(claude_root, projects=projects, cwds=[w["path"] for w in trees])
        return live, trees
    return read


def source() -> tuple[dict | None, dict | None]:
    """`({"read"}, None)`, or `(None, refusal)` when there is no sessions directory."""
    configured = os.environ.get("QUENCHING_CLAUDE_ROOT")
    claude_root = Path(configured) if configured else Path.home() / ".claude"
    if not (claude_root / "sessions").is_dir():
        return None, {"code": "se-no-sessions", "exit": 2,
                      "message": f"no sessions directory at {claude_root}"}
    return {"read": reader(claude_root, _projects(configured, claude_root))}, None
