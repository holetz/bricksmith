"""Whether the Codex quota allows a call right now — read-only, and failing closed.

Primary source: the `account/rateLimits/read` JSON-RPC method of `codex app-server` over stdio,
under a deadline. Fallback, only when the app-server does not answer: the last `token_count`
event in `$CODEX_HOME/sessions/**/rollout-*.jsonl`. `auth.json` is never opened.

    available   (exit 0)  the quota is under every configured ceiling
    unavailable (exit 1)  a known fact forbids the call: ceiling reached, usage not allowed,
                          or no `codex` on PATH
    unknown     (exit 2)  no source could be read, or the only snapshot is stale

The app-server starts no thread and no turn, so it runs nothing. Codex's native sandbox is never
asked for anywhere in this pillar: the executor runs with the bypass, bounded by the git guard
(`quenching.codex.guard`).
"""
from __future__ import annotations

import contextlib
import glob
import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import tempfile
import time

APP_SERVER_DEADLINE = 15
APP_SERVER_ARGV = ("app-server",)
EXIT = {"available": 0, "unavailable": 1, "unknown": 2}
WINDOWS = ("primary", "secondary")


def codex_home(env: dict) -> str:
    return env.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")


def group_kwargs() -> dict:
    """`Popen` keywords that put the child in a group of its own, so `stop` can end its tree."""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def stop(process: subprocess.Popen) -> None:
    """Kill the whole process tree: a hung Codex may have children of its own."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    process.wait()


def _rpc(process: subprocess.Popen, lines: queue.Queue, deadline: float, payload: dict) -> dict:
    process.stdin.write(json.dumps(payload) + "\n")
    process.stdin.flush()
    if "id" not in payload:
        return {}
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no answer to {payload['method']}")
        line = lines.get(timeout=remaining)
        if line is None:
            raise ConnectionError(f"app-server closed before answering {payload['method']}")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and message.get("id") == payload["id"]:
            if "error" in message:
                error = message["error"]
                detail = error.get("message") if isinstance(error, dict) else error
                raise RuntimeError(f"{payload['method']}: {detail}")
            result = message.get("result")
            if result is None:
                return {}
            if not isinstance(result, dict):
                raise RuntimeError(f"{payload['method']}: result is not an object")
            return result


def _with_stderr(message: str, process: subprocess.Popen, errors) -> str:
    """Append the last stderr line the app-server wrote, so a refusal is not silent."""
    stop(process)
    errors.seek(0)
    last = [line.strip() for line in errors.read().splitlines() if line.strip()]
    return f"{message} (stderr: {last[-1]})" if last else message


def read_app_server(binary: str, seconds: float,
                    env: dict | None = None) -> tuple[dict | None, str | None]:
    """Return (`GetAccountRateLimitsResponse`, None) or (None, why it could not be read)."""
    errors = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    try:
        process = subprocess.Popen([binary, *APP_SERVER_ARGV], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8", errors="replace",
                                   env=env, **group_kwargs())
    except OSError as exc:
        errors.close()
        return None, f"app-server did not start: {exc}"
    lines: queue.Queue = queue.Queue()

    def pump() -> None:
        for line in process.stdout:
            lines.put(line)
        lines.put(None)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    deadline = time.monotonic() + seconds
    try:
        _rpc(process, lines, deadline, {"id": 1, "method": "initialize",
                                        "params": {"clientInfo": {"name": "cq", "version": "1"}}})
        _rpc(process, lines, deadline, {"method": "initialized"})
        return _rpc(process, lines, deadline, {"id": 2, "method": "account/rateLimits/read"}), None
    except queue.Empty:
        return None, _with_stderr(f"app-server did not answer within {seconds:g}s", process, errors)
    except (TimeoutError, ConnectionError, RuntimeError, OSError) as exc:
        return None, _with_stderr(f"app-server: {exc}", process, errors)
    finally:
        stop(process)
        reader.join(timeout=1)
        errors.close()
        for stream in (process.stdin, process.stdout):
            with contextlib.suppress(OSError):
                stream.close()


def _window(raw, used: str, minutes: str, resets: str) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get(used), (int, float)):
        return None
    return {"usedPercent": raw[used], "windowMinutes": raw.get(minutes),
            "resetsAt": raw.get(resets)}


def from_app_server(result: dict) -> dict:
    limits = result.get("rateLimits") if isinstance(result.get("rateLimits"), dict) else {}
    return {"ordinaryUsageAllowed": result.get("ordinaryUsageAllowed"),
            "rateLimitReachedType": limits.get("rateLimitReachedType"),
            **{w: _window(limits.get(w), "usedPercent", "windowDurationMins", "resetsAt")
               for w in WINDOWS}}


def read_sessions(home: str) -> tuple[dict | None, str | None]:
    """Return the newest `token_count` rate-limit snapshot on disk, normalized, and its file."""
    paths = sorted(glob.glob(os.path.join(home, "sessions", "*", "*", "*", "rollout-*.jsonl")),
                   reverse=True)
    for path in paths:
        latest = None
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"token_count"' not in line:
                    continue
                try:
                    payload = json.loads(line).get("payload") or {}
                except (json.JSONDecodeError, AttributeError):
                    continue
                if payload.get("type") == "token_count" and isinstance(
                        payload.get("rate_limits"), dict):
                    latest = payload["rate_limits"]
        if latest is not None:
            return {"ordinaryUsageAllowed": None,
                    "rateLimitReachedType": latest.get("rate_limit_reached_type"),
                    **{w: _window(latest.get(w), "used_percent", "window_minutes", "resets_at")
                       for w in WINDOWS}}, path
    return None, None


def judge(snapshot: dict, ceilings: dict, *, authoritative: bool, now: float) -> tuple[str, str]:
    """Turn a normalized snapshot into (state, reason)."""
    allowed = snapshot.get("ordinaryUsageAllowed")
    if authoritative and allowed is None:
        return "unknown", "app-server reports ordinaryUsageAllowed as unavailable"
    if allowed is False:
        return "unavailable", "ordinary usage is not allowed for this account"
    if snapshot.get("rateLimitReachedType"):
        return "unavailable", f"rate limit reached: {snapshot['rateLimitReachedType']}"
    windows = {w: snapshot[w] for w in WINDOWS if snapshot.get(w)}
    if not windows:
        return "unknown", "no rate-limit window reported"
    if not authoritative:
        stale = [w for w, data in windows.items()
                 if not isinstance(data.get("resetsAt"), (int, float)) or data["resetsAt"] <= now]
        if stale:
            return "unknown", f"session snapshot is stale for {', '.join(stale)}"
    over = [f"{w} {data['usedPercent']:g}% >= {ceilings[w]:g}%" for w, data in windows.items()
            if data["usedPercent"] >= ceilings[w]]
    if over:
        return "unavailable", "over the ceiling: " + "; ".join(over)
    return "available", "; ".join(f"{w} {data['usedPercent']:g}% < {ceilings[w]:g}%"
                                  for w, data in windows.items())


def probe(settings: dict, env: dict | None = None, now: float | None = None) -> dict:
    env = os.environ if env is None else env
    now = time.time() if now is None else now
    out = {"state": "unknown", "reason": "", "source": None, "rateLimits": None,
           "thresholds": settings["maxUsedPercent"], "appServerError": None,
           "sessionFile": None}
    binary = shutil.which("codex", path=env.get("PATH"))
    if binary is None:
        out.update(state="unavailable", reason="codex is not on PATH")
        return out
    result, error = read_app_server(binary, min(APP_SERVER_DEADLINE, settings["timeoutSeconds"]),
                                    dict(env))
    if result is not None:
        snapshot = from_app_server(result)
        state, reason = judge(snapshot, settings["maxUsedPercent"], authoritative=True, now=now)
        out.update(state=state, reason=reason, source="app-server", rateLimits=snapshot)
        return out
    out["appServerError"] = error
    snapshot, path = read_sessions(codex_home(env))
    if snapshot is None:
        out["reason"] = f"{error}; no token_count snapshot in sessions"
        return out
    state, reason = judge(snapshot, settings["maxUsedPercent"], authoritative=False, now=now)
    out.update(state=state, reason=reason, source="sessions", rateLimits=snapshot,
               sessionFile=path)
    return out
