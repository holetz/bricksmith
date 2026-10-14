"""Interrupted transfer and replay against a disposable PostgreSQL container (skipped without Docker)."""

from __future__ import annotations

import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import FlakySink

from bricksmith.io import (
    Limits,
    MemoryBookmarkStore,
    SecretRef,
    SqlConnector,
    SqlSink,
    SqlSource,
    TransferFailedError,
    transfer,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

IMAGE = "postgres:16-alpine"
pytestmark = [pytest.mark.slow, pytest.mark.docker]


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False, timeout=30).returncode == 0


@pytest.fixture(scope="module")
def postgres() -> Iterator[dict[str, object]]:
    if not _docker_ready():
        pytest.skip("not run: Docker is unavailable")
    psycopg = pytest.importorskip("psycopg")
    password = secrets.token_hex(8)
    name = f"bricksmith-pg-{secrets.token_hex(4)}"
    command = ["docker", "run", "-d", "--rm", "--name", name, "-e", f"POSTGRES_PASSWORD={password}"]
    run = subprocess.run(
        [*command, "-p", "127.0.0.1::5432", IMAGE], capture_output=True, text=True, check=False, timeout=600
    )
    if run.returncode != 0:
        pytest.skip("not run: the PostgreSQL image could not be started")
    try:
        port = subprocess.run(
            ["docker", "port", name, "5432/tcp"], capture_output=True, text=True, check=True, timeout=30
        ).stdout.split(":")[-1].strip()  # fmt: skip
        params: dict[str, object] = {
            "host": "127.0.0.1", "port": int(port), "user": "postgres", "dbname": "postgres", "password": password,
        }  # fmt: skip
        deadline = time.monotonic() + 60
        while True:
            try:
                psycopg.connect(**params, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        yield params
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False, timeout=60)


def test_interrupted_transfer_then_replay_is_exactly_once(postgres: dict[str, object]) -> None:
    psycopg = pytest.importorskip("psycopg")

    password = str(postgres["password"])
    resolved = {k: v for k, v in postgres.items() if k != "password"}
    params = {**resolved, "password": "secret:test/pg"}

    def connector() -> SqlConnector:
        return SqlConnector(
            psycopg.connect, params, paramstyle="format",
            resolver=lambda ref: password if ref == SecretRef("test", "pg") else "",
        )  # fmt: skip

    admin = psycopg.connect(**postgres)  # type: ignore[arg-type]
    admin.execute("CREATE TABLE src (id BIGINT PRIMARY KEY, label TEXT)")
    admin.execute("CREATE TABLE dst (id BIGINT PRIMARY KEY, label TEXT)")
    admin.execute("INSERT INTO src SELECT g, 'row-' || g FROM generate_series(1, 50) AS g")
    admin.commit()

    store = MemoryBookmarkStore()
    limits = Limits(batch_rows=8)

    def source() -> SqlSource:
        return SqlSource(connector(), "src", columns=("id", "label"), bookmark_column="id", name="src")

    def sink() -> SqlSink:
        return SqlSink(connector(), "dst", columns=("id", "label"), name="dst")

    with pytest.raises(TransferFailedError) as caught:
        transfer(source(), FlakySink(sink(), {4}), store=store, key="pg", limits=limits)
    assert password not in str(caught.value)
    assert store.load("pg").bookmark is None
    assert admin.execute("SELECT count(*) FROM dst").fetchone() == (24,)
    admin.commit()

    report = transfer(source(), sink(), store=store, key="pg", limits=limits)
    assert report.resumed
    assert report.rows == 26
    assert store.load("pg").bookmark == 50
    rows = admin.execute("SELECT id, label FROM dst ORDER BY id").fetchall()
    assert rows == [(i, f"row-{i}") for i in range(1, 51)]
    again = transfer(source(), sink(), store=store, key="pg", limits=limits)
    assert again.rows == 0
    assert password not in repr(report.to_dict())
    admin.close()
