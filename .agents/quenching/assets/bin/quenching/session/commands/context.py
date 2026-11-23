"""One live transcript's recorded context, never an estimate of the current turn."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from quenching.common.output import FINDINGS, OK, REFUSAL, emit, refuse
from quenching.session.commands.cache import opencode_db, opencode_requests
from quenching.session.commands.cost import _meta
from quenching.session.model import bare_agent
from quenching.session.transcript import resolve_transcript

TOKEN_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
BUDGETS = {"main": 200000, "worker": 200000, "conductor": 150000}
ROLES = {"spec-runner": "worker", "spec-orchestrator": "conductor", "orchestrator": "conductor",
         "autopilot": "conductor"}
OPENCODE_ID = "ses_"


def token_input(usage: dict) -> int | None:
    if not any(key in usage for key in TOKEN_KEYS):
        return None
    if any(type(usage.get(key, 0)) is not int or usage.get(key, 0) < 0 for key in TOKEN_KEYS):
        return None
    return sum(usage.get(key, 0) for key in TOKEN_KEYS)


def measure_context(path: Path, budgets: dict | None = None) -> dict:
    """Deduplicate streamed request usage; incomplete windows remain unknown."""
    requests, anomalies = {}, []
    with path.open(encoding="utf-8") as stream:
        for line, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                anomalies.append({"line": line, "code": "se-unparsed-line"})
                continue
            if not isinstance(record, dict):
                anomalies.append({"line": line, "code": "se-non-object-record"})
                continue
            if record.get("isCompactSummary") or record.get("type") != "assistant":
                continue
            msg = record.get("message")
            if not isinstance(msg, dict) or not isinstance(msg.get("id"), str) or not msg["id"]:
                anomalies.append({"line": line, "code": "se-no-message-id"})
                continue
            usage = msg.get("usage")
            # A streamed request can carry usage only in its final block. Reconcile below.
            previous = requests.get(msg["id"])
            if usage is None and previous is not None:
                continue
            if isinstance(usage, dict):
                output = usage.get("output_tokens", 0)
                if type(output) is not int or output < 0:
                    anomalies.append({"line": line, "code": "se-invalid-output-usage"})
                    continue
                if previous and isinstance(previous[0], dict):
                    old_output = previous[0].get("output_tokens", 0)
                    if type(old_output) is int and output < old_output:
                        continue
            requests[msg["id"]] = (usage, line)
    measured, exposure, covered = [], [], 0
    for usage, line in requests.values():
        inp = token_input(usage) if isinstance(usage, dict) else None
        if inp is None:
            anomalies.append({"line": line, "code": "se-invalid-input-usage"})
            continue
        exposure.append(inp)
        iterations = usage.get("iterations")
        if not isinstance(iterations, list):
            continue
        windows = []
        invalid = False
        for iteration in iterations:
            if not isinstance(iteration, dict):
                invalid = True
            elif iteration.get("type") == "message":
                window = token_input(iteration)
                if window is None:
                    invalid = True
                else:
                    windows.append(window)
        if windows and max(windows) > inp:
            invalid = True
        if invalid:
            anomalies.append({"line": line, "code": "se-invalid-executor-iteration"})
        if windows and not invalid:
            covered += 1
            measured.extend(windows)
    complete = bool(requests) and covered == len(requests) and not anomalies
    role = "main"
    agent_type = None
    if path.parent.name == "subagents" or path.stem.startswith("agent-"):
        agent_type = _meta(path).get("agentType")
        role = ROLES.get(bare_agent(agent_type), "unknown") \
            if isinstance(agent_type, str) else "unknown"
    peak = max(measured) if complete else None
    budget = (budgets or BUDGETS).get(role)
    return {"peakContext": peak, "peakUpperBound": max(exposure) if exposure else None,
            "observedIterationPeak": max(measured) if measured else None,
            "turns": len(requests), "measuredTurns": len(exposure),
            "integral": sum(exposure) if exposure else None,
            "role": role, "agentType": agent_type, "budget": budget,
            "remaining": budget - peak if budget is not None and peak is not None else None,
            "exceeded": peak > budget if budget is not None and peak is not None else None,
            "iterationCoverage": "complete" if complete else "partial" if covered else "none",
            "requestsWithIterations": covered, "anomalies": anomalies,
            "currentTurnIncluded": False, "measurement": "recorded-Claude-usage",
            "limitations": ["The request currently in flight is absent from this snapshot.",
                             "Request aggregates are exposure, not measured context windows."]}


def measure_opencode(db: Path, session: str, budgets: dict | None = None) -> dict:
    """One OpenCode session's recorded peak. A subagent is a session of its own, so no marker is needed."""
    anomalies: list = []
    requests = [r for r in opencode_requests(db, None, anomalies)[0] if r.session == session]
    role = "unknown"
    if requests:
        role = "main" if requests[0].role == "main" else ROLES.get(requests[0].agent, "unknown")
    windows = [r.input + r.read + r.write for r in requests]
    peak = max(windows) if windows else None
    budget = (budgets or BUDGETS).get(role)
    return {"peakContext": peak, "peakUpperBound": peak, "observedIterationPeak": peak,
            "turns": len(requests), "measuredTurns": len(requests),
            "integral": sum(windows) if windows else None,
            "role": role, "agentType": requests[0].agent if requests else None, "budget": budget,
            "remaining": budget - peak if budget is not None and peak is not None else None,
            "exceeded": peak > budget if budget is not None and peak is not None else None,
            "anomalies": anomalies, "currentTurnIncluded": False,
            "measurement": "recorded-OpenCode-usage",
            "limitations": ["The request currently in flight is absent from this snapshot."]}


def _latest_self_call(path: Path, pattern: re.Pattern) -> str | None:
    """The timestamp of the newest Bash call in `path` that invokes `session context --self`."""
    latest = None
    try:
        stream = path.open(encoding="utf-8")
    except OSError:
        return None
    with stream:
        for raw in stream:
            if "--self" not in raw:
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            msg = record.get("message") if isinstance(record, dict) else None
            content = msg.get("content") if isinstance(msg, dict) else None
            stamp = record.get("timestamp")
            if not isinstance(content, list) or not isinstance(stamp, str):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use" \
                        or block.get("name") != "Bash":
                    continue
                command = (block.get("input") or {}).get("command")
                if isinstance(command, str) and pattern.search(command) and (latest is None or stamp > latest):
                    latest = stamp
    return latest


def find_self(main: Path, marker: str, verb: str = "context",
              subagents_only: bool = False) -> tuple[Path | None, str]:
    """The transcript of this session whose latest `session <verb> … --self <marker>` call is the newest.

    The in-flight tool call is already recorded when its command runs, so the caller finds itself."""
    pattern = re.compile(r"session " + re.escape(verb) + r"\b.*--self[ =]['\"]?" + re.escape(marker) + r"['\"]?(\s|$)")
    candidates = [*([] if subagents_only else [main]),
                  *sorted((main.with_suffix("") / "subagents").glob("agent-*.jsonl"))]
    found = [(stamp, path) for path in candidates
             if (stamp := _latest_self_call(path, pattern)) is not None]
    if not found:
        return None, "se-self-not-found"
    newest = max(stamp for stamp, _ in found)
    paths = [path for stamp, path in found if stamp == newest]
    return (paths[0], "self marker") if len(paths) == 1 else (None, "se-self-ambiguous")


def effective_budgets(root) -> dict:
    """`shared.contextBudgets` over the code defaults; a bad leaf warns on stderr and is ignored."""
    from quenching.common.config import setting
    out = dict(BUDGETS)
    for role, default in BUDGETS.items():
        out[role], warning = setting(str(root), "shared", f"contextBudgets.{role}", default,
                                     lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
    return out


def _opencode(args, selector: str) -> int:
    session = args.agent or selector
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session):
        return refuse({"code": "se-invalid-agent-id", "message": "session id must be a bare identifier"}, args.json)
    db = opencode_db(None)
    if not db.is_file():
        return refuse({"code": "se-no-transcript", "message": f"no OpenCode database at {db}"}, args.json)
    try:
        out = measure_opencode(db, session, effective_budgets(Path.cwd()))
    except sqlite3.Error as error:
        return refuse({"code": "se-unreadable-transcript", "message": str(error)}, args.json)
    out.update(transcript=f"{db}#{session}", resolvedBy="OpenCode session id")
    return _report(args, out)


def _report(args, out: dict) -> int:
    inconclusive = out["peakContext"] is None or out["budget"] is None
    out["ok"] = not inconclusive
    if inconclusive:
        out.update(code="se-context-inconclusive", message=
                   "no reliable context verdict: inspect usage, iteration coverage and agent metadata")
    emit(args.json, out, f"context: peak={out['peakContext']} turns={out['turns']} "
         f"integral={out['integral']} role={out['role']} budget={out['budget']} "
         f"remaining={out['remaining']}" + (f"\n{out['message']}" if inconclusive else ""))
    return REFUSAL if inconclusive else FINDINGS if out["exceeded"] else OK


def cmd_context(args) -> int:
    # OpenCode first: it is the innermost shell when one harness is opened from the other.
    selector = (args.transcript or os.environ.get("OPENCODE_SESSION_ID")
                or os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if not selector:
        return refuse({"code": "se-no-live-session", "message":
                       "pass a transcript/session id or set OPENCODE_SESSION_ID or CLAUDE_CODE_SESSION_ID"},
                      args.json)
    if selector.startswith(OPENCODE_ID):
        return _opencode(args, selector)
    path, how = resolve_transcript(selector, Path.cwd().resolve())
    if path is None:
        return refuse({"code": "se-no-transcript", "message": how}, args.json)
    marker = getattr(args, "self_marker", None)
    if marker:
        path, found = find_self(path, marker)
        if path is None:
            return refuse({"code": found, "message":
                           f"no single transcript of this session carries `--self {marker}`"}, args.json)
        how += "; self marker"
    elif args.agent:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", args.agent):
            return refuse({"code": "se-invalid-agent-id", "message": "agent id must be a bare identifier"}, args.json)
        path = path.with_suffix("") / "subagents" / f"agent-{args.agent}.jsonl"
        how += "; agent id"
    try:
        out = measure_context(path, effective_budgets(Path.cwd()))
    except (OSError, UnicodeError) as error:
        return refuse({"code": "se-unreadable-transcript", "message": str(error)}, args.json)
    out.update(transcript=str(path), resolvedBy=how)
    return _report(args, out)
