"""`cq specs serve` — the local spec portal.

A stdlib HTTP server bound to loopback that reads and writes through the SAME layer the CLI
uses: `open_backend` for every read, and the CLI's own verbs (`cmd_new`, `cmd_section`,
`cmd_record`, `cmd_discover`) for every write, so CAS, refusals and the backend's write path are
the ones the terminal gets, on github, azure-boards or git alike.

The request logic is `Portal.handle`, a pure function of (method, target, headers, body) that the
tests call without a socket; `serve` only wraps it in `ThreadingHTTPServer`.

SECURITY
--------
* Loopback only: a non-loopback `--host` is refused before a socket opens.
* A random per-run token is required on every `/api/` call and for the page, carried by the
  `qtoken` cookie (`HttpOnly; SameSite=Strict; Path=/`) or the `X-Quenching-Token` header; it is
  never accepted in a URL. The URL the portal prints and `--open` passes holds only a one-use
  nonce, which `GET /?nonce=` trades for the cookie (303 to `/`). Static assets need nothing.
* `Host` must name the bound loopback address (DNS rebinding); `Origin`, when sent, must be this
  server, and every POST must send it (CSRF from another tab).
* Writes need `application/json` and are refused SERVER-SIDE
  when the portal is `--read-only` or the backend itself is read-only.
* A refusal or conflict is an HTTP error that carries the CLI's refusal `code`; never a success.
* `~/.claude` is read only under `--sessions` (off by default), only by `GET /api/sessions`, and
  only through the reader `cq` lends from the session pillar (`quenching.common.lent`), so this
  module never imports `session` and the reader copy, which lends nothing, refuses the flag;
  each row is rebuilt from `SESSION_FIELDS`, never copied by key.
* The chat runs only under `--chat` (off by default): `POST /api/chat` starts one read-only
  `claude -p` (`quenching.specs.chat`) in a worktree of this project, one job at a time, and
  `GET /api/chat/<job>` polls its projected events. It writes nothing to the store, so it is
  outside `writes_enabled`, but it keeps every other POST gate (token, Origin, JSON, size).
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import io
import ipaddress
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
from argparse import Namespace
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from quenching.common.dates import today
from quenching.common.lent import borrow
from quenching.common.output import OK
from quenching.specs import chat as chat_mod
from quenching.specs.backends import open_backend
from quenching.specs.backends.base import BackendRefusal, SpecBackend, transport_read_only
from quenching.specs.commands.create import cmd_new
from quenching.specs.commands.fields import cmd_record
from quenching.specs.commands.granular import cmd_section
from quenching.specs.commands.next import _priority_rank
from quenching.specs.commands.output import Emitter
from quenching.specs.commands.push import cmd_push
from quenching.specs.commands.task import cmd_discover
from quenching.specs.members import read_members
from quenching.specs.parse.deps import schedule
from quenching.specs.parse.epics import (derive_epic, parse_items, presentation_level,
                                         split_ref)
from quenching.specs.parse.fields import set_frontmatter_record
from quenching.specs.parse.records import spec_records
from quenching.specs.parse.sections import ready_report, section_state
from quenching.specs.parse.tasks import task_progress
from quenching.specs.schema import canonical_headings

TOKEN_HEADER = "X-Quenching-Token"
COOKIE_NAME = "qtoken"
TTL_SECONDS = 5.0
MAX_BODY = 1_000_000
STAGE_ORDER = ["captured", "ready", "approved", "executing", "blocked", "review",
               "archived"]
CONFLICT_MARKERS = ("stale", "conflict", "write-once", "changed")
SESSION_FIELDS = ("sessionId", "cwd", "state", "status", "statusSince", "lastEvent",
                  "pendingQuestion", "pendingTool", "activity")
SUBAGENT_FIELDS = ("agentType", "start", "end", "active")


def static_dir() -> Path | None:
    """The portal assets: `QUENCHING_PORTAL_DIR`, else `portal/` beside `bin/` (the plugin and
    the reader) or beside `scripts/` (the Codex translation)."""
    env = os.environ.get("QUENCHING_PORTAL_DIR")
    here = Path(__file__).resolve()
    for candidate in ([Path(env)] if env else []) + [here.parents[3] / "portal",
                                                    here.parents[4] / "portal"]:
        if (candidate / "index.html").is_file():
            return candidate
    return None


def _json(status: int, obj: dict) -> tuple[int, dict, bytes]:
    return status, {"Content-Type": "application/json; charset=utf-8",
                    "Cache-Control": "no-store"}, \
        json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _validated(headers: dict, obj: dict, volatile: str = "fetchedAt") -> tuple[int, dict, bytes]:
    """A list read with a change token: the ETag hashes the content without `volatile` (which
    moves on every rebuild), and a matching `If-None-Match` is a body-less 304."""
    stable = {k: v for k, v in obj.items() if k != volatile}
    etag = '"' + hashlib.sha256(json.dumps(
        stable, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32] + '"'
    if etag in [t.strip() for t in headers.get("if-none-match", "").split(",")]:
        return 304, {"ETag": etag, "Cache-Control": "no-store"}, b""
    status, out, body = _json(200, obj)
    return status, {**out, "ETag": etag}, body


def _error(status: int, code: str, message: str, **extra) -> tuple[int, dict, bytes]:
    return _json(status, {"ok": False, "code": code, "message": message, **extra})


def _status_for(payload: dict) -> int:
    code = str(payload.get("code", ""))
    if code == "sp-read-only":
        return 403
    if any(marker in code for marker in CONFLICT_MARKERS):
        return 409
    if "unknown" in code or "not-found" in code or code.endswith("-no-spec"):
        return 404
    return 422


_EPIC_ITEM_KEYS = ("label", "title", "spec", "after", "group", "groupTitle", "waitingOn",
                   "status")


HUMAN_EPIC_MARK = "decidir com o humano"

# sys.stdin/stdout/stderr belong to the process, so the lock that guards their redirection does too.
# Always taken after `Portal.lock` and never held while taking another: no deadlock between portals.
_STDIO_LOCK = threading.RLock()


class _SnapshotStore:
    """`read_specs` over the snapshot, so a self-reference resolves without a second read."""

    def __init__(self, infos: dict, name: str):
        self.infos, self.name = infos, name

    def read_specs(self, ids) -> dict:
        return {str(i): self.infos.get(str(i)) for i in ids}


class Portal:
    """One portal over one repository root. Every backend touch happens under `self.lock`;
    the CLI verbs print to the process-wide stdout, so `_verb` also takes the module-wide
    `_STDIO_LOCK` around the redirection, which keeps two portals from capturing each other."""

    def __init__(self, root: str, token: str, read_only: bool = False,
                 host: str = "127.0.0.1", port: int = 0, nonce: str | None = None,
                 sessions: dict | None = None, chat: dict | None = None) -> None:
        self.root, self.token, self.read_only = root, token, read_only
        self.serve_cmd = "cq specs serve"  # the command the 401 names; the central door overrides
        self.sessions = sessions  # {"read"} lent by the session pillar under --sessions, else None
        self.chat = chat  # {"claude", "budget", "worktrees"} under --chat, else None
        self.jobs: dict[str, chat_mod.Job] = {}
        self.nonce: str | None = nonce if nonce is not None else secrets.token_urlsafe(18)
        self.host, self.port = host, port
        self.lock = threading.RLock()
        self._cache: dict | None = None

    # ── gates ────────────────────────────────────────────────────────────────────────────
    def allowed_hosts(self) -> set[str]:
        names = {self.host, "localhost", "127.0.0.1", "[::1]"} if self.host in (
            "127.0.0.1", "localhost", "::1") else {self.host}
        return {f"{n}:{self.port}" for n in names}

    def check_host(self, headers: dict) -> bool:
        return headers.get("host", "").lower() in {h.lower() for h in self.allowed_hosts()}

    def check_origin(self, headers: dict, required: bool = False) -> bool:
        origin = headers.get("origin")
        if origin is None:
            return not required
        return origin.lower() in {f"http://{h}".lower() for h in self.allowed_hosts()}

    def check_token(self, headers: dict) -> bool:
        given = headers.get(TOKEN_HEADER.lower())
        if given is None:
            jar = SimpleCookie()
            try:
                jar.load(headers.get("cookie", ""))
            except CookieError:
                return False
            morsel = jar.get(COOKIE_NAME)
            given = morsel.value if morsel else None
        return bool(given) and hmac.compare_digest(str(given), self.token)

    def redeem_nonce(self, given: str | None) -> bool:
        """The one-use nonce of the printed URL: true once, then never again."""
        with self.lock:
            if not given or self.nonce is None or not hmac.compare_digest(given, self.nonce):
                return False
            self.nonce = None
            return True

    def writes_enabled(self) -> tuple[bool, str]:
        if self.read_only:
            return False, "the portal was started with --read-only"
        if SpecBackend.read_only or transport_read_only():
            return False, "the specs backend is read-only"
        return True, ""

    # ── request entry ────────────────────────────────────────────────────────────────────
    def handle(self, method: str, target: str, headers: dict[str, str],
               body: bytes = b"") -> tuple[int, dict, bytes]:
        headers = {k.lower(): v for k, v in headers.items()}
        if not self.check_host(headers):
            return _error(403, "sp-portal-bad-host", "Host header is not this server")
        if not self.check_origin(headers, required=method == "POST"):
            return _error(403, "sp-portal-bad-origin", "Origin is not this server")
        parts = urlsplit(target)
        query = parse_qs(parts.query)
        path = parts.path
        try:
            if path.startswith("/api/"):
                return self._api(method, path, query, headers, body)
            if method not in ("GET", "HEAD"):
                return _error(405, "sp-portal-method", "static files are read-only")
            if path in ("/", "/index.html") and not self.check_token(headers):
                if method == "GET" and self.redeem_nonce((query.get("nonce") or [None])[0]):
                    return 303, {"Location": "/", "Set-Cookie": self.cookie(),
                                 "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}, b""
                return _error(401, "sp-portal-token", f"open the URL `{self.serve_cmd}` printed")
            return self._static(path)
        except BackendRefusal as e:
            return _json(_status_for(e.err), {"ok": False, **{
                k: v for k, v in e.err.items() if k != "exit"}})

    def cookie(self) -> str:
        return f"{COOKIE_NAME}={self.token}; HttpOnly; SameSite=Strict; Path=/"

    def _static(self, path: str) -> tuple[int, dict, bytes]:
        root = static_dir()
        if root is None:
            return _error(500, "sp-portal-assets", "the portal assets are not installed")
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        file = (root / name).resolve()
        if root.resolve() not in file.parents or not file.is_file():
            return _error(404, "sp-portal-not-found", f"no such asset: {path}")
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        csp = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
               "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        return 200, {"Content-Type": f"{ctype}; charset=utf-8", "Content-Security-Policy": csp,
                     "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                     "Referrer-Policy": "no-referrer"}, file.read_bytes()

    def _api(self, method, path, query, headers, body):
        write = method == "POST"
        if method not in ("GET", "POST"):
            return _error(405, "sp-portal-method", "GET or POST only")
        if not self.check_token(headers):
            return _error(401, "sp-portal-token", "a valid token is required on every API call")
        route = path[len("/api/"):].strip("/")
        if write:
            ok, why = self.writes_enabled()
            if not ok and not (route == "chat" and self.chat is not None):
                return _error(403, "sp-read-only", f"writes are disabled: {why}")
            if "application/json" not in headers.get("content-type", ""):
                return _error(415, "sp-portal-content-type", "send application/json")
            if len(body) > MAX_BODY:
                return _error(413, "sp-portal-too-large", "request body too large")
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                return _error(400, "sp-portal-bad-json", "the body is not valid JSON")
            if not isinstance(data, dict):
                return _error(400, "sp-portal-bad-json", "the body must be a JSON object")
            if route == "chat" and self.chat is not None:
                return self._chat_start(data)
            return self._post(route, data)
        return self._get(route, query, headers)

    # ── reads ────────────────────────────────────────────────────────────────────────────
    def _get(self, route: str, query: dict, headers: dict):
        fresh = (query.get("refresh") or ["0"])[0] == "1"
        if route == "config":
            ok, why = self.writes_enabled()
            return _json(200, {"ok": True, "writable": ok, "readOnlyReason": why,
                               "ttlSeconds": TTL_SECONDS, "stages": STAGE_ORDER,
                               "headings": canonical_headings(),
                               "sessions": self.sessions is not None,
                               "chat": self.chat is not None,
                               "chatBudgetUsd": self.chat["budget"] if self.chat else None})
        if route == "sessions" and self.sessions is not None:
            return _validated(headers, self._sessions(), volatile="generatedAt")
        if route.startswith("chat/") and self.chat is not None:
            return self._chat_poll(route.split("/", 1)[1], query)
        with self.lock:
            snap = self._snapshot(fresh)
            if isinstance(snap, tuple):
                return snap
            if route == "specs":
                return _validated(headers, {
                    "ok": True, "fetchedAt": snap["at"], "ttlSeconds": TTL_SECONDS,
                    "count": len(snap["rows"]), "specs": snap["rows"], "stages": STAGE_ORDER,
                    "pending": self._pending()})
            if route.startswith("specs/"):
                return self._detail(snap, route.split("/", 1)[1])
            if route == "search":
                return self._search(snap, (query.get("q") or [""])[0])
            if route == "epics":
                return _validated(headers, {"ok": True, **self._epics(snap)})
            if route == "human":
                return _validated(headers, {"ok": True, **self._human(snap)})
            if route == "schedule":
                return _validated(headers, {"ok": True, "fetchedAt": snap["at"],
                                            **self._schedule(snap)})
        return _error(404, "sp-portal-not-found", f"no such route: /api/{route}")

    def _pending(self) -> dict:
        """The unsent local writes the header advertises, without reading the remote."""
        backend, err = open_backend(self.root)
        if err or backend is None:
            return {"count": 0, "specs": [], "since": None, "supported": False}
        pending = getattr(backend, "pending_state", None)
        if pending is None:
            return {"count": 0, "specs": [], "since": None, "supported": False}
        try:
            state = pending()
        except BackendRefusal as e:
            return {"count": 0, "specs": [], "since": None, "supported": True,
                    "error": e.err.get("message") or e.err.get("code")}
        if not state:
            return {"count": 0, "specs": [], "artifacts": [], "since": None, "supported": True}
        artifacts = state.get("artifacts") or []
        return {"count": len(state["specs"]) + len(artifacts), "specs": state["specs"],
                "artifacts": artifacts, "since": state["since"], "supported": True}

    def _snapshot(self, fresh: bool):
        now = time.time()
        if not fresh and self._cache and now - self._cache["at"] < TTL_SECONDS:
            return self._cache
        backend, err = open_backend(self.root)
        if err:
            return _json(_status_for(err), {"ok": False, **{k: v for k, v in err.items()
                                                            if k != "exit"}})
        from quenching.specs.parse import derive_info
        infos = []
        for s in backend.list_specs():
            info, rerr = backend.read_spec(s["id"])
            infos.append(info if info and not rerr else derive_info(s, ""))
        rows = [self._row(i) for i in infos]
        plans = sorted((r for r in rows if r["phase"] == "plans"),
                       key=lambda r: (_priority_rank(r["priority"])[0], r["date"], str(r["id"])))
        for rank, r in enumerate(plans, 1):
            r["rank"] = rank
        self._cache = {"at": now, "rows": rows, "infos": {str(i["id"]): i for i in infos},
                       "backend": backend.name}
        return self._cache

    @staticmethod
    def _stage(info: dict) -> str:
        if info["phase"] == "archive":
            return "archived"
        if task_progress(info["tasks"])[1] > 0:
            return "blocked"
        if info["frontmatter"].get("reviewed"):
            return "review"
        stage = info["stage"]
        return stage if stage in STAGE_ORDER else "captured"

    def _row(self, info: dict) -> dict:
        fm = info["frontmatter"]
        checked, blocked, total = task_progress(info["tasks"])
        tags = fm.get("tags") or []
        locator = str(info.get("path") or "")
        card = fm.get("card") or fm.get("issue") or (locator if locator.startswith("http") else "")
        merge = fm.get("merge") if isinstance(fm.get("merge"), dict) else {}
        priority = fm.get("priority") if isinstance(fm.get("priority"), dict) else None
        run = fm.get("run") if isinstance(fm.get("run"), dict) else None
        return {
            "id": info["id"], "title": str(fm.get("title") or ""), "phase": info["phase"],
            "stage": self._stage(info), "derivedStage": info["stage"], "date": info["date"],
            "summary": str(fm.get("summary") or ""), "priority": priority, "rank": None,
            "complexity": (priority or {}).get("complexity"),
            "progress": {"checked": checked, "blocked": blocked, "total": total},
            "subject": fm.get("subject") or "", "tags": tags if isinstance(tags, list) else [],
            "assignee": fm.get("assignee") or "", "workItemType": fm.get("workItemType") or "",
            "approved": fm.get("approved") or None, "card": card,
            "pr": fm.get("pr") or merge.get("pr") or "",
            "run": {k: str(run.get(k) or "") for k in ("state", "verifier", "date")} if run else None,
        }

    def _detail(self, snap: dict, spec_id: str):
        info = snap["infos"].get(spec_id)
        if info is None:
            return _error(404, "sp-unknown-spec", f"no spec {spec_id}")
        records = spec_records(info["frontmatter"])
        sections = [{"heading": h, "state": section_state(info["sections"], h),
                     "body": info["sections"].get(h, {}).get("body", "")}
                    for h in canonical_headings()]
        tasks = [{k: t.get(k) for k in ("id", "text", "checked", "blocked", "state", "reason",
                                         "verify", "commit", "subject", "files")}
                 for t in info["tasks"]]
        timeline = [{"record": k, "value": v} for k, v in records.items() if v]
        ready = ready_report(info) if info["phase"] == "plans" else None
        row = next(r for r in snap["rows"] if str(r["id"]) == spec_id)
        return _json(200, {"ok": True, "fetchedAt": snap["at"], "spec": row,
                           "sections": sections, "tasks": tasks, "records": records,
                           "timeline": timeline, "ready": ready,
                           "executeCommand": f"/quenching-specs execute {info['id']}"})

    def _search(self, snap: dict, q: str):
        needle = q.strip().lower()
        if not needle:
            return _json(200, {"ok": True, "query": q, "count": 0, "results": []})
        out = []
        for sid, info in snap["infos"].items():
            title = str(info["frontmatter"].get("title") or "")
            text = info["text"]
            at = text.lower().find(needle)
            if at < 0 and needle not in title.lower():
                continue
            lo = max(0, at - 50)
            out.append({"id": info["id"], "title": title, "phase": info["phase"],
                        "stage": self._stage(info),
                        "snippet": " ".join(text[lo:at + 90].split()) if at >= 0 else ""})
        return _json(200, {"ok": True, "query": q, "count": len(out), "results": out[:100]})

    def _epics(self, snap: dict) -> dict:
        """Composition progress — every spec whose `## Tasks` names a member. The route keeps
        its historical name; the derivation no longer reads `workItemType: epic` or a `parent:`
        back-reference."""
        epics = [i for i in snap["infos"].values() if parse_items(i)]
        members, unreadable = self._members(snap, epics)
        rows = []
        for e in epics:
            derived = derive_epic(e, members, unreadable=unreadable)
            items = [{k: v for k, v in i.items() if k in _EPIC_ITEM_KEYS} for i in derived["items"]]
            rows.append({"id": e["id"], "title": str(e["frontmatter"].get("title") or ""),
                         "level": presentation_level(e),
                         "items": items, "groups": derived["groups"],
                         "cycle": derived["cycle"], "criticalPath": derived["criticalPath"],
                         "children": [{"id": i["spec"], "title": i["title"],
                                       "stage": i["status"]} for i in derived["items"]],
                         "done": sum(1 for i in derived["items"] if i["status"] == "done"),
                         "total": len(derived["items"])})
        return {"supported": bool(epics), "epics": rows}

    def _schedule(self, snap: dict) -> dict:
        """The schedule `cq specs schedule` derives over the snapshot, each node carrying the
        portal's stage. The page draws it and recomputes nothing."""
        result = schedule(snap["infos"])
        for node in result["nodes"]:
            node["stage"] = self._stage(snap["infos"][node["id"]])
        return result

    def _members(self, snap: dict, epics: list[dict]) -> tuple[dict, set[str]]:
        """The snapshot's infos, plus every cross-repository member of `epics` read from its own
        store through `members.py`; the second value names the keys whose store was unreadable."""
        members = dict(snap["infos"])
        remote = sorted({str(i["spec"]) for e in epics for i in parse_items(e)
                         if i["spec"] and split_ref(str(i["spec"]))[0]})
        if not remote:
            return members, set()
        got, unreadable = read_members(_SnapshotStore(snap["infos"], snap["backend"]), remote, self.root)
        members.update(got)
        return members, unreadable

    def _human(self, snap: dict) -> dict:
        """What waits for the human, read from the snapshot alone: open members of the epics
        titled `HUMAN_EPIC_MARK`, specs with a blocked task, and ready specs nobody approved."""
        infos = snap["infos"]
        rows = {str(r["id"]): r for r in snap["rows"]}
        epics = [e for e in infos.values()
                 if HUMAN_EPIC_MARK in str(e["frontmatter"].get("title") or "").lower()
                 and parse_items(e)
                 and e["phase"] != "archive"]
        members, unreadable = self._members(snap, epics)
        epic_members, seen = [], set()
        for e in epics:
            for it in derive_epic(e, members, unreadable=unreadable)["items"]:
                if it["status"] in ("done", "dropped"):
                    continue
                sid = str(it["spec"]) if it["spec"] else ""
                row, member = rows.get(sid), it["member"]
                if row:
                    title, stage = row["title"], row["stage"]
                elif member:
                    title = str(member["frontmatter"].get("title") or it["title"])
                    stage = self._stage(member)
                else:
                    title = it["title"]
                    stage = "unreadable" if it["status"] == "unreadable" else "missing"
                epic_members.append({"epic": e["id"], "label": it["label"],
                                     "id": it["spec"] or None, "title": title, "stage": stage,
                                     "status": it["status"]})
                if sid:
                    seen.add(sid)
        blocked, waiting = [], []
        for sid, info in infos.items():
            row = rows[sid]
            if info["phase"] != "plans":
                continue
            reasons = [{"task": t.get("id"), "reason": t.get("reason") or ""}
                       for t in info["tasks"] if t.get("blocked")]
            if reasons:
                blocked.append({"id": info["id"], "title": row["title"], "stage": row["stage"],
                                "reasons": reasons})
                seen.add(sid)
            if info["stage"] == "ready" and not info["frontmatter"].get("approved"):
                waiting.append({"id": info["id"], "title": row["title"], "stage": row["stage"]})
                seen.add(sid)
        return {"fetchedAt": snap["at"], "count": len(seen) + sum(
                    1 for m in epic_members if not m["id"]),
                "epicMembers": epic_members, "blocked": blocked, "awaitingApproval": waiting}

    def _sessions(self) -> dict:
        """The live sessions inside this repository's worktrees, rebuilt field by field."""
        live, trees = self.sessions["read"](self.root)
        reals = sorted(((os.path.realpath(w["path"]), w) for w in trees),
                       key=lambda pair: len(pair[0]), reverse=True)
        rows = []
        for row in live["sessions"]:
            real = os.path.realpath(row["cwd"])
            tree = next((w for path, w in reals
                         if real == path or real.startswith(path + os.sep)), None)
            out = {k: row.get(k) for k in SESSION_FIELDS}
            out["worktree"] = {"path": tree["path"], "branch": tree["branch"],
                               "main": tree["main"]} if tree else None
            out["subagents"] = [{k: s.get(k) for k in SUBAGENT_FIELDS}
                                for s in row.get("subagents") or []]
            rows.append(out)
        return {"ok": True, "generatedAt": live["generatedAt"],
                "windowMinutes": live["windowMinutes"], "sessions": rows}

    # ── chat ─────────────────────────────────────────────────────────────────────────────
    def _chat_trees(self) -> list[str]:
        lister = self.chat.get("worktrees")
        trees = (lister(self.root) if lister else None) or [{"path": self.root}]
        return [os.path.realpath(w["path"]) for w in trees]

    def _chat_start(self, data: dict):
        question = data.get("question")
        if not isinstance(question, str) or not question.strip():
            return _error(400, "sp-portal-bad-input", "`question` is required (a string)")
        if len(question) > chat_mod.MAX_QUESTION:
            return _error(400, "sp-portal-bad-input",
                          f"`question` is longer than {chat_mod.MAX_QUESTION} characters")
        tree = data.get("worktree") or self.root
        trees = self._chat_trees()
        if not isinstance(tree, str) or os.path.realpath(tree) not in trees:
            return _error(400, "sp-portal-chat-worktree", "`worktree` is not a worktree of "
                                                          "this project")
        cwd = os.path.realpath(tree)
        with self.lock:
            if any(job.running for job in self.jobs.values()):
                return _error(409, "sp-portal-chat-busy", "a chat question is still running")
            job = chat_mod.Job(self.chat["claude"], self.chat["budget"], cwd, question.strip(),
                               self.chat.get("timeout", chat_mod.CHAT_TIMEOUT_SECONDS),
                               self.chat.get("model", "sonnet"))
            self.jobs[job.id] = job
            for old in list(self.jobs)[:-chat_mod.KEEP_JOBS]:
                if not self.jobs[old].running:
                    del self.jobs[old]
        return _json(202, {"ok": True, "job": job.id, "worktree": cwd})

    def _chat_poll(self, job_id: str, query: dict):
        job = self.jobs.get(job_id)
        if job is None:
            return _error(404, "sp-portal-chat-unknown-job", "no such chat job")
        try:
            after = int((query.get("after") or ["0"])[0])
        except ValueError:
            after = 0
        return _json(200, job.view(after))

    # ── writes ───────────────────────────────────────────────────────────────────────────
    def _post(self, route: str, data: dict):
        handlers = {"capture": self._capture, "section": self._section, "approve": self._approve,
                    "reorder": self._reorder, "discovery": self._discovery, "push": self._push}
        handler = handlers.get(route)
        if handler is None:
            return _error(404, "sp-portal-not-found", f"no such route: /api/{route}")
        with self.lock:
            try:
                result = handler(data)
            finally:
                self._cache = None
        if isinstance(result, tuple):
            return result
        return _json(200, {"ok": True, **result})

    def _verb(self, fn, args: dict, stdin: str = ""):
        """Run one CLI verb in-process and parse its `--json` payload. Returns the payload dict
        on success or a ready `(status, headers, body)` for a refusal."""
        out_buf, err_buf = io.StringIO(), io.StringIO()
        rc = 2
        with _STDIO_LOCK:
            old_in = sys.stdin
            sys.stdin = io.StringIO(stdin)
            try:
                with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                    try:
                        rc = fn(Namespace(json=True, **args), self.root, Emitter())
                    except BackendRefusal as e:
                        rc = e.err.get("exit", 2)
                        print(json.dumps({"ok": False, **{k: v for k, v in e.err.items()
                                                          if k != "exit"}}))
            finally:
                sys.stdin = old_in
        try:
            payload = json.loads(out_buf.getvalue())
        except ValueError:
            payload = {"ok": False, "code": "sp-portal-verb",
                       "message": (err_buf.getvalue() or out_buf.getvalue()).strip()
                       or "the verb produced no result"}
        if rc != OK or not payload.get("ok"):
            payload["ok"] = False
            payload.setdefault("code", "sp-portal-verb")
            return _json(_status_for(payload), payload)
        return payload

    @staticmethod
    def _text(data: dict, key: str, required: bool = True) -> str | tuple:
        value = data.get(key)
        if not isinstance(value, str) or (required and not value.strip()):
            return _error(400, "sp-portal-bad-input", f"`{key}` is required (a string)")
        return value.strip()

    def _capture(self, data: dict):
        title, problem = self._text(data, "title"), self._text(data, "problem")
        for v in (title, problem):
            if isinstance(v, tuple):
                return v
        tags = data.get("tags")
        return self._verb(cmd_new, {
            "name": title, "title": title, "verification": None,
            "subject": data.get("subject") or None, "type": data.get("type") or None,
            "tags": ",".join(tags) if isinstance(tags, list) else None,
            "complexity": data.get("complexity") or None}, f"## Problem\n\n{problem}\n")

    def _section(self, data: dict):
        spec, heading = str(data.get("spec", "")).strip(), self._text(data, "heading")
        body = self._text(data, "body", required=False)
        for v in (heading, body):
            if isinstance(v, tuple):
                return v
        if not spec:
            return _error(400, "sp-portal-bad-input", "`spec` is required")
        return self._verb(cmd_section, {"spec": spec, "heading": heading, "moment": None,
                                        "write": True, "scope": None, "fold": None}, body + "\n")

    def _discovery(self, data: dict):
        spec, text = str(data.get("spec", "")).strip(), self._text(data, "text")
        if isinstance(text, tuple):
            return text
        if not spec:
            return _error(400, "sp-portal-bad-input", "`spec` is required")
        return self._verb(cmd_discover, {"spec": spec, "text": text})

    def _push(self, data: dict):
        """Publish every pending write in one gesture, the same verb the CLI runs. `--drop`
        stays on the terminal: a stale write surfaces as a 409 instead of discarding work."""
        return self._verb(cmd_push, {"drop": None})

    def _approve(self, data: dict):
        """A human clicked: stamp `approved: {by: human}` through the CLI's own `record` verb.
        The ready gate is checked first, exactly as `execute` does before it stamps."""
        spec = str(data.get("spec", "")).strip()
        if not spec:
            return _error(400, "sp-portal-bad-input", "`spec` is required")
        backend, err = open_backend(self.root)
        if err:
            return _json(_status_for(err), {"ok": False, **{k: v for k, v in err.items()
                                                            if k != "exit"}})
        info, rerr = backend.read_spec(spec)
        if rerr:
            return _json(_status_for(rerr), {"ok": False, **{k: v for k, v in rerr.items()
                                                             if k != "exit"}})
        if info["phase"] == "plans":
            ready = ready_report(info)
            if not ready["ok"]:
                return _error(422, "sp-portal-not-ready",
                              "the ready gate is not met; fill the missing sections first",
                              missing=ready["missing"], malformed=ready["malformed"])
        return self._verb(cmd_record, {"spec": spec, "name": "approved",
                                       "set": [f"date={today()}", "by=human"]})

    def _reorder(self, data: dict):
        """Write `priority.level` 1..N for the ids in `order`: ONE commit through the backend's
        batch helper (`write_specs`, the git store) when it has one, else a write per spec.
        Nothing is written when any id is unknown or the backend refuses the batch."""
        order = data.get("order")
        if not isinstance(order, list) or not order or not all(
                isinstance(i, (int, str)) for i in order):
            return _error(400, "sp-portal-bad-input", "`order` must be a non-empty id list")
        order = [str(i) for i in order]
        if len(set(order)) != len(order):
            return _error(400, "sp-portal-bad-input", "`order` repeats an id")
        backend, err = open_backend(self.root)
        if err:
            return _json(_status_for(err), {"ok": False, **{k: v for k, v in err.items()
                                                            if k != "exit"}})
        items = []
        for level, spec_id in enumerate(order, 1):
            info, rerr = backend.read_spec(spec_id)
            if rerr:
                return _json(_status_for(rerr), {"ok": False, **{k: v for k, v in rerr.items()
                                                                 if k != "exit"}})
            current = info["frontmatter"].get("priority")
            current = dict(current) if isinstance(current, dict) else {}
            if str(current.get("level", "")) == str(level):
                continue
            merged = {**current, "level": str(level), "date": today()}
            fields = ["level", "criticality", "complexity", "date"]
            items.append((info, set_frontmatter_record(
                info["text"], "priority", {k: merged[k] for k in fields if k in merged})))
        if not items:
            return {"written": 0, "order": order, "batched": False}
        batch = getattr(backend, "write_specs", None)
        if batch is not None:
            batch(items)
            return {"written": len(items), "order": order, "batched": True}
        done = 0
        try:
            for info, text in items:
                backend.write_spec(info, text)
                done += 1
        except BackendRefusal as e:
            payload = {"ok": False, **{k: v for k, v in e.err.items() if k != "exit"},
                       "written": done, "pending": len(items) - done}
            return _json(_status_for(e.err), payload)
        return {"written": done, "order": order, "batched": False}


# ── the server ───────────────────────────────────────────────────────────────────────────
def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def make_server(root: str, host: str = "127.0.0.1", port: int = 0, read_only: bool = False,
                token: str | None = None, sessions: dict | None = None,
                chat: dict | None = None):
    """A bound, not yet serving `ThreadingHTTPServer` carrying its `Portal` as `.portal`."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    portal = Portal(root, token or secrets.token_urlsafe(24), read_only, host, 0,
                    sessions=sessions, chat=chat)

    class Handler(BaseHTTPRequestHandler):
        server_version = "quenching-portal"
        protocol_version = "HTTP/1.1"

        def _run(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, MAX_BODY + 1)) if length else b""
            status, headers, payload = portal.handle(method, self.path, dict(self.headers), body)
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if method != "HEAD":
                self.wfile.write(payload)

        def do_GET(self): self._run("GET")
        def do_HEAD(self): self._run("HEAD")
        def do_POST(self): self._run("POST")
        def do_PUT(self): self._run("PUT")
        def do_DELETE(self): self._run("DELETE")

        def log_message(self, *args):  # quiet: the URL is the only thing worth printing
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    portal.port = server.server_address[1]
    server.portal = portal
    return server


SESSIONS = "specs.portal.sessions"  # the capability name `cq` lends under


def sessions_source() -> tuple[dict | None, dict | None]:
    """`({"read"}, None)` for `--sessions`, or `(None, refusal)`."""
    lender = borrow(SESSIONS)
    if lender is None:
        return None, {"code": "sp-portal-sessions-unavailable", "exit": 2,
                      "message": "this copy of the portal carries no session reader; "
                                 "--sessions needs the full quenching plugin"}
    return lender.source()


def chat_settings(root: str | None) -> dict:
    """`specs.chat` over the code defaults; a bad leaf warns on stderr and is ignored."""
    from quenching.common.config import setting
    def positive(v) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0

    checks = {"model": lambda v: isinstance(v, str) and bool(v.strip()),
              "budgetUsd": positive, "timeoutSeconds": positive}
    defaults = {"model": "sonnet", "budgetUsd": chat_mod.DEFAULT_BUDGET_USD,
                "timeoutSeconds": chat_mod.CHAT_TIMEOUT_SECONDS}
    out = dict(defaults)
    for key, check in checks.items():
        if root is None:
            break
        out[key], warning = setting(root, "specs", f"chat.{key}", defaults[key], check)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
    return out


def chat_source(enabled: bool, budget: float | None,
                root: str | None = None) -> tuple[dict | None, dict | None]:
    """`({"claude", "budget"}, None)` for `--chat`, `(None, None)` without it, or a refusal."""
    if not enabled:
        return None, None
    cfg = chat_settings(root)
    budget = cfg["budgetUsd"] if budget is None else budget
    if not budget > 0:
        return None, {"code": "sp-portal-chat-budget", "exit": 2,
                      "message": "--chat-budget-usd must be a positive dollar amount"}
    claude = chat_mod.find_claude()
    if claude is None:
        return None, {"code": "sp-portal-chat-unavailable", "exit": 2,
                      "message": "--chat needs the `claude` CLI on PATH"}
    chat = {"claude": claude, "budget": budget, "worktrees": None}
    if cfg["model"] != "sonnet":
        chat["model"] = cfg["model"]
    if cfg["timeoutSeconds"] != chat_mod.CHAT_TIMEOUT_SECONDS:
        chat["timeout"] = cfg["timeoutSeconds"]
    return chat, None


def cmd_serve(args, root: str, out: Emitter) -> int:
    # The portal publishes on the human's click, and `_git` does not take an env: without this
    # a credential prompt would wait behind the locks with the browser polling.
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    host = args.host
    if not is_loopback(host):
        return out.emit_err(args.json, {
            "code": "sp-serve-host", "exit": 2,
            "message": f"the portal binds loopback only; '{host}' is refused"})
    if static_dir() is None:
        return out.emit_err(args.json, {
            "code": "sp-portal-assets", "exit": 2,
            "message": "the portal assets (assets/portal) are not installed"})
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    sessions = None
    if vars(args).get("sessions"):
        sessions, refusal = sessions_source()
        if refusal:
            return out.emit_err(args.json, refusal)
    chat, refusal = chat_source(bool(vars(args).get("chat")), vars(args).get("chat_budget_usd"), root)
    if refusal:
        return out.emit_err(args.json, refusal)
    read_only = bool(args.read_only)
    server = make_server(root, host, args.port, read_only, sessions=sessions, chat=chat)
    portal = server.portal
    shown = "localhost" if host == "localhost" else host
    url = f"http://{shown if ':' not in shown else '[' + shown + ']'}:{portal.port}/" \
          f"?nonce={portal.nonce}"
    writable, why = portal.writes_enabled()
    info = {"ok": True, "url": url, "host": host, "port": portal.port, "backend": backend.name,
            "writable": writable, "readOnlyReason": why, "sessions": sessions is not None,
            "chat": chat is not None}
    out.emit(args.json, info,
             f"quenching portal ({backend.name}) — {'read-write' if writable else 'read-only: ' + why}\n"
             f"{url}\nCtrl-C to stop")
    sys.stdout.flush()
    if args.open:
        import webbrowser
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return OK
