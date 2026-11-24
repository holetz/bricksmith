"""`cache --inflow` — what entered the context, by origin, and how much of it was read again later.

A tool result enters the context once and is then re-read from the cache at every later request of
its session, so its weight is `tokens x requests left`. Only sizes (characters / 4, the estimate
`skillListing` uses), tool names, file paths and the `cq components read` target are taken from a
record; the content itself is never kept or printed. Claude Code reads `tool_use`/`tool_result`
blocks, OpenCode the tool entries of `session_message`."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

CQ_READ = re.compile(r"components\s+read\s+(?:--\S+\s+)*[\"']?([^\s\"']+)")
READ_TOOLS = {"read", "view"}
SHELL_TOOLS = {"bash", "shell", "powershell"}


def _tail(path: str) -> str:
    path = path.replace("\\", "/")
    for marker in ("/references/", "/skills/", "/standards/"):
        at = path.rfind(marker)
        if at >= 0:
            return path[at + 1:]
    return "/".join(path.split("/")[-2:])


def origin(name: str, args: object) -> tuple:
    """(kind, key) of a tool call: where its result came from."""
    args = args if isinstance(args, dict) else {}
    tool = (name or "unknown").lower()
    bridged = tool.rpartition("__")[2]      # `mcp__opencode__read` is the read tool behind a bridge
    if bridged in READ_TOOLS or bridged in SHELL_TOOLS:
        tool = bridged
    if tool in SHELL_TOOLS:
        match = CQ_READ.search(str(args.get("command") or ""))
        if match:
            part = "sections" if "--sections" in str(args.get("command")) else "whole"
            return f"cq-read-{part}", _tail(match[1])
        return "shell", "shell"
    path = args.get("path") or args.get("filePath") or args.get("file_path")
    if tool in READ_TOOLS and isinstance(path, str):
        norm = _tail(path)
        return ("reference" if "references/" in norm or "skills/" in norm else "code"), norm
    if tool.startswith("mcp") or tool in ("execute", "search"):
        return "mcp", tool
    return "tool", tool


def _size(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(b.get("text") or "")) for b in content if isinstance(b, dict))
    return 0


class Tally:
    def __init__(self):
        self.rows: dict = {}

    def add(self, harness: str, calls: list, steps: int) -> None:
        """`calls` are (origin, chars, step the result landed at); `steps` is the session's request count."""
        for (kind, key), chars, at in calls:
            tokens = chars // 4
            row = self.rows.setdefault((harness, kind, key), {
                "harness": harness, "kind": kind, "origin": key, "calls": 0, "tokens": 0, "reread": 0})
            row["calls"] += 1
            row["tokens"] += tokens
            row["reread"] += tokens * max(0, steps - at)

    def top(self, limit: int) -> list:
        return sorted(self.rows.values(), key=lambda r: (-r["reread"], r["harness"], r["origin"]))[:limit]


def claude_file(path: Path, anomalies: list) -> tuple:
    """(calls, steps) of one transcript; a request is a distinct `message.id`."""
    seen: set = set()
    pending: dict = {}
    calls: list = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    anomalies.append({"file": path.name, "line": n, "message": "unparsable record"})
                    continue
                msg = rec.get("message") if isinstance(rec, dict) else None
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                if rec.get("type") == "assistant":
                    seen.add(msg.get("id") or rec.get("requestId") or rec.get("uuid"))
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            pending[block.get("id")] = origin(block.get("name"), block.get("input"))
                elif rec.get("type") == "user":
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result" \
                                and block.get("tool_use_id") in pending:
                            calls.append((pending.pop(block["tool_use_id"]), _size(block.get("content")),
                                          len(seen)))
    except OSError:
        pass
    return calls, len(seen)


def opencode_calls(db: sqlite3.Connection, keep, since_ms: int | None, anomalies: list) -> dict:
    """session id -> (calls, steps) from the v2 `session_message` assistant rows `keep` accepts."""
    out: dict = {}
    query = "SELECT session_id, data FROM session_message WHERE type = 'assistant' ORDER BY session_id, seq"
    for sid, raw in db.execute(query):
        if not keep(sid):
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            anomalies.append({"file": "opencode", "line": 0, "message": f"unparsable message in {sid}"})
            continue
        created = (data.get("time") or {}).get("created") if isinstance(data, dict) else None
        if since_ms is not None and not (isinstance(created, (int, float)) and created >= since_ms):
            continue
        calls, steps = out.setdefault(sid, ([], 0))
        steps += 1
        for block in (data.get("content") if isinstance(data.get("content"), list) else []):
            state = block.get("state") if isinstance(block, dict) else None
            if isinstance(state, dict) and block.get("type") == "tool":
                calls.append((origin(block.get("name"), state.get("input")), _size(state.get("content")), steps))
        out[sid] = (calls, steps)
    return out
