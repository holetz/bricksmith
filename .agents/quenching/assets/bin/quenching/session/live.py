"""Live Claude Code sessions and their sub-agents, read with a FIXED field list.

Reads `<claude_root>/sessions/<pid>.json` and the session's transcript under
`<projects>/<slug>/`. Nothing is copied by key: every output record is built field by field
from the names below, so a field Claude Code adds tomorrow cannot leak. The reader never opens
a `*.key` file and never returns `messagingSocketPath`, a `tool_result` or a raw tool `input`;
a pending question carries only the question texts of `AskUserQuestion` (or `plan` for
`ExitPlanMode`), clipped to `QUESTION_CLIP` characters.

Fixed output, per session:
  sessionId, pid, cwd, status, statusSince, lastEvent, state, pendingQuestion,
  subagents[{agentType, start, end, active}], activity[60] (events per minute, oldest first)
`state` is `waiting` (pending question), `busy` or `idle`.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from quenching.session.transcript import encode_cwd

WINDOW_MINUTES = 60
TAIL_BYTES = 512 * 1024
ACTIVE_SECONDS = 120
QUESTION_CLIP = 500
ASKING = ("AskUserQuestion", "ExitPlanMode")


def _alive(pid) -> bool:
    if os.name == "nt":
        return _alive_nt(int(pid))
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return False
    return True


class _Win32:
    """As três chamadas de kernel32 que `_alive_nt` precisa, isoladas para o teste."""

    STILL_ACTIVE = 259
    ACCESS_DENIED = 5
    QUERY_LIMITED_INFORMATION = 0x1000

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE,
                                                     ctypes.POINTER(wintypes.DWORD)]
        self._kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def open(self, pid: int):
        return self._kernel32.OpenProcess(self.QUERY_LIMITED_INFORMATION, False, pid)

    def exit_code(self, handle) -> int | None:
        code = self._wintypes.DWORD()
        if not self._kernel32.GetExitCodeProcess(handle, self._ctypes.byref(code)):
            return None
        return code.value

    def close(self, handle) -> None:
        self._kernel32.CloseHandle(handle)

    def last_error(self) -> int:
        return self._ctypes.get_last_error()


def _alive_nt(pid: int, api: _Win32 | None = None) -> bool:
    """Consulta o processo pela Win32, sem `tasklist` e sem criar processo filho.

    Args:
        pid: O processo a consultar.
        api: A camada Win32; o teste injeta uma falsa para rodar fora do Windows.

    Returns:
        ``True`` enquanto o processo existe; acesso negado conta como vivo, o lado conservador.
    """
    api = api or _Win32()
    handle = api.open(pid)
    if not handle:
        return api.last_error() == _Win32.ACCESS_DENIED
    try:
        code = api.exit_code(handle)
        return True if code is None else code == _Win32.STILL_ACTIVE
    finally:
        api.close(handle)


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _from_millis(value):
    """A UTC datetime from epoch milliseconds, or None for anything out of range or not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _valid_identity(info: dict) -> bool:
    pid = info.get("pid")
    return (isinstance(pid, int) and not isinstance(pid, bool)
            and isinstance(info.get("sessionId"), str) and isinstance(info.get("cwd"), str))


def _iso(moment) -> str | None:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def _tail_events(path: Path) -> tuple[list[dict], int]:
    """The parsed JSON objects of the file's last `TAIL_BYTES`, and how many lines failed."""
    try:
        with open(path, "rb") as stream:
            size = stream.seek(0, os.SEEK_END)
            start = max(0, size - TAIL_BYTES)
            stream.seek(start)
            data = stream.read()
    except OSError:
        return [], 0
    lines = data.splitlines()
    if start > 0 and lines:
        lines = lines[1:]
    events, bad = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events, bad


def _first_ts(path: Path):
    try:
        with open(path, "rb") as stream:
            for line in stream:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                moment = _parse_ts(obj.get("timestamp")) if isinstance(obj, dict) else None
                if moment:
                    return moment
    except OSError:
        pass
    return None


def _blocks(event: dict) -> list[dict]:
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _pending(events: list[dict]) -> tuple[str, str] | None:
    """(tool name, text) of the last asking tool call with no result yet, else None."""
    asked: dict[str, tuple[str, str]] = {}
    answered: set[str] = set()
    for event in events:
        for block in _blocks(event):
            if block.get("type") == "tool_use" and block.get("name") in ASKING:
                asked[str(block.get("id"))] = (str(block.get("name")), _question_text(block))
            elif block.get("type") == "tool_result":
                answered.add(str(block.get("tool_use_id")))
    open_ids = [key for key in asked if key not in answered]
    return asked[open_ids[-1]] if open_ids else None


def _question_text(block: dict) -> str:
    if block.get("name") == "ExitPlanMode":
        return "plan"
    given = block.get("input")
    questions = given.get("questions") if isinstance(given, dict) else None
    texts = [q["question"] for q in questions
             if isinstance(q, dict) and isinstance(q.get("question"), str)] \
        if isinstance(questions, list) else []
    return " | ".join(texts)[:QUESTION_CLIP]


def _bucket(events: list[dict], now: datetime, activity: list[int]) -> datetime | None:
    last = None
    for event in events:
        moment = _parse_ts(event.get("timestamp"))
        if not moment:
            continue
        if last is None or moment > last:
            last = moment
        if event.get("type") in ("assistant", "user"):
            slot = WINDOW_MINUTES - 1 - int((now - moment).total_seconds() // 60)
            if 0 <= slot < WINDOW_MINUTES and moment <= now:
                activity[slot] += 1
    return last


def _subagents(folder: Path, now: datetime, activity: list[int]) -> list[dict]:
    rows = []
    for path in sorted(folder.glob("**/agent-*.jsonl")):
        events, _ = _tail_events(path)
        end = _bucket(events, now, activity)
        start = _first_ts(path)
        if end is None or (now - end).total_seconds() > WINDOW_MINUTES * 60:
            continue
        agent_type = None
        try:
            meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
            if isinstance(meta, dict) and isinstance(meta.get("agentType"), str):
                agent_type = meta["agentType"]
        except (OSError, ValueError):
            pass
        rows.append({"agentType": agent_type, "start": _iso(start), "end": _iso(end),
                     "active": (now - end).total_seconds() < ACTIVE_SECONDS})
    return rows


def _session_row(info: dict, projects: Path, now: datetime) -> dict:
    cwd = info.get("cwd")
    session_id = info.get("sessionId")
    status = info.get("status") if isinstance(info.get("status"), str) else None
    since = _from_millis(info.get("statusUpdatedAt"))
    activity = [0] * WINDOW_MINUTES
    events, bad, subagents = [], 0, []
    if isinstance(cwd, str) and isinstance(session_id, str):
        folder = projects / encode_cwd(cwd)
        events, bad = _tail_events(folder / f"{session_id}.jsonl")
        subagents = _subagents(folder / session_id / "subagents", now, activity)
    last = _bucket(events, now, activity)
    pending = _pending(events)
    tool, question = pending if pending else (None, None)
    state = "waiting" if pending is not None else "busy" if status == "busy" else "idle"
    return {"sessionId": session_id, "pid": info.get("pid"), "cwd": cwd, "status": status,
            "statusSince": _iso(since), "lastEvent": _iso(last), "state": state,
            "pendingQuestion": question, "pendingTool": tool, "subagents": subagents, "activity": activity,
            "unparsed": bad}


def read_live(claude_root, projects=None, now=None, cwds=None, alive=_alive) -> dict:
    """The fixed-field view of every live session; `cwds` keeps only sessions under them."""
    root = Path(claude_root)
    projects = Path(projects) if projects else root / "projects"
    now = now or datetime.now(timezone.utc)
    keep = [os.path.realpath(c) for c in cwds] if cwds is not None else None
    rows = []
    for path in sorted((root / "sessions").glob("*.json")):
        try:
            info = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(info, dict) or not _valid_identity(info) or not alive(info["pid"]):
            continue
        cwd = info.get("cwd")
        if keep is not None:
            real = os.path.realpath(cwd) if isinstance(cwd, str) else None
            if real is None or not any(real == k or real.startswith(k + os.sep) for k in keep):
                continue
        rows.append(_session_row(info, projects, now))
    return {"generatedAt": _iso(now), "windowMinutes": WINDOW_MINUTES, "sessions": rows}
