"""The MCP protocol core every quenching server mounts: JSON-RPC over stdio, nothing about a verb.

A server is a *surface* handed to `handle` and `serve`: an object that names itself and answers
`list_tools()`, `has_tool(name)`, `call_tool(name, args, root, session)`, `list_resources(root)`,
`resource_templates()` and `read_resource(uri, root)`. `cq specs mcp` mounts the specs surface,
`cq mcp` mounts the whole catalog; both share this loop, the elicitation handshake, the size guard
and the in-process dispatch, so a tool behaves the same under either.

* Transport: JSON-RPC 2.0, one message per line on stdin/stdout. stdout carries protocol lines
  ONLY — every verb runs with stdout and stderr captured, and `serve` moves file descriptors 0
  and 1 away from a verb's subprocesses so nothing they write or read reaches the protocol.
* Elicitation: a client that declares the `elicitation` capability can be asked, mid-call, to
  confirm a human decision; `Session.confirm` is how a surface asks.
* Size: `guard` cuts any answer above `MAX_CHARS` with `truncated: true`.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys

from quenching.common.output import OK
from quenching.common.version import VERSION

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_CHARS = 60_000
NOT_FOUND, BAD_PARAMS = -32002, -32602


class ToolArgs(ValueError):
    """A tool argument that fails validation — reported as a tool error, never a crash."""


class ResourceError(Exception):
    """A resource read that fails: a JSON-RPC error with the MCP code, never a crash."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class Session:
    """What one stdio connection negotiated: whether the client can ask the human, and how."""

    def __init__(self, ask=None, elicitation: bool = False):
        self.ask, self.elicitation = ask, elicitation

    def confirm(self, message: str) -> bool | None:
        """True on an accept, False on a decline/cancel, None when the human cannot be asked."""
        if not (self.elicitation and self.ask):
            return None
        reply = self.ask(message)
        if not isinstance(reply, dict) or reply.get("action") != "accept":
            return False
        return (reply.get("content") or {}).get("confirm") is True


def dump(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def dispatch(main, argv: list[str], stdin: str | None = None,
             cwd: str | None = None) -> tuple[int, str, str]:
    """`main(argv)` in this process: `(exit code, stdout, stderr)`.

    The verb sees an empty stdin unless one is given, never the protocol's; its output is
    captured; a `SystemExit` becomes its exit code; the working directory is restored whatever
    the verb did with it."""
    out, err = io.StringIO(), io.StringIO()
    held_stdin, held_cwd = sys.stdin, os.getcwd()
    sys.stdin = io.StringIO(stdin or "")
    try:
        if cwd is not None:
            os.chdir(cwd)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
    finally:
        sys.stdin = held_stdin
        os.chdir(held_cwd)
    return (code if isinstance(code, int) else 0), out.getvalue(), err.getvalue().strip()


def guard(payload: dict, hint: str) -> dict:
    """The last line of defence: an answer above the ceiling is cut and says so."""
    text = dump(payload)
    if len(text) <= MAX_CHARS:
        return payload
    return {"ok": payload.get("ok", True), "truncated": True, "chars": len(text),
            "kept": MAX_CHARS, "hint": hint, "text": text[:MAX_CHARS]}


class Composite:
    """Several surfaces served as one: tools and resources are the union, a call goes to the
    first part that names the tool, a read to the first part that knows the uri."""

    def __init__(self, name: str, parts: list):
        self.name, self.parts = name, parts

    def _owner(self, name: str):
        return next((p for p in self.parts if p.has_tool(name)), None)

    def has_tool(self, name: str) -> bool:
        return self._owner(name) is not None

    def list_tools(self) -> list[dict]:
        return [tool for part in self.parts for tool in part.list_tools()]

    def call_tool(self, name, args, root, session):
        return self._owner(name).call_tool(name, args, root, session)

    def list_resources(self, root) -> list[dict]:
        return [res for part in self.parts for res in part.list_resources(root)]

    def resource_templates(self) -> list[dict]:
        return [tpl for part in self.parts for tpl in part.resource_templates()]

    def read_resource(self, uri, root):
        for part in self.parts[:-1]:
            try:
                return part.read_resource(uri, root)
            except ResourceError as exc:
                if exc.code != NOT_FOUND:
                    raise
        return self.parts[-1].read_resource(uri, root)


def _result(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(message, root: str, session: Session | None, surface):
    """One JSON-RPC message in, its response out (`None` for a notification)."""
    session = session if session is not None else Session()
    if isinstance(message, list):
        replies = [r for r in (handle(m, root, session, surface) for m in message)
                   if r is not None]
        return replies or None
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" \
            or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None,
                      -32600, "invalid request")
    method, request_id = message["method"], message.get("id")
    params = message.get("params")
    if "id" not in message:
        return None
    if params is None:
        params = {}
    elif not isinstance(params, dict):
        return _error(request_id, BAD_PARAMS, "params must be an object")
    if method == "initialize":
        asked = params.get("protocolVersion")
        capabilities = params.get("capabilities")
        if capabilities is None:
            capabilities = {}
        elif not isinstance(capabilities, dict):
            return _error(request_id, BAD_PARAMS, "capabilities must be an object")
        session.elicitation = isinstance(capabilities.get("elicitation"), dict)
        return _result(request_id, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {"subscribe": False, "listChanged": False}},
            "serverInfo": {"name": surface.name, "version": VERSION}})
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": surface.list_tools()})
    if method == "resources/list":
        return _result(request_id, {"resources": surface.list_resources(root)})
    if method == "resources/templates/list":
        return _result(request_id, {"resourceTemplates": surface.resource_templates()})
    if method == "resources/read":
        try:
            return _result(request_id,
                           {"contents": surface.read_resource(params.get("uri"), root)})
        except ResourceError as exc:
            return _error(request_id, exc.code, exc.message)
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if not isinstance(name, str) or not surface.has_tool(name) or not isinstance(args, dict):
            return _error(request_id, BAD_PARAMS, f"unknown tool or bad arguments: {name!r}")
        payload, failed = surface.call_tool(name, args, root, session)
        return _result(request_id, {"content": [{"type": "text", "text": dump(payload)}],
                                    "isError": failed})
    return _error(request_id, -32601, f"method not found: {method}")


def _emit(stdout, message: dict) -> None:
    stdout.write(dump(message) + "\n")
    stdout.flush()


def serve(root: str, surface, stdin=None, stdout=None) -> int:
    """The stdio loop: one JSON message per line until EOF."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    lines = iter(stdin)
    counter = [0]

    def ask(message: str):
        """Server-to-client `elicitation/create`, read synchronously: the loop is serial, so the
        next line answering this id is the human's reply. Anything else meanwhile is dropped."""
        counter[0] += 1
        request_id = f"elicit-{counter[0]}"
        _emit(stdout, {"jsonrpc": "2.0", "id": request_id, "method": "elicitation/create",
                       "params": {"message": message, "requestedSchema": {
                           "type": "object",
                           "properties": {"confirm": {"type": "boolean", "title": "Confirm"}},
                           "required": ["confirm"]}}})
        for line in lines:
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            if isinstance(reply, dict) and reply.get("id") == request_id:
                result = reply.get("result")
                return result if isinstance(result, dict) else {"action": "cancel"}
        return {"action": "cancel"}

    session = Session(ask=ask)
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            _emit(stdout, _error(None, -32700, "parse error"))
            continue
        try:
            reply = handle(message, root, session, surface)
        except Exception as exc:  # one request never takes the whole stdio session down
            request_id = message.get("id") if isinstance(message, dict) else None
            reply = _error(request_id, -32603, f"internal error: {type(exc).__name__}")
        if reply is not None:
            _emit(stdout, reply)
    return OK


def private_stdio():
    """`(stdin, stdout)` for the protocol on private descriptors, with 0 and 1 moved away.

    A verb that spawns a subprocess hands it descriptors 0 and 1 unless it says otherwise; on a
    protocol pipe that child would read the client's next request or write into the stream. The
    protocol keeps duplicates, fd 0 becomes /dev/null and fd 1 becomes stderr. A stream with no
    real descriptor (a test double) is returned as it is."""
    try:
        in_fd, out_fd = sys.stdin.fileno(), sys.stdout.fileno()
    except (AttributeError, OSError, ValueError):
        return sys.stdin, sys.stdout
    sys.stdout.flush()
    proto_in = os.fdopen(os.dup(in_fd), "r", encoding="utf-8")
    proto_out = os.fdopen(os.dup(out_fd), "w", encoding="utf-8")
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, in_fd)
    os.close(null)
    os.dup2(sys.stderr.fileno(), out_fd)
    return proto_in, proto_out


def run_stdio(root: str, surface) -> int:
    """The entry point a command mounts: the loop over the process's own stdio."""
    stdin, stdout = private_stdio()
    return serve(root, surface, stdin, stdout)
