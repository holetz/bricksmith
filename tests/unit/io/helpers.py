"""Shared synthetic fixtures for the external I/O tests: SQLite tables and fault-injecting wrappers."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from bricksmith.io import SqlConnector, SqlSink, SqlSource
from bricksmith.io.errors import PermanentIoError, TransientIoError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from bricksmith.io import Batch


def make_db(path: Path, ddl: str, rows: Sequence[tuple[object, ...]] | None = None, insert: str = "") -> str:
    """Create a SQLite file with ``ddl``; load ``rows`` with ``insert`` when given."""
    conn = sqlite3.connect(path)
    conn.execute(ddl)
    if rows:
        conn.executemany(insert, rows)
    conn.commit()
    conn.close()
    return str(path)


def source_db(path: Path, count: int = 10) -> str:
    """Return a source database with ``count`` rows ``(id, label)``."""
    rows = [(i, f"row-{i}") for i in range(1, count + 1)]
    return make_db(path, "CREATE TABLE src (id INTEGER PRIMARY KEY, label TEXT)", rows, "INSERT INTO src VALUES (?, ?)")


def sink_db(path: Path) -> str:
    """Return an empty sink database."""
    return make_db(path, "CREATE TABLE dst (id INTEGER PRIMARY KEY, label TEXT)")


def dst_rows(path: str) -> list[tuple[int, str]]:
    """Return the sink table content ordered by id."""
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT id, label FROM dst ORDER BY id").fetchall()
    finally:
        conn.close()


def append_rows(path: str, start: int, stop: int) -> None:
    """Add source rows ``start..stop`` inclusive."""
    conn = sqlite3.connect(path)
    conn.executemany("INSERT INTO src VALUES (?, ?)", [(i, f"row-{i}") for i in range(start, stop + 1)])
    conn.commit()
    conn.close()


def sqlite_source(path: str, **kwargs: object) -> SqlSource:
    """Return a source over ``src`` with ``id`` as bookmark."""
    connector = SqlConnector(sqlite3.connect, {"database": path})
    return SqlSource(connector, "src", columns=("id", "label"), bookmark_column="id", name="src", **kwargs)  # type: ignore[arg-type]


def sqlite_sink(path: str) -> SqlSink:
    """Return a sink over ``dst``."""
    return SqlSink(SqlConnector(sqlite3.connect, {"database": path}), "dst", columns=("id", "label"), name="dst")


class FlakySink:
    """Delegates to a sink but raises on chosen write calls (1-based) with a chosen error."""

    def __init__(self, inner: SqlSink, fail_on: set[int], *, transient: bool = False) -> None:
        self.inner = inner
        self.name = inner.name
        self.fail_on = fail_on
        self.transient = transient
        self.calls = 0

    def committed(self, token: str) -> object:
        return self.inner.committed(token)

    def write(self, batch: Batch, token: str) -> None:
        self.calls += 1
        if self.calls in self.fail_on:
            msg = "connection lost" if self.transient else "disk full"
            raise (TransientIoError if self.transient else PermanentIoError)(msg)
        self.inner.write(batch, token)

    def describe(self) -> dict[str, object]:
        return self.inner.describe()

    def close(self) -> None:
        self.inner.close()


class FlakySource:
    """Delegates to a source but raises a transient error on the Nth ``batches`` call (1-based)."""

    def __init__(self, inner: object, fail_calls: set[int]) -> None:
        self.inner = inner
        self.name = inner.name  # type: ignore[attr-defined]
        self.columns = inner.columns  # type: ignore[attr-defined]
        self.bookmark_column = inner.bookmark_column  # type: ignore[attr-defined]
        self.fail_calls = fail_calls
        self.calls = 0

    def batches(self, *, after: object, max_rows: int | None, batch_rows: int) -> Iterator[Batch]:
        self.calls += 1
        if self.calls in self.fail_calls:
            msg = "temporary outage"
            raise TransientIoError(msg)
        return self.inner.batches(after=after, max_rows=max_rows, batch_rows=batch_rows)  # type: ignore[attr-defined]

    def describe(self) -> dict[str, object]:
        return self.inner.describe()  # type: ignore[attr-defined]

    def close(self) -> None:
        self.inner.close()  # type: ignore[attr-defined]
