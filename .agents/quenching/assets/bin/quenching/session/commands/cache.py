"""`cache` — the prompt cache a harness actually delivered, in one shape for Claude Code and OpenCode.

Claude Code keeps JSONL transcripts; OpenCode keeps one SQLite database, opened read-only here.
Both reduce to the same request record, so a cold request means the same thing on both: at least
`COLD_FLOOR` tokens of context with more than `COLD_SHARE` of them missed. Claude reports a miss as
a cache write and DeepSeek as plain input, so `missed` is their sum.

The output is token counts only unless `--usd` asks: no price is applied by default, because the
gateway's rate is not in either store, and tokens are never summed across models. `--usd` adds each
row's list-price estimate from quenching's own table (`session/pricing.json`), `null` for a model
the table does not price. `--session` narrows the scope to one session (the `quenching-cache` bar's
"this chat"). Message content is never selected."""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from quenching.common.output import FINDINGS, OK, refuse
from quenching.session import pricing
from quenching.session.commands.cost import _agent_type, _n, _requests, parse_instant
from quenching.session.transcript import projects_root, resolve_project_dir

COLD_FLOOR = 5000
COLD_SHARE = 0.5
# A boundary needs this many requests in its bucket: one idle turn is not a cache lifetime.
BOUNDARY_MIN = 10
GAPS = ((2, "<2m"), (5, "2-5m"), (10, "5-10m"), (30, "10-30m"), (60, "30-60m"), (None, ">60m"))
SKILL_KEY = "core/skill-guidance"
# Claude Code records its own local notices as assistant turns; no model answered them.
SYNTHETIC = "<synthetic>"


class Req(NamedTuple):
    harness: str
    session: str
    role: str
    agent: str
    model: str
    at: datetime | None
    input: int
    read: int
    write: int
    write5m: int
    write1h: int
    output: int


def bare(agent: str) -> str:
    return agent.rsplit(":", 1)[-1].removeprefix("quenching-")


def bucket(minutes: float) -> str:
    return next(label for limit, label in GAPS if limit is None or minutes < limit)


def _claude_file(path: Path, role: str, agent: str, anomalies: list) -> list:
    out = []
    for model, usage, when, _ in _requests(path, anomalies).values():
        if model == SYNTHETIC:
            continue
        created = usage.get("cache_creation")
        created = created if isinstance(created, dict) else {}
        out.append(Req("claude", str(path), role, agent, model, when, _n(usage, "input_tokens"),
                       _n(usage, "cache_read_input_tokens"), _n(usage, "cache_creation_input_tokens"),
                       _n(created, "ephemeral_5m_input_tokens"), _n(created, "ephemeral_1h_input_tokens"),
                       _n(usage, "output_tokens")))
    return out


def claude_requests(directory: Path | None, anomalies: list) -> tuple:
    """Every request under the project directories in scope; `directory` None means all of them."""
    if directory is None:
        root = projects_root()
        dirs = sorted(d for d in root.glob("*") if d.is_dir()) if root.is_dir() else []
        source = str(root)
    else:
        found, how = resolve_project_dir(directory)
        if found is None:
            return [], None, how
        dirs, source = [found], str(found)
    reqs: list = []
    for project in dirs:
        for main in project.glob("*.jsonl"):
            reqs += _claude_file(main, "main", "main", anomalies)
        for sub in project.glob("*/subagents/agent-*.jsonl"):
            reqs += _claude_file(sub, "subagent", bare(_agent_type(sub)), anomalies)
    return reqs, source, None


def opencode_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    configured = os.environ.get("QUENCHING_OPENCODE_DB")
    if configured:
        return Path(configured).expanduser()
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data) / "opencode" / "opencode.db"


def _tables(db: sqlite3.Connection) -> set:
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _sessions(db: sqlite3.Connection, tables: set) -> dict:
    """session id -> (parent id, agent, directory), both generations of the store."""
    found: dict = {}
    for table in ("session", "session_v2"):
        if table in tables:
            for sid, parent, agent, directory in db.execute(
                    f"SELECT id, parent_id, agent, directory FROM {table}"):  # noqa: S608 — fixed names
                found[sid] = (parent, agent, directory)
    return found


def _inside(sid: str, sessions: dict, directory: Path | None) -> bool:
    """A subagent belongs to the directory of its root session, wherever it ran."""
    if directory is None:
        return True
    seen = set()
    while sid in sessions and sid not in seen:
        seen.add(sid)
        parent, _, where = sessions[sid]
        here = Path(where) if where else None
        if here is not None and (here == directory or directory in here.parents):
            return True
        sid = parent
    return False


TOKENS = ("json_extract(data, '$.tokens.input'), json_extract(data, '$.tokens.cache.read'), "
          "json_extract(data, '$.tokens.cache.write'), json_extract(data, '$.tokens.output'), "
          "json_extract(data, '$.tokens.reasoning')")
QUERIES = {
    # OpenCode 2 keeps one row per step; a compaction is a request of its own.
    "session_message": f"SELECT session_id, json_extract(data, '$.time.created'), "
                       f"json_extract(data, '$.model.id'), {TOKENS} FROM session_message "
                       "WHERE type IN ('assistant', 'compaction') "
                       "AND json_type(data, '$.tokens') = 'object' ORDER BY session_id, seq",
    "message": f"SELECT session_id, json_extract(data, '$.time.created'), "
               f"json_extract(data, '$.modelID'), {TOKENS} FROM message "
               "WHERE json_extract(data, '$.role') = 'assistant' "
               "AND json_type(data, '$.tokens') = 'object' ORDER BY session_id, time_created",
}


def opencode_requests(path: Path, directory: Path | None, anomalies: list) -> tuple:
    """Requests in scope and the skill listing each session in scope was given."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = _tables(db)
        sessions = _sessions(db, tables)
        kept = [t for t in QUERIES if t in tables]
        if not sessions or not kept:
            anomalies.append({"file": path.name, "line": 0,
                              "message": "no OpenCode session tables this reader knows"})
            return [], []
        reqs = []
        for table in kept:
            for sid, created, model, inp, read, write, output, reasoning in db.execute(QUERIES[table]):
                if sid not in sessions or not _inside(sid, sessions, directory):
                    continue
                parent, agent, _ = sessions[sid]
                when = datetime.fromtimestamp(created / 1000, timezone.utc) if created else None
                reqs.append(Req("opencode", sid, "subagent" if parent else "main",
                                bare(agent or "unknown") if parent else "main", model or "unknown", when,
                                inp or 0, read or 0, write or 0, 0, 0, (output or 0) + (reasoning or 0)))
        return reqs, _listings(db, tables, sessions, directory)
    finally:
        db.close()


def _listings(db: sqlite3.Connection, tables: set, sessions: dict, directory: Path | None) -> list:
    if not {"instruction_state", "instruction_blob"} <= tables:
        return []
    blobs = dict(db.execute("SELECT hash, value FROM instruction_blob"))
    rows = []
    for sid, current in db.execute("SELECT session_id, current_values FROM instruction_state"):
        if sid not in sessions or not _inside(sid, sessions, directory):
            continue
        try:
            entries = json.loads(blobs.get(json.loads(current).get(SKILL_KEY)) or "null")
        except (ValueError, TypeError, AttributeError):
            continue
        if isinstance(entries, list) and all(isinstance(e, dict) for e in entries):
            parent, agent, _ = sessions[sid]
            chars = sum(len(str(e.get("name", ""))) + len(str(e.get("description", ""))) for e in entries)
            rows.append(("subagent" if parent else "main", bare(agent or "unknown") if parent else "main",
                         len(entries), chars))
    return rows


def _median(values: list) -> int | None:
    return int(statistics.median(values)) if values else None


def _context(r: Req) -> int:
    return r.input + r.read + r.write


def request_usd(r: Req) -> float | None:
    """One request at list price; a cache write the record did not split prices at 5 minutes."""
    return pricing.usd(r.model, input=r.input, cache_read=r.read, write5m=r.write5m, write1h=r.write1h,
                       write_other=max(0, r.write - r.write5m - r.write1h), output=r.output,
                       prompt_tokens=_context(r))


def summarize(reqs: list, priced: bool = False) -> tuple:
    """Rows per harness/model/role/agent, gap histogram per harness/model/role, cold boundaries."""
    by_session: dict = {}
    for r in reqs:
        by_session.setdefault((r.harness, r.session, r.model), []).append(r)
    rows: dict = {}
    gaps: dict = {}
    for (harness, _, model), own in by_session.items():
        own.sort(key=lambda r: (r.at is None, r.at or datetime.min.replace(tzinfo=timezone.utc)))
        head = own[0]
        row = rows.setdefault((harness, model, head.role, head.agent), {
            "harness": harness, "model": model, "role": head.role, "agent": head.agent, "sessions": 0,
            "requests": 0, "input": 0, "cacheRead": 0, "cacheWrite": 0, "write5m": 0, "write1h": 0,
            "output": 0, "_first": [], "_peak": [], "_usd": 0.0, "_unpriced": False})
        row["sessions"] += 1
        row["_first"].append(_context(head))
        row["_peak"].append(max(_context(r) for r in own))
        for key, value in (("requests", len(own)), ("input", sum(r.input for r in own)),
                           ("cacheRead", sum(r.read for r in own)), ("cacheWrite", sum(r.write for r in own)),
                           ("write5m", sum(r.write5m for r in own)), ("write1h", sum(r.write1h for r in own)),
                           ("output", sum(r.output for r in own))):
            row[key] += value
        if priced:
            for r in own:
                cost = request_usd(r)
                row["_unpriced"] = row["_unpriced"] or cost is None
                row["_usd"] += cost or 0.0
        for prev, cur in zip(own, own[1:]):
            if prev.at is None or cur.at is None or _context(cur) < COLD_FLOOR:
                continue
            label = bucket((cur.at - prev.at).total_seconds() / 60)
            slot = gaps.setdefault((harness, model, cur.role, label), {"requests": 0, "cold": 0, "coldTokens": 0})
            slot["requests"] += 1
            missed = cur.input + cur.write
            if missed / _context(cur) > COLD_SHARE:
                slot["cold"] += 1
                slot["coldTokens"] += missed
    out_rows = []
    for key in sorted(rows):
        row = rows[key]
        context = row["input"] + row["cacheRead"] + row["cacheWrite"]
        row["hitRate"] = round(row["cacheRead"] / context, 4) if context else None
        row["firstRequestMedian"] = _median(row.pop("_first"))
        row["peakMedian"] = _median(row.pop("_peak"))
        spent, unpriced = row.pop("_usd"), row.pop("_unpriced")
        if priced:
            row["usd"] = None if unpriced else round(spent, 4)
        out_rows.append(row)
    order = {label: n for n, (_, label) in enumerate(GAPS)}
    out_gaps = [{"harness": h, "model": m, "role": ro, "gap": label, **slot,
                 "coldRate": round(slot["cold"] / slot["requests"], 4)}
                for (h, m, ro, label), slot in sorted(gaps.items(), key=lambda i: (i[0][:3], order[i[0][3]]))]
    boundaries: dict = {}
    for g in out_gaps:
        key = (g["harness"], g["model"], g["role"])
        boundaries.setdefault(key, None)
        # Sub-two-minute cold requests are compaction and prefix changes, not expiry.
        if boundaries[key] is None and g["gap"] != "<2m" and g["requests"] >= BOUNDARY_MIN \
                and g["coldRate"] >= COLD_SHARE:
            boundaries[key] = g["gap"]
    return out_rows, out_gaps, [{"harness": h, "model": m, "role": ro, "coldFromGap": b}
                                for (h, m, ro), b in boundaries.items()]


def listing_summary(rows: list) -> list:
    grouped: dict = {}
    for role, agent, entries, chars in rows:
        grouped.setdefault((role, agent), []).append((entries, chars))
    return [{"harness": "opencode", "role": role, "agent": agent, "sessions": len(values),
             "entriesMedian": _median([v[0] for v in values]), "charsMedian": _median([v[1] for v in values]),
             "tokensEstimate": (_median([v[1] for v in values]) or 0) // 4}
            for (role, agent), values in sorted(grouped.items())]


def cmd_cache(args) -> int:
    since = None
    if args.since:
        since = parse_instant(args.since)
        if since is None:
            return refuse({"code": "se-bad-since", "message": f"--since is not an ISO-8601 instant: {args.since}"},
                          args.json)
    directory = None if args.all else Path(args.directory or Path.cwd()).expanduser().resolve()
    anomalies: list = []
    reqs: list = []
    listings: list = []
    sources: dict = {}
    unavailable: dict = {}
    if args.harness in ("all", "claude"):
        found, source, why = claude_requests(directory, anomalies)
        reqs += found
        if source:
            sources["claude"] = source
        else:
            unavailable["claude"] = why
    if args.harness in ("all", "opencode"):
        path = opencode_db(args.opencode_db)
        if path.is_file():
            try:
                found, listings = opencode_requests(path, directory, anomalies)
                reqs += found
                sources["opencode"] = str(path)
            except sqlite3.Error as exc:
                unavailable["opencode"] = f"{path}: {exc}"
        else:
            unavailable["opencode"] = f"no OpenCode database at {path}"
    if since is not None:
        reqs = [r for r in reqs if r.at is not None and r.at >= since]
    session = getattr(args, "session", None)
    if session:
        reqs = [r for r in reqs if session in r.session]
    if not reqs:
        return refuse({"code": "se-no-usage", "message": "no request with usage in scope",
                       "unavailable": unavailable}, args.json)
    priced = bool(getattr(args, "usd", False))
    rows, gaps, boundaries = summarize(reqs, priced)
    out = {"ok": True, "scope": str(directory) if directory else "all", "sources": sources,
           "unavailable": unavailable, "rows": rows, "gaps": gaps, "boundaries": boundaries,
           "skillListing": listing_summary(listings), "anomalies": anomalies}
    if priced:
        out["pricing"] = {"sourceDate": pricing.table()["sourceDate"], "basis": "list price estimate"}
    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return FINDINGS if anomalies else OK
    print(f"{'harness':<10}{'role':<10}{'agent':<18}{'model':<26}{'sess':>6}{'req':>8}{'hit':>7}"
          f"{'first':>8}{'peak':>9}")
    for r in rows:
        print(f"{r['harness']:<10}{r['role']:<10}{r['agent'][:17]:<18}{r['model'][-25:]:<26}{r['sessions']:>6}"
              f"{r['requests']:>8}{(r['hitRate'] or 0):>7.1%}{r['firstRequestMedian']:>8}{r['peakMedian']:>9}")
    print("\ncold requests by the gap before them (a model never sums into another):")
    for g in gaps:
        print(f"  {g['harness']:<10}{g['role']:<10}{g['model'][-25:]:<26}{g['gap']:<8}"
              f"{g['cold']:>6}/{g['requests']:<7}{g['coldRate']:>7.1%}")
    for b in boundaries:
        print(f"  boundary {b['harness']} {b['role']} {b['model'][-25:]}: cold from {b['coldFromGap'] or 'no gap measured'}")
    for s in out["skillListing"]:
        print(f"  skills {s['role']} {s['agent']}: {s['entriesMedian']} entries, ~{s['tokensEstimate']} tokens always on")
    for name, why in unavailable.items():
        print(f"  ! {name} unavailable: {why}")
    for a in anomalies:
        print(f"  ! {a['file']} line {a['line']}: {a['message']}")
    return FINDINGS if anomalies else OK
