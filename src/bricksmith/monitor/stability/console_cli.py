"""``bricksmith console``: serve the stability console locally, or as a Databricks App with per-user authorization."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK, UsageError
from bricksmith.monitor.stability.console import TOKEN_HEADER, Console, make_server
from bricksmith.monitor.stability.ops import Operations
from bricksmith.monitor.stability.sql import SqlRunner

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable
    from http.server import ThreadingHTTPServer

DEFAULT_PORT = 8765


def _operations(args: argparse.Namespace) -> Callable[[str | None], Operations]:
    """Return the factory of operations: one explicit profile locally, the caller's own token in an app."""
    from bricksmith.clients import Clients, import_extra  # noqa: PLC0415 - heavy integration, loaded where used

    def build(workspace: object) -> Operations:
        return Operations(
            SqlRunner(workspace, args.warehouse),
            args.scope,
            prefix=args.prefix,
            jobs=workspace.jobs,  # type: ignore[attr-defined]
        )

    if args.app:
        host = os.environ.get("DATABRICKS_HOST", "")
        if not host:
            msg = f"--app needs DATABRICKS_HOST; the user's token arrives in the {TOKEN_HEADER} header"
            raise UsageError(msg)
        sdk = import_extra("databricks.sdk", "sdk")
        return lambda token: build(sdk.WorkspaceClient(host=host, token=token, auth_type="pat"))
    operations = build(Clients(context_from_args(args)).workspace())
    return lambda _token: operations


def build_server(args: argparse.Namespace) -> ThreadingHTTPServer:
    """Build the server of the console from the parsed options."""
    if not args.warehouse or not args.scope:
        msg = "the console needs --warehouse and --scope (catalog.schema of the stability tables)"
        raise UsageError(msg)
    host = "0.0.0.0" if args.app else args.host  # noqa: S104 - an app listens for the platform proxy
    port = int(os.environ.get("DATABRICKS_APP_PORT") or args.port) if args.app else args.port
    allowed = (f"localhost:{port}", f"127.0.0.1:{port}") if not args.app else tuple(args.allow_host)
    if args.app and not allowed:
        msg = "--app needs --allow-host: the host name the platform proxy forwards"
        raise UsageError(msg)
    console = Console(_operations(args), allowed_hosts=allowed, job_id=args.job, on_behalf_of=args.app)
    return make_server(console, host, port)


def _console(args: argparse.Namespace) -> int:
    server = build_server(args)
    host, port = server.server_address[:2]
    emit(args, {"url": f"http://{host}:{port}"}, f"stability console on http://{host}:{port} (Ctrl+C stops it)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``console``."""
    parser = subparsers.add_parser(
        "console", help="serve the stability operations console (local, or a Databricks App)", parents=[global_parent()]
    )
    parser.add_argument("--warehouse", help="SQL warehouse id every query runs on")
    parser.add_argument("--scope", help="catalog.schema of the stability tables")
    parser.add_argument("--prefix", default="stability_", help="table name prefix (default stability_)")
    parser.add_argument(
        "--job", help="workspace job id the reprocess action starts (its runner task takes --reprocess)"
    )
    parser.add_argument("--host", default="127.0.0.1", help="address to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port to bind (default {DEFAULT_PORT})")
    parser.add_argument("--app", action="store_true", help="run as a Databricks App: authorize as the calling user")
    parser.add_argument(
        "--allow-host", action="append", default=[], help="with --app: a Host header the proxy sends (repeatable)"
    )
    parser.set_defaults(handler=_console)
