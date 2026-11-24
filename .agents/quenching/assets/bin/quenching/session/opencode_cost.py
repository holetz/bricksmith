"""What an OpenCode session's tree spent, per agent and model, read from its SQLite database.

OpenCode keeps one row per session in `session_v2`, and a subagent is a child session (`parent_id`).
Each row carries the totals of all its requests (`tokens_*`), so the tree is one read-only query
away: no request is replayed, so a request count and a context window are not known — those
columns stay `null` rather than carrying an invented number.

The root is the session asked for, labelled `main`; every descendant is an agent, keyed by its agent
name and model. Tokens are never summed across models: the payload has no grand total.

Reuses the reader `cache` and `digest` own (`opencode_db`, `_tables`, `_sessions`, `current_session`):
no second access layer to the database."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from quenching.session.commands.cache import _sessions, _tables, opencode_db
from quenching.session.model import bare_agent
from quenching.session.opencode_transcript import OPENCODE_ID, SESSION_ID, OpenCodeError, current_session

UNKNOWN = "unknown"
ORCHESTRATORS = ("spec-orchestrator", "orchestrator")
COLUMNS = ("id, parent_id, agent, model, tokens_input, tokens_cache_read, tokens_cache_write, "
           "tokens_output, tokens_reasoning, time_created")


def wants_opencode_cost(arg: str | None, explicit_db: str | None) -> bool:
    """Whether `cost` is asked about an OpenCode tree: a session id, a database, or the running session."""
    if arg:
        return arg.startswith(OPENCODE_ID)
    return bool(explicit_db or os.environ.get("OPENCODE_SESSION_ID"))


def _model(raw) -> str:
    """`session_v2.model` is `{"id": …, "providerID": …}`; the id names the tier."""
    if not isinstance(raw, str) or not raw:
        return UNKNOWN
    try:
        data = json.loads(raw)
    except ValueError:
        return raw
    found = data.get("id") if isinstance(data, dict) else None
    return found if isinstance(found, str) and found else UNKNOWN


def _n(value) -> int:
    return value if isinstance(value, int) else 0


def read_tree(arg: str | None, cwd: Path, explicit_db: str | None) -> tuple[dict, str, str, str]:
    """(sessions by id, database path, how the root was found, root id). Raises `OpenCodeError`."""
    path = opencode_db(explicit_db)
    if not path.is_file():
        raise OpenCodeError("se-no-transcript", f"no OpenCode database at {path}")
    selector = arg or os.environ.get("OPENCODE_SESSION_ID")
    if selector and not SESSION_ID.fullmatch(selector):
        raise OpenCodeError("se-invalid-agent-id", "session id must be a bare identifier")
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = _tables(db)
            if "session_v2" not in tables:
                raise OpenCodeError("se-no-transcript", f"{path} has no OpenCode 2 session table "
                                    "(session_v2) — this reader knows no other generation")
            known = _sessions(db, tables)
            if selector:
                how = "OpenCode session id" if arg else "OPENCODE_SESSION_ID"
            else:
                selector = current_session(db, known, cwd)
                how = "most recent OpenCode session in this directory"
            if not selector or selector not in known:
                raise OpenCodeError("se-no-transcript",
                                    f"no OpenCode session {selector or 'in ' + str(cwd)} in {path}")
            sessions = {row[0]: {"parent": row[1], "agent": row[2], "model": _model(row[3]),
                                 "input": _n(row[4]), "cacheRead": _n(row[5]), "cacheWrite": _n(row[6]),
                                 "output": _n(row[7]) + _n(row[8]), "created": _n(row[9])}
                        for row in db.execute(f"SELECT {COLUMNS} FROM session_v2")}  # noqa: S608 — fixed names
        finally:
            db.close()
    except sqlite3.Error as error:
        raise OpenCodeError("se-unreadable-transcript", f"{path}: {error}") from error
    return sessions, str(path), how, selector


def _depths(sessions: dict, root: str) -> dict:
    """Session id -> its depth under `root` (the root is 0), every descendant transitively."""
    children: dict = {}
    for sid, row in sessions.items():
        children.setdefault(row["parent"], []).append(sid)
    depths, queue = {root: 0}, [root]
    while queue:
        sid = queue.pop()
        for child in children.get(sid, []):
            if child not in depths:
                depths[child] = depths[sid] + 1
                queue.append(child)
    return depths


def aggregate(sessions: dict, root: str, descendants_of: str | None = None, nested: bool = False,
              since_ms: int | None = None, latest: bool = False) -> dict:
    depths = _depths(sessions, root)
    if latest:
        found = [sid for sid in depths if sid != root
                 and bare_agent(sessions[sid]["agent"] or "") in ORCHESTRATORS]
        if not found:
            return {"groups": [], "byModel": [], "anomalies": [], "noOrchestrator": True}
        descendants_of = max(found, key=lambda sid: sessions[sid]["created"])
    if descendants_of:
        if descendants_of not in depths:
            return {"groups": [], "byModel": [], "anomalies": [], "unknownScope": descendants_of}
        keep = [sid for sid in _depths(sessions, descendants_of) if sid != descendants_of]
    elif nested:
        keep = [sid for sid, depth in depths.items() if depth >= 2]
    else:
        keep = list(depths)
    groups: dict = {}
    for sid in keep:
        row = sessions[sid]
        if since_ms is not None and row["created"] < since_ms:
            continue
        agent = "main" if sid == root else bare_agent(row["agent"] or UNKNOWN)
        g = groups.setdefault((agent, row["model"]), {
            "agentType": agent, "model": row["model"], "sessions": 0, "requests": None, "input": 0,
            "cacheRead": 0, "cacheWrite": 0, "output": 0, "maxWindow": None})
        g["sessions"] += 1
        for key in ("input", "cacheRead", "cacheWrite", "output"):
            g[key] += row[key]
    rows = sorted(groups.values(), key=lambda g: (g["agentType"], g["model"]))
    models: dict = {}
    for g in rows:
        m = models.setdefault(g["model"], {"model": g["model"], "sessions": 0, "requests": None, "input": 0,
                                           "cacheRead": 0, "cacheWrite": 0, "output": 0})
        for key in ("sessions", "input", "cacheRead", "cacheWrite", "output"):
            m[key] += g[key]
    return {"groups": rows, "byModel": sorted(models.values(), key=lambda m: m["model"]),
            "agents": [], "overBudget": [], "anomalies": []}
