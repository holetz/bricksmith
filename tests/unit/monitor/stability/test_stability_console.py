"""The console: routes over the operations, the request guards, the on-behalf-of token and a real socket."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import argparse
import json
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.cli.main import main
from bricksmith.errors import UsageError
from bricksmith.monitor.stability import console_cli
from bricksmith.monitor.stability.console import PAGE, Console, make_server
from bricksmith.monitor.stability.ops import Operations

if TYPE_CHECKING:
    from collections.abc import Iterator

HOST = "localhost:8765"
JSON = {"host": HOST, "content-type": "application/json"}


class _Sql:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def run(self, statement: str, params: Any = None) -> list[dict[str, Any]]:  # noqa: ANN401
        self.calls.append((statement, dict(params or {})))
        return [{"dataset": "orders", "status": "red"}] if "FROM" in statement else []


class _Jobs:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run_now(self, **kwargs: Any) -> SimpleNamespace:  # noqa: ANN401
        self.calls.append(kwargs)
        return SimpleNamespace(run_id=5)


def _console(**kwargs: Any) -> tuple[Console, _Sql, _Jobs, list[str | None]]:  # noqa: ANN401
    sql, jobs, tokens = _Sql(), _Jobs(), []

    def ops_for(token: str | None) -> Operations:
        tokens.append(token)
        return Operations(sql, "main.mon", jobs=jobs, now=lambda: "2026-02-01T00:00:00+00:00")

    return Console(ops_for, allowed_hosts=(HOST,), **kwargs), sql, jobs, tokens


def _body(reply: tuple[int, str, bytes]) -> Any:  # noqa: ANN401
    return json.loads(reply[2])


def test_the_page_and_the_reads() -> None:
    console, sql, _, _ = _console()
    status, kind, page = console.handle("GET", "/", {"host": HOST})
    assert status == 200 and kind.startswith("text/html") and page.decode() == PAGE and "textContent" in PAGE
    assert "innerHTML" not in PAGE  # the page never writes markup from data
    assert _body(console.handle("GET", "/api/datasets", {"host": HOST}))["datasets"][0]["dataset"] == "orders"
    assert console.handle("GET", "/api/series?dataset=orders&metric=psi", {"host": HOST})[0] == 200
    assert sql.calls[-1][1] == {"dataset": "orders", "metric": "psi"}
    for name in ("alerts", "marks", "actions", "runs"):
        assert console.handle("GET", f"/api/{name}?dataset=orders", {"host": HOST})[0] == 200
    assert console.handle("GET", "/api/nope", {"host": HOST})[0] == 404
    assert console.handle("GET", "/elsewhere", {"host": HOST})[0] == 404


def test_changes_go_through_the_verbs_rows() -> None:
    console, sql, jobs, _ = _console(job_id="42")
    mark = {"dataset": "orders", "period": "2025-04", "mark": "excluded", "reason": "migration", "author": "ana"}
    assert _body(console.handle("POST", "/api/mark", JSON, json.dumps(mark).encode()))["mark"]["mark"] == "excluded"
    assert sql.calls[-1][0].startswith("INSERT INTO main.mon.stability_marks")
    undo = {k: v for k, v in mark.items() if k != "mark"}
    assert console.handle("POST", "/api/unmark", JSON, json.dumps(undo).encode())[0] == 200
    assert sql.calls[-1][1]["mark"] == "unmarked"
    act = {"dataset": "orders", "alert_id": "a1", "action": "analysis", "reason": "looking", "author": "ana"}
    assert console.handle("POST", "/api/action", JSON, json.dumps(act).encode())[0] == 200
    assert sql.calls[-1][0].startswith("INSERT INTO main.mon.stability_actions")
    assert console.handle("POST", "/api/reprocess", JSON, b'{"periods": "2026-01..2026-02"}')[0] == 200
    assert jobs.calls == [{"job_id": 42, "job_parameters": {"reprocess": "2026-01..2026-02"}}]


def test_the_author_defaults_to_the_forwarded_user_and_refusals_are_400() -> None:
    console, sql, _, _ = _console()
    body = json.dumps({"dataset": "orders", "period": "2025-04", "mark": "accepted", "reason": "ok"}).encode()
    assert console.handle("POST", "/api/mark", {**JSON, "X-Forwarded-Email": "bo@example.com"}, body)[0] == 200
    assert sql.calls[-1][1]["author"] == "bo@example.com"
    refused = console.handle("POST", "/api/mark", JSON, body)  # no author at all
    assert refused[0] == 400 and "author" in _body(refused)["error"]
    assert console.handle("POST", "/api/reprocess", JSON, b'{"periods": "2026-01"}')[0] == 400  # no job configured
    assert (
        "missing: period" in _body(console.handle("POST", "/api/mark", JSON, b'{"dataset": "o", "mark": "x"}'))["error"]
    )
    assert console.handle("POST", "/api/mark", JSON, b"[1]")[0] == 400
    assert console.handle("POST", "/api/mark", JSON, b"{")[0] == 400
    assert console.handle("POST", "/api/mark", JSON, b" " * 70000)[0] == 413


def test_guards_refuse_foreign_hosts_origins_and_form_posts() -> None:
    console, sql, _, _ = _console()
    assert console.handle("GET", "/api/datasets", {"host": "evil.example:8765"})[0] == 403
    assert console.handle("GET", "/api/datasets", {})[0] == 403
    body = b'{"dataset": "orders"}'
    assert console.handle("POST", "/api/mark", {"host": HOST, "content-type": "text/plain"}, body)[0] == 415
    form = {"host": HOST, "content-type": "application/x-www-form-urlencoded"}
    assert console.handle("POST", "/api/mark", form, body)[0] == 415
    assert console.handle("POST", "/api/mark", {**JSON, "Origin": "http://evil.example"}, body)[0] == 403
    assert (
        console.handle("POST", "/api/mark", {**JSON, "Origin": f"http://{HOST}"}, body)[0] == 400
    )  # past the guard, short of fields
    assert console.handle("DELETE", "/api/mark", JSON)[0] == 405
    assert sql.calls == []


def test_an_unexpected_failure_shows_nothing_and_on_behalf_of_needs_the_user_token() -> None:
    def boom(_: str | None) -> Operations:
        msg = "secret detail"
        raise RuntimeError(msg)

    broken = Console(boom, allowed_hosts=(HOST,))
    reply = broken.handle("GET", "/api/datasets", {"host": HOST})
    assert reply[0] == 500 and b"secret" not in reply[2]
    console, _, _, tokens = _console(on_behalf_of=True)
    assert console.handle("GET", "/api/datasets", {"host": HOST})[0] == 401 and tokens == []
    assert console.handle("GET", "/api/datasets", {"host": HOST, "X-Forwarded-Access-Token": "tok"})[0] == 200
    assert tokens == ["tok"]
    local, _, _, local_tokens = _console()
    local.handle("GET", "/api/datasets", {"host": HOST, "x-forwarded-access-token": "ignored"})
    assert local_tokens == [None]  # a token is used only when the console runs on behalf of the user


@pytest.fixture
def served() -> Iterator[tuple[str, _Sql]]:
    sql, jobs = _Sql(), _Jobs()
    holder: dict[str, str] = {}
    console = Console(
        lambda _t: Operations(sql, "main.mon", jobs=jobs),
        allowed_hosts=(),  # filled once the port is known
    )
    server = make_server(console, "127.0.0.1", 0)
    port = server.server_address[1]
    holder["host"] = f"127.0.0.1:{port}"
    console.allowed_hosts = (holder["host"],)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://{holder['host']}", sql
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_a_real_request_round_trip(served: tuple[str, _Sql]) -> None:
    url, sql = served
    with urllib.request.urlopen(f"{url}/api/datasets", timeout=5) as response:  # noqa: S310 - localhost
        assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
        assert json.loads(response.read())["datasets"][0]["dataset"] == "orders"
    request = urllib.request.Request(  # noqa: S310
        f"{url}/api/mark", data=b'{"dataset": "o"}', headers={"Content-Type": "text/plain"}, method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=5)  # noqa: S310
    assert refused.value.code == 415 and len(sql.calls) == 1


def test_the_command_builds_a_local_server_and_refuses_what_it_cannot_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    ns = argparse.Namespace(
        warehouse="wh", scope="main.mon", prefix="stability_", job="7", host="127.0.0.1", port=0, app=False,
        allow_host=[],
    )  # fmt: skip
    monkeypatch.setattr(console_cli, "_operations", lambda _a: lambda _t: None)
    server = console_cli.build_server(ns)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()
    with pytest.raises(UsageError, match="--warehouse"):
        console_cli.build_server(argparse.Namespace(**{**vars(ns), "warehouse": ""}))
    with pytest.raises(UsageError, match="--allow-host"):
        console_cli.build_server(argparse.Namespace(**{**vars(ns), "app": True}))
    assert main(["console"]) == 2  # the group is registered and refuses without a warehouse
