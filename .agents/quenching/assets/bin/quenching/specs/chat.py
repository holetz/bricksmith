"""The portal's chat job: one read-only `claude -p` per question, polled by the page.

The argv is fixed (`chat_argv`): sonnet, `Read`/`Grep`/`Glob` only, no MCP, no session
persistence, no slash commands, `dontAsk`, only the project's setting source, a dollar ceiling and
a temporary settings file (`deny_settings`) that denies reading the secrets and disables every
hook. The question goes through stdin, never the argv. What the page sees is rebuilt by `project`
field by field: assistant text, the NAME of each tool used, and the final result — never a
`tool_result` and never a tool `input`.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time

CHAT_TIMEOUT_SECONDS = 300.0
DEFAULT_BUDGET_USD = 0.50
MAX_QUESTION = 4000
MAX_EVENTS = 500
MAX_CHARS = 200_000
KEEP_JOBS = 20
TOOLS = "Read,Grep,Glob"
DENIED_PATHS = ("~/.ssh/**", "~/.config/**", "//**/*.key", "//**/.env",
                "**/.env.*", "//**/.env.*")


def deny_settings() -> dict:
    """The `--settings` payload: read denies (Claude Code applies `Read` rules to `Grep` and
    `Glob` too) and no hooks at all."""
    return {"permissions": {"deny": [f"Read({path})" for path in DENIED_PATHS]},
            "disableAllHooks": True}


def chat_argv(claude: str, budget: float, settings: str, model: str = "sonnet") -> list[str]:
    return [claude, "-p", "--model", model, "--output-format", "stream-json", "--verbose",
            "--tools", TOOLS, "--strict-mcp-config", "--no-session-persistence",
            "--disable-slash-commands", "--permission-mode", "dontAsk",
            "--setting-sources", "project", "--max-budget-usd", f"{budget:g}",
            "--settings", settings]


def project(event: object) -> list[dict]:
    """The page-safe events one `stream-json` line yields; anything else yields nothing."""
    if not isinstance(event, dict):
        return []
    if event.get("type") == "assistant":
        content = (event.get("message") or {}).get("content")
        out = []
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                out.append({"kind": "text", "text": block["text"]})
            elif block.get("type") == "tool_use" and isinstance(block.get("name"), str):
                out.append({"kind": "tool", "name": block["name"]})
        return out
    if event.get("type") == "result":
        cost = event.get("total_cost_usd")
        return [{"kind": "result",
                 "text": event["result"] if isinstance(event.get("result"), str) else "",
                 "costUsd": cost if isinstance(cost, (int, float)) else None,
                 "isError": bool(event.get("is_error"))}]
    return []


def find_claude() -> str | None:
    return shutil.which("claude")


class Job:
    """One question: the process, its reader thread, its timer, and the projected events."""

    def __init__(self, claude: str, budget: float, cwd: str, question: str,
                 timeout: float = CHAT_TIMEOUT_SECONDS, model: str = "sonnet") -> None:
        self.id = secrets.token_urlsafe(12)
        self.cwd, self.question = cwd, question
        self.status, self.error, self.cost = "running", "", None
        self.events: list[dict] = []
        self.chars = 0
        self.started = time.time()
        self.lock = threading.Lock()
        fd, self.settings = tempfile.mkstemp(prefix="quenching-chat-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:  # mkstemp creates it 0600
            json.dump(deny_settings(), handle)
        try:
            self.proc = subprocess.Popen(
                chat_argv(claude, budget, self.settings, model), cwd=cwd, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                errors="replace")
        except OSError as exc:
            os.unlink(self.settings)
            self.status, self.error = "failed", f"claude did not start: {exc}"
            self.proc = None
            return
        self.timer = threading.Timer(timeout, self._timeout)
        self.timer.daemon = True
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.timer.start()
        self.thread.start()

    def _timeout(self) -> None:
        with self.lock:
            if self.status == "running":
                self.status = "timeout"
        try:
            self.proc.kill()
        except OSError:
            pass

    def _run(self) -> None:
        try:
            self.proc.stdin.write(self.question)
            self.proc.stdin.close()
        except OSError:
            pass
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            for out in project(event):
                self._add(out)
        stderr = self.proc.stderr.read()
        self.proc.stdout.close()
        self.proc.stderr.close()
        code = self.proc.wait()
        self.timer.cancel()
        try:
            os.unlink(self.settings)
        except OSError:
            pass
        with self.lock:
            if self.status != "running":
                return
            result = next((e for e in reversed(self.events) if e["kind"] == "result"), None)
            if code == 0 and result and not result["isError"]:
                self.status = "done"
            else:
                self.status = "failed"
                self.error = (stderr.strip().splitlines() or [f"claude exited {code}"])[-1][:500]

    def _add(self, event: dict) -> None:
        with self.lock:
            size = len(event.get("text", ""))
            if len(self.events) >= MAX_EVENTS or self.chars + size > MAX_CHARS:
                return
            self.chars += size
            if event["kind"] == "result":
                self.cost = event["costUsd"]
            self.events.append(event)

    @property
    def running(self) -> bool:
        return self.status == "running"

    def view(self, after: int = 0) -> dict:
        with self.lock:
            after = max(0, min(after, len(self.events)))
            return {"ok": True, "job": self.id, "status": self.status, "error": self.error,
                    "costUsd": self.cost, "events": list(self.events[after:]),
                    "next": len(self.events)}
