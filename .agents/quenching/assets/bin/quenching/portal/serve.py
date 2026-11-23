"""`cq portal serve` — one loopback server over every registered project.

The registry is read once, at start. Each project gets its own `Portal` from the specs pillar,
all sharing one token, one host and one port; `Central.handle` routes `/p/<name>/api/...` to the
project's portal with the prefix stripped, so every gate, CAS and refusal is that portal's own.
The page, its assets and the one-use nonce go through a door `Portal` over no root that never
reaches a backend, because every `/api/` path is answered here before it could.

The specs portal and the worktree reader are borrowed (`quenching.common.lent`), never imported:
`cq` lends them under `LOANS["portal"]`.
"""
from __future__ import annotations

import secrets
import sys
from urllib.parse import quote, unquote, urlsplit

from quenching.common.lent import borrow
from quenching.common.output import OK, emit, refuse
from quenching.portal import registry
from quenching.portal.registry import PortalRefusal

SPECS = "portal.specs"
WORKTREES = "portal.worktrees"
PREFIX = "/p/"


class Central:
    """The router over one `Portal` per project; `handle` has `Portal.handle`'s signature."""

    def __init__(self, specs, list_worktrees, projects: list[dict], token: str,
                 read_only: bool = False, host: str = "127.0.0.1", port: int = 0,
                 sessions: dict | None = None, nonce: str | None = None,
                 chat: dict | None = None) -> None:
        self.specs, self.list_worktrees, self.projects = specs, list_worktrees, projects
        self.door = specs.Portal("", token, read_only, host, port, nonce=nonce)
        self.door.serve_cmd = "cq portal serve"
        self.portals = {}
        for project in projects:
            portal = specs.Portal(project["path"], token, read_only, host, port,
                                  sessions=sessions,
                                  chat={**chat, "worktrees": list_worktrees} if chat else None)
            portal.nonce = None  # only the door trades a nonce for the cookie
            self.portals[project["name"]] = portal

    @property
    def nonce(self) -> str | None:
        return self.door.nonce

    def bind(self, port: int) -> None:
        for portal in (self.door, *self.portals.values()):
            portal.port = port

    def handle(self, method: str, target: str, headers: dict[str, str],
               body: bytes = b"") -> tuple[int, dict, bytes]:
        low = {k.lower(): v for k, v in headers.items()}
        error = self.specs._error
        if not self.door.check_host(low):
            return error(403, "sp-portal-bad-host", "Host header is not this server")
        if not self.door.check_origin(low, required=method == "POST"):
            return error(403, "sp-portal-bad-origin", "Origin is not this server")
        parts = urlsplit(target)
        path = parts.path
        if not (path.startswith(PREFIX) or path.startswith("/api/")):
            return self.door.handle(method, target, headers, body)
        if not self.door.check_token(low):
            return error(401, "sp-portal-token", "a valid token is required on every API call")
        if path.startswith(PREFIX):
            name, _, rest = path[len(PREFIX):].partition("/")
            portal = self.portals.get(unquote(name))
            if portal is None:
                return error(404, "portal-unknown-project", f"no registered project {unquote(name)!r}")
            if not rest.startswith("api/"):
                return error(404, "sp-portal-not-found", f"no such route: {path}")
            query = f"?{parts.query}" if parts.query else ""
            return portal.handle(method, f"/{rest}{query}", headers, body)
        if path.rstrip("/") == "/api/projects":
            if method != "GET":
                return error(405, "sp-portal-method", "GET only")
            return self.specs._json(200, {"ok": True, "projects": self._projects()})
        return error(404, "sp-portal-not-found", f"no such route: {path}")

    def _projects(self) -> list[dict]:
        """Each project with its prefix and its worktrees, read from git's files, no process."""
        rows = []
        for project in self.projects:
            root = project["path"]
            trees = self.list_worktrees(root) or [{"path": root, "branch": None, "main": True}]
            rows.append({"name": project["name"], "path": root,
                         "prefix": f"{PREFIX}{quote(project['name'], safe='')}",
                         "worktrees": [{k: w.get(k) for k in ("path", "branch", "main")}
                                       for w in trees]})
        return rows


def make_central_server(central: Central, host: str = "127.0.0.1", port: int = 0):
    """A bound, not yet serving `ThreadingHTTPServer` carrying `central` as `.central`."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    max_body = central.specs.MAX_BODY

    class Handler(BaseHTTPRequestHandler):
        server_version = "quenching-portal"
        protocol_version = "HTTP/1.1"

        def _run(self, method: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(min(length, max_body + 1)) if length else b""
            status, headers, payload = central.handle(method, self.path, dict(self.headers), body)
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
    central.bind(server.server_address[1])
    server.central = central
    return server


def _unavailable(as_json: bool) -> int:
    return refuse({"code": "portal-serve-unavailable",
                   "message": "this copy carries no specs portal; `cq portal serve` needs the "
                              "quenching-specs module"}, as_json)


def cmd_serve(args) -> int:
    as_json = bool(args.json)
    specs, worktrees = borrow(SPECS), borrow(WORKTREES)
    if specs is None or worktrees is None:
        return _unavailable(as_json)
    if not specs.is_loopback(args.host):
        return refuse({"code": "sp-serve-host",
                       "message": f"the portal binds loopback only; '{args.host}' is refused"},
                      as_json)
    if specs.static_dir() is None:
        return refuse({"code": "sp-portal-assets",
                       "message": "the portal assets (assets/portal) are not installed"}, as_json)
    try:
        projects = registry.list_projects()
    except PortalRefusal as exc:
        return refuse({"code": exc.code, "message": exc.message}, as_json)
    if not projects:
        return refuse({"code": "portal-no-projects",
                       "message": "no project is registered; run `cq portal add <folder>`"},
                      as_json)
    sessions = None
    if args.sessions:
        sessions, refusal = specs.sessions_source()
        if refusal:
            return refuse({k: v for k, v in refusal.items() if k != "exit"}, as_json)
    chat, refusal = specs.chat_source(bool(args.chat), args.chat_budget_usd)
    if refusal:
        return refuse({k: v for k, v in refusal.items() if k != "exit"}, as_json)
    central = Central(specs, worktrees.list_worktrees, projects, secrets.token_urlsafe(24),
                      bool(args.read_only), args.host, sessions=sessions, chat=chat)
    server = make_central_server(central, args.host, args.port)
    port = server.server_address[1]
    shown = args.host if ":" not in args.host else f"[{args.host}]"
    url = f"http://{shown}:{port}/?nonce={central.nonce}"
    writable, why = central.door.writes_enabled()
    emit(as_json, {"ok": True, "url": url, "host": args.host, "port": port,
                   "projects": [p["name"] for p in projects], "writable": writable,
                   "readOnlyReason": why, "sessions": sessions is not None,
                   "chat": chat is not None},
         f"quenching portal — {len(projects)} project(s), "
         f"{'read-write' if writable else 'read-only: ' + why}\n{url}\nCtrl-C to stop")
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
