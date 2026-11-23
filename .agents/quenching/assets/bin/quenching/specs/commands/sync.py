"""`cq specs sync`: the local Azure Boards cache, by hand or kept in step by service hooks.

`status` and `reconcile` act on the cache; `apply` records ONE service-hook event read from stdin
(for any receiver that is not ours); `serve` is the receiver itself, a stdlib HTTP server on
loopback that answers `POST /hooks/azure` and reconciles in the background every TTL.

THE SECRET NEVER TRAVELS IN ARGV. It is read from `QUENCHING_SYNC_HOOK_SECRET`, compared in
constant time, and appears in no output and no log line. The receiver binds loopback only: a
tunnel or reverse proxy that exposes it is the operator's, not ours."""
from __future__ import annotations

import base64
import hmac
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter
from quenching.specs.portal import is_loopback
from quenching.specs.sync import (EventError, SyncCache, SyncedBackend, apply_event, cache_dir,
                                  reconcile_ttl)

ENV_SECRET = "QUENCHING_SYNC_HOOK_SECRET"
HOOK_PATH = "/hooks/azure"
MAX_BODY = 1 << 20


def _synced(root: str, out: Emitter, as_json: bool):
    """The synced backend for the repository, or `(None, exit code)` after a refusal. The verbs
    work whether or not `QUENCHING_SPECS_SYNC` is set: they are the cache's own commands."""
    backend, err = open_backend(root)
    if err:
        return None, out.emit_err(as_json, err)
    if isinstance(backend, SyncedBackend):
        return backend, 0
    if getattr(backend, "name", "") != "azure-boards":
        return None, out.emit_err(as_json, {
            "code": "sp-sync-backend", "exit": 2,
            "message": f"the sync cache serves the azure-boards backend only; this repository "
                       f"uses '{getattr(backend, 'name', '?')}'"})
    return SyncedBackend(backend, SyncCache(cache_dir(backend.org, backend.project))), 0


def secret_ok(header: str | None, secret: str) -> bool:
    """`Authorization: Basic base64(user:secret)`; the user name is ignored, as Azure DevOps
    lets the hook author choose it."""
    if not header or not secret:
        return False
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    _user, _sep, given = decoded.partition(":")
    return hmac.compare_digest(given.encode(), secret.encode())


def make_receiver(synced: SyncedBackend, host: str, port: int, secret: str):
    """A bound, not yet serving `ThreadingHTTPServer`. Loopback hosts only."""
    if not is_loopback(host):
        raise ValueError(f"the receiver binds loopback only; '{host}' is refused")
    cache = synced.cache

    class Handler(BaseHTTPRequestHandler):
        server_version = "cq-sync"

        def log_message(self, fmt, *a):  # nothing is logged: a request line may carry a secret
            pass

        def _reply(self, code: int, body: dict, headers: dict | None = None) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _not_post(self) -> None:
            self._reply(405, {"ok": False, "error": "POST only"}, {"Allow": "POST"})

        do_GET = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _not_post

        def do_POST(self) -> None:
            if self.path.split("?", 1)[0] != HOOK_PATH:
                return self._reply(404, {"ok": False, "error": "not found"})
            if not secret_ok(self.headers.get("Authorization"), secret):
                return self._reply(401, {"ok": False, "error": "unauthorized"},
                                   {"WWW-Authenticate": 'Basic realm="cq-sync"'})
            try:
                length = int(self.headers.get("Content-Length", ""))
            except ValueError:
                return self._reply(411, {"ok": False, "error": "Content-Length required"})
            if length < 0 or length > MAX_BODY:
                return self._reply(413, {"ok": False, "error": "body too large"})
            try:
                payload = json.loads(self.rfile.read(length) or b"null")
                result = apply_event(cache, payload)
            except (ValueError, EventError) as e:
                return self._reply(400, {"ok": False, "error": str(e)})
            self._reply(200, {"ok": True, **result})

    return ThreadingHTTPServer((host, port), Handler)


def start_reconciler(synced: SyncedBackend, interval: float) -> threading.Event:
    """Reconcile every `interval` seconds on a daemon thread; set the returned event to stop."""
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            try:
                synced.reconcile()
            except Exception:  # a failed pass is retried on the next tick; the cache stays valid
                pass

    threading.Thread(target=loop, name="cq-sync-reconcile", daemon=True).start()
    return stop


def cmd_sync(args, root: str, out: Emitter) -> int:
    verb = getattr(args, "sync_cmd", None)
    if not verb:
        return out.emit_err(args.json, {"code": "sp-sync-verb", "exit": 2,
                                        "message": "choose: status, reconcile, apply or serve"})
    if verb == "serve" and not is_loopback(args.host):
        return out.emit_err(args.json, {
            "code": "sp-serve-host", "exit": 2,
            "message": f"the receiver binds loopback only; '{args.host}' is refused"})
    synced, code = _synced(root, out, args.json)
    if synced is None:
        return code
    if verb == "status":
        info = {"ok": True, "ttl": synced.ttl, **synced.cache.status()}
        out.emit(args.json, info, f"cache {info['path']}: {info['items']} item(s), "
                                  f"{info['stale']} stale, reconciled at {info['reconciledAt']}")
        return 0
    if verb == "reconcile":
        result = {"ok": True, **synced.reconcile()}
        out.emit(args.json, result, "reconciled: " + ", ".join(
            f"{k} {v}" for k, v in result.items() if k != "ok"))
        return 0
    if verb == "apply":
        try:
            result = apply_event(synced.cache, json.loads(sys.stdin.read() or "null"))
        except (ValueError, EventError) as e:
            return out.emit_err(args.json, {"code": "sp-sync-event", "exit": 2, "message": str(e)})
        out.emit(args.json, {"ok": True, **result}, f"{result['event']} #{result['id']}: "
                                                    f"{result['action']}")
        return 0
    return _serve(args, synced, out)


def _serve(args, synced: SyncedBackend, out: Emitter) -> int:
    secret = os.environ.get(ENV_SECRET, "")
    if not secret:
        return out.emit_err(args.json, {
            "code": "sp-sync-secret", "exit": 2,
            "message": f"set {ENV_SECRET} in the environment (never as an argument)"})
    server = make_receiver(synced, args.host, args.port, secret)
    port = server.server_address[1]
    stop = start_reconciler(synced, max(1.0, reconcile_ttl()))
    info = {"ok": True, "host": args.host, "port": port, "path": HOOK_PATH,
            "backend": synced.name, "cache": synced.cache.directory}
    out.emit(args.json, info, f"quenching sync receiver on http://{args.host}:{port}{HOOK_PATH}"
                              f"\nCtrl-C to stop")
    sys.stdout.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0
