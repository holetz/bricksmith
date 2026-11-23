"""`cost` — what a session's subagents spent, per agent type, model and window.

Reads `<session>/subagents/agent-*.jsonl`, with the agent type taken from the
`agent-*.meta.json` beside each. The Claude Code transcript writes one record per content
block and repeats the same `message.id` with a growing `usage`, so the unit counted here is
the request (one `message.id`), never the line, and the request keeps its final usage.

Tokens are never summed across models: tiers price differently, so the totals are per model
and the payload has no grand total."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from quenching.common.output import FINDINGS, OK, refuse
from quenching.session.model import bare_agent
from quenching.session.transcript import resolve_project_dir, resolve_transcript

UNKNOWN = "unknown"


def subagents_dir(arg: str | None) -> tuple[Path | None, str]:
    """The `subagents/` directory for an explicit directory, or the session `resolve_transcript` finds."""
    if arg:
        p = Path(arg).expanduser()
        if p.is_dir():
            sub = p / "subagents"
            return (sub if sub.is_dir() else p), "explicit directory"
    if not arg:
        project, _ = resolve_project_dir(Path.cwd().resolve())
        if project is not None:
            live = sorted(f.stem for f in project.glob("*.jsonl") if (f.with_suffix("") / "subagents").is_dir())
            if len(live) > 1:
                return None, ("several sessions have subagents here, pass the session id of the one to "
                              "cost: " + ", ".join(live))
            if len(live) == 1:
                current, _ = resolve_transcript(None, Path.cwd().resolve())
                if current is not None and current.stem != live[0]:
                    return None, (f"the most recent session {current.stem} has no subagents yet; "
                                  f"pass the session id to cost another one: {live[0]}")
                return project / live[0] / "subagents", "the only session with subagents in the project dir"
    path, how = resolve_transcript(arg, Path.cwd().resolve())
    if path is None:
        return None, how
    return path.with_suffix("") / "subagents", how


def _agent_type(transcript: Path) -> str:
    meta = transcript.with_name(transcript.stem + ".meta.json")
    try:
        kind = json.loads(meta.read_text(encoding="utf-8")).get("agentType")
    except (OSError, ValueError, AttributeError):
        return UNKNOWN
    return kind if isinstance(kind, str) and kind else UNKNOWN


def _meta(transcript: Path) -> dict:
    meta = transcript.with_name(transcript.stem + ".meta.json")
    try:
        data = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _agent_id(transcript: Path) -> str:
    return transcript.stem.removeprefix("agent-")


def scoped(transcripts: list, descendants_of: str | None, nested: bool) -> list:
    """The transcripts inside the scope: children of `descendants_of` (transitively), or every
    agent that has a `parentAgentId`. No scope keeps them all."""
    if not descendants_of and not nested:
        return transcripts
    parent = {}
    for tr in transcripts:
        pid = _meta(tr).get("parentAgentId")
        parent[_agent_id(tr)] = pid if isinstance(pid, str) and pid else None
    if nested:
        return [tr for tr in transcripts if parent[_agent_id(tr)]]

    def inside(agent: str) -> bool:
        seen = set()
        while agent in parent and agent not in seen:
            seen.add(agent)
            agent = parent[agent]
            if agent == descendants_of:
                return True
        return False
    return [tr for tr in transcripts if inside(_agent_id(tr))]


def _started(transcript: Path) -> tuple:
    """When an agent began: its earliest record timestamp (file mtime breaks a tie or fills a gap)."""
    first = None
    try:
        with transcript.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                when = parse_instant(rec.get("timestamp")) if isinstance(rec, dict) else None
                if when is not None and (first is None or when < first):
                    first = when
    except OSError:
        pass
    floor = datetime.min.replace(tzinfo=timezone.utc)
    try:
        mtime = transcript.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (first or floor, mtime)


def latest_orchestrator(transcripts: list) -> str | None:
    """The agent id of the most recently started `spec-orchestrator` (or the older `orchestrator`) (orchestrators run one at a time)."""
    found = [tr for tr in transcripts if bare_agent(_agent_type(tr)) in ("spec-orchestrator", "orchestrator")]
    return _agent_id(max(found, key=_started)) if found else None


def parse_instant(value) -> datetime | None:
    """An ISO-8601 instant as an aware datetime (`Z` accepted; naive reads as UTC), else None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        when = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _requests(transcript: Path, anomalies: list) -> dict:
    """message.id -> (model, usage, timestamp, line) with the usage of the largest `output_tokens` seen."""
    seen: dict = {}
    with transcript.open(encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                anomalies.append({"file": transcript.name, "line": n, "message": "unparsable record"})
                continue
            if not isinstance(rec, dict):
                anomalies.append({"file": transcript.name, "line": n, "message": "record is not a JSON object"})
                continue
            msg = rec.get("message") if rec.get("type") == "assistant" else None
            usage = msg.get("usage") if isinstance(msg, dict) else None
            if not isinstance(usage, dict):
                continue
            key = msg.get("id") or rec.get("requestId") or rec.get("uuid")
            if key is None:
                continue
            prev = seen.get(key)
            if prev is None or (usage.get("output_tokens") or 0) >= (prev[1].get("output_tokens") or 0):
                model = msg.get("model")
                seen[key] = (model if isinstance(model, str) and model else UNKNOWN, usage,
                             parse_instant(rec.get("timestamp")), n)
    return seen


def _n(usage: dict, key: str) -> int:
    v = usage.get(key)
    return v if isinstance(v, int) else 0


def aggregate(directory: Path, descendants_of: str | None = None, nested: bool = False,
              since: datetime | None = None, latest: bool = False) -> dict:
    anomalies: list = []
    groups: dict = {}
    if latest:
        descendants_of = latest_orchestrator(sorted(directory.glob("agent-*.jsonl")))
        if descendants_of is None:
            return {"groups": [], "byModel": [], "anomalies": [], "noOrchestrator": True}
    transcripts = scoped(sorted(directory.glob("agent-*.jsonl")), descendants_of, nested)
    for tr in transcripts:
        agent = _agent_type(tr)
        for model, usage, when, line in _requests(tr, anomalies).values():
            if since is not None:
                if when is None:
                    anomalies.append({"file": tr.name, "line": line,
                                      "message": "request has no parsable timestamp, not counted under --since"})
                    continue
                if when < since:
                    continue
            g = groups.setdefault((agent, model), {
                "agentType": agent, "model": model, "requests": 0, "input": 0,
                "cacheRead": 0, "cacheWrite": 0, "output": 0, "maxWindow": 0})
            inp, cr, cw = _n(usage, "input_tokens"), _n(usage, "cache_read_input_tokens"), \
                _n(usage, "cache_creation_input_tokens")
            g["requests"] += 1
            g["input"] += inp
            g["cacheRead"] += cr
            g["cacheWrite"] += cw
            g["output"] += _n(usage, "output_tokens")
            g["maxWindow"] = max(g["maxWindow"], inp + cr + cw)
    rows = sorted(groups.values(), key=lambda g: (g["agentType"], g["model"]))
    models: dict = {}
    for g in rows:
        m = models.setdefault(g["model"], {"model": g["model"], "requests": 0, "input": 0,
                                           "cacheRead": 0, "cacheWrite": 0, "output": 0})
        for k in ("requests", "input", "cacheRead", "cacheWrite", "output"):
            m[k] += g[k]
    agents = agent_peaks(transcripts)
    return {"groups": rows, "byModel": sorted(models.values(), key=lambda m: m["model"]),
            "agents": agents, "overBudget": [a["agentId"] for a in agents if a["exceeded"]],
            "anomalies": anomalies}


def price_rows(out: dict) -> None:
    """`--usd`: each group and model row at list price; a cache write here carries no lifetime, so
    it prices at the 5-minute floor. An unpriced model is `null`, never a neighbour's rate."""
    from quenching.session import pricing
    for row in [*out["groups"], *out["byModel"]]:
        row["usd"] = pricing.usd(row["model"], input=row["input"], cache_read=row["cacheRead"],
                                 write_other=row["cacheWrite"], output=row["output"])
    out["pricing"] = {"sourceDate": pricing.table()["sourceDate"],
                      "basis": "list price estimate; cache writes at the 5-minute rate"}


def agent_peaks(transcripts: list) -> list:
    """Each agent's recorded context peak against its role budget, whole transcript.

    `exceeded` is None where no reliable peak or no role budget exists: unmeasured, never within."""
    from quenching.session.commands.context import measure_context  # context imports this module
    rows = []
    for tr in transcripts:
        try:
            m = measure_context(tr)
        except (OSError, UnicodeError):
            m = {"agentType": _agent_type(tr), "role": "unknown", "peakContext": None,
                 "budget": None, "exceeded": None}
        rows.append({"agentId": _agent_id(tr), "agentType": m["agentType"], "role": m["role"],
                     "peakContext": m["peakContext"], "budget": m["budget"], "exceeded": m["exceeded"]})
    return rows


def cmd_cost(args) -> int:
    directory, how = subagents_dir(args.transcript)
    if directory is None:
        code = ("se-ambiguous-session" if how.startswith("several sessions")
                else "se-no-subagents" if how.startswith("the most recent session") else "se-no-transcript")
        return refuse({"code": code, "message": how}, args.json)
    if not directory.is_dir():
        return refuse({"code": "se-no-subagents", "message": f"no subagents directory at {directory}"},
                      args.json)
    since = None
    if getattr(args, "since", None):
        since = parse_instant(args.since)
        if since is None:
            return refuse({"code": "se-bad-since",
                           "message": f"--since is not an ISO-8601 instant: {args.since}"}, args.json)
    descendants_of = getattr(args, "descendants_of", None)
    marker = getattr(args, "self_marker", None)
    if marker:
        from quenching.session.commands.context import find_self  # context imports this module
        found, code = find_self(Path(str(directory.parent) + ".jsonl"), marker, "cost", subagents_only=True)
        if found is None:
            return refuse({"code": code, "message":
                           f"no single subagent transcript under {directory} carries `session cost --self {marker}`"},
                          args.json)
        descendants_of = _agent_id(found)
    out = aggregate(directory, descendants_of, getattr(args, "nested", False), since,
                    getattr(args, "latest_orchestrator", False))
    if out.get("noOrchestrator"):
        return refuse({"code": "se-no-orchestrator",
                       "message": f"--latest-orchestrator: no orchestrator agent under {directory}"}, args.json)
    if not out["groups"]:
        return refuse({"code": "se-no-requests",
                       "message": f"no subagent request with usage under {directory}"}, args.json)
    out.update(ok=True, subagents=str(directory), resolvedBy=how)
    if getattr(args, "usd", False):
        price_rows(out)
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"subagents: {directory}  ({how})")
        print(f"{'agentType':<22}{'model':<26}{'req':>6}{'input':>9}{'cacheRead':>12}"
              f"{'cacheWrite':>12}{'output':>9}{'maxWindow':>11}")
        for g in out["groups"]:
            print(f"{g['agentType']:<22}{g['model']:<26}{g['requests']:>6}{g['input']:>9}"
                  f"{g['cacheRead']:>12}{g['cacheWrite']:>12}{g['output']:>9}{g['maxWindow']:>11}")
        print("\nper model (tiers are never summed together):")
        for m in out["byModel"]:
            print(f"  {m['model']}: req={m['requests']} input={m['input']} cacheRead={m['cacheRead']}"
                  f" cacheWrite={m['cacheWrite']} output={m['output']}")
        for a in out["anomalies"]:
            print(f"  ! {a['file']} line {a['line']}: {a['message']}")
    return FINDINGS if out["anomalies"] else OK
