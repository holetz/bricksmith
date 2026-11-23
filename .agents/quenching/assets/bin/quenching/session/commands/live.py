"""`live` — the live Claude Code sessions, fixed fields only (see `quenching.session.live`)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from quenching.common.output import OK, refuse
from quenching.session.worktrees import list_worktrees
from quenching.session.live import read_live
from quenching.session.transcript import projects_root


def _projects(configured, root: Path) -> Path:
    """Transcripts live beside the sessions when only the Claude root is configured."""
    if configured and not os.environ.get("QUENCHING_TRANSCRIPTS_ROOT"):
        return root / "projects"
    return projects_root()


def cmd_live(args) -> int:
    configured = os.environ.get("QUENCHING_CLAUDE_ROOT")
    root = Path(configured) if configured else Path.home() / ".claude"
    if not (root / "sessions").is_dir():
        return refuse({"code": "se-no-sessions", "message": f"no sessions directory at {root}"},
                      args.json)
    cwds = None
    if args.cwd:
        cwds = [row["path"] for row in list_worktrees(args.cwd)] or [args.cwd]
    out = read_live(root, projects=_projects(configured, root), cwds=cwds)
    out["ok"] = True
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for row in out["sessions"]:
            print(f"{row['sessionId']}  {row['state']:<8} {row['cwd']}")
            if row["pendingQuestion"]:
                print(f"  ? {row['pendingQuestion']}")
        print(f"{len(out['sessions'])} live session(s)")
    return OK
