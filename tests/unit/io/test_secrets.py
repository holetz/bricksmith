"""Secrets are resolved at point of use and never reach reports, errors, logs or reprs."""

from __future__ import annotations

import base64
import json
import logging
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import FlakySink, sink_db, source_db, sqlite_sink, sqlite_source

from bricksmith.io import (
    IoRetry,
    MemoryBookmarkStore,
    Redactor,
    SecretError,
    SecretRef,
    SqlConnector,
    TransferFailedError,
    transfer,
    workspace_resolver,
)
from bricksmith.io.errors import PermanentIoError

SECRET = "pw-9f3a-synthetic"  # noqa: S105 - synthetic marker searched for in outputs


class FakeSecrets:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_secret(self, *, scope: str, key: str) -> SimpleNamespace:
        self.calls.append((scope, key))
        return SimpleNamespace(value=base64.b64encode(SECRET.encode()).decode())


def test_reference_parsing() -> None:
    assert str(SecretRef.parse("secret:app/db-password")) == "secret:app/db-password"
    for bad in ("secret:app", "secret:/key", "secret:a b/c"):
        with pytest.raises(SecretError):
            SecretRef.parse(bad)


def test_resolution_happens_at_connect_and_nowhere_else() -> None:
    client = FakeSecrets()
    seen: dict[str, object] = {}

    def driver(**params: object) -> object:
        seen.update(params)
        msg = f"password authentication failed for password {params['password']!r}"
        raise sqlite3.OperationalError(msg)

    connector = SqlConnector(
        driver, {"host": "db.example.test", "password": "secret:app/pw"}, resolver=workspace_resolver(client)
    )
    assert client.calls == []  # constructing resolves nothing
    with pytest.raises(Exception, match="redacted") as caught:
        connector.connect()
    assert client.calls == [("app", "pw")]
    assert seen["password"] == SECRET  # the driver received the value
    assert SECRET not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__
    assert SECRET not in json.dumps(connector.describe())
    assert connector.describe() == {"host": "db.example.test", "password": "<secret-ref>"}
    assert SECRET not in repr(connector.redactor)


def test_literal_credential_like_parameters_are_masked_in_reports() -> None:
    connector = SqlConnector(sqlite3.connect, {"database": ":memory:", "api_token": "literal-token"})
    assert connector.describe() == {"api_token": "<redacted>", "database": ":memory:"}


def test_missing_resolver_and_unreadable_secret_fail_without_values() -> None:
    with pytest.raises(SecretError, match="no secret resolver"):
        SqlConnector(sqlite3.connect, {"database": "secret:a/b"}).connect()

    class Broken:
        def get_secret(self, **_: object) -> object:
            msg = f"token {SECRET} rejected"
            raise RuntimeError(msg)

    with pytest.raises(SecretError) as caught:
        workspace_resolver(Broken())(SecretRef("a", "b"))
    assert SECRET not in str(caught.value)


def test_transfer_outputs_never_contain_the_secret(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source_path, sink_path = source_db(tmp_path / "s.db"), sink_db(tmp_path / "d.db")
    redactor = Redactor()
    redactor.add(SECRET)

    class LeakySink(FlakySink):
        def write(self, batch: object, token: str) -> None:  # noqa: ARG002 - interface
            self.calls += 1
            msg = f"cannot reach host with {SECRET}"
            raise PermanentIoError(redactor.scrub(msg))

    caplog.set_level(logging.DEBUG, logger="bricksmith.io")
    with pytest.raises(TransferFailedError) as caught:
        transfer(
            sqlite_source(source_path),
            LeakySink(sqlite_sink(sink_path), set()),
            store=MemoryBookmarkStore(),
            key="k",
            retry=IoRetry(max_attempts=2, delay=0, sleep=lambda _: None),
        )
    assert SECRET not in str(caught.value)
    assert SECRET not in json.dumps(caught.value.progress, default=str)
    assert SECRET not in caplog.text
    report = transfer(
        sqlite_source(source_path), sqlite_sink(sink_path), store=MemoryBookmarkStore(), key="ok"
    ).to_dict()
    assert SECRET not in json.dumps(report, default=str)
    assert set(report) >= {"key", "rows", "bookmark_after"}
