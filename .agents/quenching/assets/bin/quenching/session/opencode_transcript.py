"""An OpenCode session, reshaped into the JSON lines `parse.read_lines` already reads.

OpenCode keeps its sessions in one SQLite database, not in a JSONL file per session. This module
opens it read-only through the reader `cache` already owns (`opencode_db`, `_tables`, `_sessions`,
`_inside`) and emits one Claude-shaped record per user turn and per tool call, so the digest's
parser, its attribution rules and its honesty flags apply unchanged.

OpenCode has no `attributionSkill`. The mark it leaves is the `skill` tool call: a record is
attributed to the skill most recently entered before it, and the call itself to its caller — the
same "most-recently-entered pointer" the Claude parser documents, with the same unclosed-stage
honesty. Only what identifies a target is emitted (a path, a read window, a shell command, a skill
id); a tool's payload and result never leave the database.

An assistant message keeps its blocks inline (`session_message.data.content`) once finished and in
`session_part` while it is in flight, so a message with no inline blocks is read from its parts: the
session still running is the one the digest is asked about. OpenCode 1's `message`/`part` tables
are not read; `read_opencode` says so instead of reporting an empty session.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from quenching.session.commands.cache import _inside, _sessions, _tables, opencode_db

OPENCODE_ID = "ses_"
SESSION_ID = re.compile(r"[A-Za-z0-9_-]+")
# OpenCode tool name -> the Claude Code name the parser counts under.
TOOLS = {"read": "Read", "view": "Read", "edit": "Edit", "write": "Write", "patch": "Edit",
         "shell": "Bash", "bash": "Bash", "powershell": "Bash", "skill": "Skill"}


class OpenCodeError(Exception):
    """A refusal the digest turns into `{code, message}`."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def wants_opencode(arg: str | None) -> bool:
    """Whether the digest is asked about an OpenCode session: its id, or the running session's."""
    if arg:
        return arg.startswith(OPENCODE_ID)
    return bool(os.environ.get("OPENCODE_SESSION_ID"))


def _stamp(ms) -> str | None:
    try:
        return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _bare(name: str) -> str:
    """`mcp__opencode__read` is the `read` tool behind a bridge."""
    return (name or "").rpartition("__")[2].lower()


def _target(inp: dict) -> str | None:
    found = inp.get("path") or inp.get("filePath") or inp.get("file_path")
    return found if isinstance(found, str) and found else None


def _tool_use(block: dict) -> tuple[dict, str | None]:
    """The Claude `tool_use` block for one OpenCode tool entry, and the skill it entered, if any."""
    state = block.get("state")
    inp = state.get("input") if isinstance(state, dict) else None
    inp = inp if isinstance(inp, dict) else {}
    raw = block.get("name") or block.get("tool") or ""
    name = TOOLS.get(_bare(raw), raw)
    kept: dict = {}
    entered = None
    if name in ("Read", "Edit", "Write") and _target(inp):
        kept["file_path"] = _target(inp)
        if name == "Read":
            kept.update({k: inp[k] for k in ("offset", "limit") if k in inp})
    elif name == "Bash" and isinstance(inp.get("command"), str):
        kept["command"] = inp["command"]
    elif name == "Skill":
        entered = inp.get("id") or inp.get("name") or inp.get("skill")
        entered = entered.strip() if isinstance(entered, str) and entered.strip() else None
        if entered:
            kept["skill"] = entered
            if isinstance(inp.get("args"), str):
                kept["args"] = inp["args"]
    return {"type": "tool_use", "name": name, "input": kept}, entered


def _parts(db: sqlite3.Connection, sid: str) -> dict:
    """message id -> its tool/text blocks from `session_part`, for the messages still in flight."""
    out: dict = {}
    for mid, raw in db.execute("SELECT message_id, data FROM session_part WHERE session_id = ? "
                               "ORDER BY message_id, ordinal", (sid,)):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("type") == "tool":
            out.setdefault(mid, []).append(data)
    return out


def _lines(db: sqlite3.Connection, sid: str, directory: str | None, parts: dict):
    pointer = None                 # the skill most recently entered
    yield json.dumps({"type": "system", "sessionId": sid, "cwd": directory})
    query = ("SELECT id, type, data FROM session_message WHERE session_id = ? "
             "AND type IN ('user', 'assistant') ORDER BY seq")
    for mid, kind, raw in db.execute(query, (sid,)):
        try:
            data = json.loads(raw)
        except ValueError:
            yield raw if isinstance(raw, str) else ""      # counted by the parser as unparsed
            continue
        if not isinstance(data, dict):
            yield raw
            continue
        when = _stamp((data.get("time") or {}).get("created"))
        if kind == "user":
            text = data.get("text")
            if isinstance(text, str) and text.strip():
                yield json.dumps({"type": "user", "uuid": mid, "timestamp": when,
                                  "attributionSkill": pointer, "message": {"content": text}})
            continue
        content = data.get("content")
        blocks = content if isinstance(content, list) and content else parts.get(mid, [])
        for number, block in enumerate(b for b in blocks if isinstance(b, dict) and b.get("type") == "tool"):
            tool_use, entered = _tool_use(block)
            yield json.dumps({"type": "assistant", "uuid": block.get("id") or f"{mid}:{number}",
                              "timestamp": _stamp((block.get("time") or {}).get("created")) or when,
                              "attributionSkill": pointer, "message": {"content": [tool_use]}})
            if entered:
                pointer = entered


def current_session(db: sqlite3.Connection, sessions: dict, cwd: Path) -> str | None:
    """The newest root session that ran in `cwd` or beneath it."""
    for (sid,) in db.execute("SELECT id FROM session_v2 WHERE parent_id IS NULL "
                             "ORDER BY time_updated DESC"):
        if sid in sessions and _inside(sid, sessions, cwd):
            return sid
    return None


def read_opencode(arg: str | None, cwd: Path, explicit_db: str | None = None) -> tuple[list, str, str, str]:
    """(JSON lines, label, how, session id) for the session `arg` names, the running one, or the
    newest in `cwd`. Raises `OpenCodeError` for every reason there is nothing honest to read."""
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
            sessions = _sessions(db, tables)
            if not {"session_v2", "session_message"} <= tables:
                raise OpenCodeError("se-no-transcript", f"{path} has no OpenCode 2 session tables "
                                    "(session_v2, session_message) — this reader knows no other generation")
            if selector:
                how = "OpenCode session id" if arg else "OPENCODE_SESSION_ID"
            else:
                selector = current_session(db, sessions, cwd)
                how = "most recent OpenCode session in this directory"
            if not selector or selector not in sessions:
                raise OpenCodeError("se-no-transcript", f"no OpenCode session {selector or 'in ' + str(cwd)} in {path}")
            _, _, directory = sessions[selector]
            lines = list(_lines(db, selector, directory, _parts(db, selector)))
        finally:
            db.close()
    except sqlite3.Error as error:
        raise OpenCodeError("se-unreadable-transcript", f"{path}: {error}") from error
    return lines, f"{path}#{selector}", how, selector
