"""Contract, limits, retry, bookmark and replay behaviour on SQLite (always on, offline)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # importlib mode does not put the test folder on the path

from helpers import (
    FlakySink,
    FlakySource,
    append_rows,
    dst_rows,
    sink_db,
    source_db,
    sqlite_sink,
    sqlite_source,
)

from bricksmith.errors import ValidationError
from bricksmith.io import (
    Batch,
    BookmarkState,
    ExternalResources,
    FileBookmarkStore,
    IoRetry,
    LimitExceededError,
    Limits,
    MemoryBookmarkStore,
    Table,
    TransferFailedError,
    classify,
    is_transient,
    transfer,
)
from bricksmith.io.errors import PermanentIoError, TransientIoError

NO_WAIT = IoRetry(max_attempts=3, delay=0, sleep=lambda _: None)


@pytest.fixture
def dbs(tmp_path: Path) -> tuple[str, str]:
    return source_db(tmp_path / "s.db"), sink_db(tmp_path / "d.db")


def test_source_contract_pages_in_bookmark_order(dbs: tuple[str, str]) -> None:
    source = sqlite_source(dbs[0])
    batches = list(source.batches(after=None, max_rows=None, batch_rows=4))
    assert [len(b.rows) for b in batches] == [4, 4, 2]
    assert [(b.start, b.end) for b in batches] == [(None, 4), (4, 8), (8, 10)]
    tail = list(source.batches(after=8, max_rows=None, batch_rows=4))
    assert [r[0] for b in tail for r in b.rows] == [9, 10]
    capped = list(source.batches(after=None, max_rows=5, batch_rows=4))
    assert [len(b.rows) for b in capped] == [4, 1]
    source.close()


def test_sink_contract_is_atomic_and_idempotent_by_token(dbs: tuple[str, str]) -> None:
    sink = sqlite_sink(dbs[1])
    assert sink.committed("t1") is None
    sink.write(Batch(("id", "label"), ((1, "a"), (2, "b")), None, 2), "t1")
    assert sink.committed("t1") == 2
    clash = Batch(("id", "label"), ((3, "c"), (1, "dup")), 2, 3)
    with pytest.raises(PermanentIoError):
        sink.write(clash, "t2")
    assert dst_rows(dbs[1]) == [(1, "a"), (2, "b")]
    assert sink.committed("t2") is None
    sink.close()


def test_full_transfer_advances_bookmark_only_after_success(dbs: tuple[str, str]) -> None:
    store = MemoryBookmarkStore()
    limits = Limits(batch_rows=4)
    report = transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="orders", limits=limits)
    assert (report.rows, report.batches, report.bookmark_after, report.truncated) == (10, 3, 10, False)
    assert dst_rows(dbs[1]) == [(i, f"row-{i}") for i in range(1, 11)]
    # while running the committed bookmark stayed None; only the final save advanced it
    assert [s.bookmark for _, s in store.saves] == [None, None, None, 10]
    assert store.saves[-1][1].pending is False
    again = transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="orders", limits=limits)
    assert (again.rows, again.bookmark_before) == (0, 10)
    append_rows(dbs[0], 11, 13)
    more = transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="orders", limits=limits)
    assert more.rows == 3
    assert len(dst_rows(dbs[1])) == 13


def test_interrupted_transfer_then_replay_is_exactly_once(dbs: tuple[str, str]) -> None:
    store = MemoryBookmarkStore()
    limits = Limits(batch_rows=3)
    flaky = FlakySink(sqlite_sink(dbs[1]), {3})
    with pytest.raises(TransferFailedError) as caught:
        transfer(sqlite_source(dbs[0]), flaky, store=store, key="k", limits=limits)
    assert caught.value.progress["committed_rows"] == 6
    assert store.load("k").bookmark is None  # not advanced
    assert store.load("k").pending_position == 6  # progress kept
    assert len(dst_rows(dbs[1])) == 6
    report = transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="k", limits=limits)
    assert report.resumed
    assert report.rows == 4
    assert dst_rows(dbs[1]) == [(i, f"row-{i}") for i in range(1, 11)]
    assert store.load("k") == BookmarkState(10)


class CrashingStore(MemoryBookmarkStore):
    """Raises after the sink committed a batch but before progress is saved, on the Nth save."""

    def __init__(self, crash_on: int) -> None:
        super().__init__()
        self.crash_on = crash_on
        self.count = 0

    def save(self, key: str, state: BookmarkState) -> None:
        self.count += 1
        if self.count == self.crash_on:
            msg = "process died"
            raise RuntimeError(msg)
        super().save(key, state)


def test_crash_between_sink_commit_and_progress_save_still_exactly_once(dbs: tuple[str, str]) -> None:
    store = CrashingStore(crash_on=2)
    limits = Limits(batch_rows=3)
    with pytest.raises(RuntimeError, match="process died"):
        transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="k", limits=limits)
    assert len(dst_rows(dbs[1])) == 6  # batch 2 is in the sink but not in the progress record
    assert store.load("k").pending_position == 3
    append_rows(dbs[0], 11, 12)  # the source also grew meanwhile
    report = transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="k", limits=limits)
    assert report.replayed_rows == 3
    assert dst_rows(dbs[1]) == [(i, f"row-{i}") for i in range(1, 13)]


def test_replay_trims_a_partially_delivered_batch_when_batch_size_changes(dbs: tuple[str, str]) -> None:
    store = CrashingStore(crash_on=1)
    with pytest.raises(RuntimeError, match="process died"):
        transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="k", limits=Limits(batch_rows=4))
    report = transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="k", limits=Limits(batch_rows=6))
    assert report.replayed_rows == 4
    assert dst_rows(dbs[1]) == [(i, f"row-{i}") for i in range(1, 11)]


def test_file_store_round_trip_and_key_validation(tmp_path: Path) -> None:
    store = FileBookmarkStore(tmp_path / "state")
    assert store.load("a") == BookmarkState()
    state = BookmarkState(5, 5, 8, pending=True)
    store.save("a", state)
    assert store.load("a") == state
    assert json.loads((tmp_path / "state" / "a.json").read_text())["pending"] == {"base": 5, "position": 8}
    with pytest.raises(ValidationError):
        store.save("../escape", state)


def test_row_limit_truncates_and_next_run_continues(dbs: tuple[str, str]) -> None:
    store = MemoryBookmarkStore()
    limits = Limits(max_rows=4, batch_rows=3)
    first = transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="k", limits=limits)
    assert (first.rows, first.truncated, first.bookmark_after) == (4, True, 4)
    second = transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="k", limits=limits)
    assert second.bookmark_after == 8
    third = transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="k", limits=limits)
    assert (third.rows, third.truncated, third.bookmark_after) == (2, False, 10)
    assert len(dst_rows(dbs[1])) == 10


def test_byte_limit_stops_before_the_batch_and_fails_when_first_batch_is_too_big(dbs: tuple[str, str]) -> None:
    store = MemoryBookmarkStore()
    report = transfer(
        sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="k", limits=Limits(max_bytes=60, batch_rows=2)
    )
    assert report.truncated
    assert 0 < report.rows < 10
    assert report.bytes <= 60
    with pytest.raises(LimitExceededError):
        transfer(sqlite_source(dbs[0]), sqlite_sink(dbs[1]), store=store, key="j", limits=Limits(max_bytes=5))


def test_limits_reject_non_positive_values_and_transfer_needs_a_bookmark(dbs: tuple[str, str]) -> None:
    with pytest.raises(ValidationError):
        Limits(max_rows=0)
    with pytest.raises(ValidationError):
        Limits(batch_rows=0)
    from bricksmith.io import SqlConnector, SqlSource  # noqa: PLC0415

    plain = SqlSource(SqlConnector(sqlite3.connect, {"database": dbs[0]}), "src", columns=("id",))
    with pytest.raises(ValidationError, match="bookmark_column"):
        transfer(plain, sqlite_sink(dbs[1]), store=MemoryBookmarkStore(), key="k")


def test_transient_failures_are_retried_from_the_last_committed_batch(dbs: tuple[str, str]) -> None:
    sleeps: list[float] = []
    retry = IoRetry(max_attempts=3, delay=1.0, backoff=2.0, sleep=sleeps.append)
    flaky = FlakySink(sqlite_sink(dbs[1]), {2}, transient=True)
    report = transfer(
        sqlite_source(dbs[0]), flaky, store=MemoryBookmarkStore(), key="k", limits=Limits(batch_rows=4), retry=retry
    )
    assert report.attempts == 2
    assert sleeps == [1.0]
    assert dst_rows(dbs[1]) == [(i, f"row-{i}") for i in range(1, 11)]
    source = FlakySource(sqlite_source(dbs[0]), {1, 2, 3})
    with pytest.raises(TransferFailedError):
        transfer(source, sqlite_sink(sink_db_path(dbs)), store=MemoryBookmarkStore(), key="z", retry=retry)
    assert source.calls == 3


def sink_db_path(dbs: tuple[str, str]) -> str:
    return dbs[1]


def test_permanent_failures_are_not_retried(dbs: tuple[str, str]) -> None:
    sleeps: list[float] = []
    flaky = FlakySink(sqlite_sink(dbs[1]), {1})
    retry = IoRetry(max_attempts=5, delay=1.0, sleep=sleeps.append)
    with pytest.raises(TransferFailedError):
        transfer(sqlite_source(dbs[0]), flaky, store=MemoryBookmarkStore(), key="k", retry=retry)
    assert sleeps == []
    assert flaky.calls == 1


def test_error_classification() -> None:
    assert is_transient(sqlite3.OperationalError("database is locked"))
    assert is_transient(TimeoutError())
    assert not is_transient(sqlite3.IntegrityError("unique"))
    assert not is_transient(ValueError("x"))
    assert isinstance(classify(sqlite3.OperationalError("locked")), TransientIoError)
    assert isinstance(classify(sqlite3.IntegrityError("unique")), PermanentIoError)
    with pytest.raises(ValidationError):
        IoRetry(max_attempts=0)


def test_external_resources_read_write_and_limits(dbs: tuple[str, str]) -> None:
    resources = ExternalResources(
        sources={"src": lambda: sqlite_source(dbs[0])},
        sinks={"dst": lambda: sqlite_sink(dbs[1])},
        limits=Limits(max_rows=10, batch_rows=4),
    )
    table = resources.read_external("src")
    assert isinstance(table, Table)
    assert table.columns == ("id", "label")
    assert len(table) == 10
    resources.write_external("dst", [{"id": 1, "label": "x"}, {"id": 2, "label": "y"}])
    assert dst_rows(dbs[1]) == [(1, "x"), (2, "y")]
    with pytest.raises(ValidationError):
        resources.read_external("missing")
    small = ExternalResources(sources={"src": lambda: sqlite_source(dbs[0])}, limits=Limits(max_rows=9))
    with pytest.raises(LimitExceededError):
        small.read_external("src")
    with pytest.raises(LimitExceededError):
        ExternalResources(sinks={"dst": lambda: sqlite_sink(dbs[1])}, limits=Limits(max_rows=1)).write_external(
            "dst", Table(("id", "label"), ((5, "a"), (6, "b")))
        )


def test_identifiers_are_validated() -> None:
    from bricksmith.io.sql import quote_identifier  # noqa: PLC0415

    assert quote_identifier("public.orders") == '"public"."orders"'
    for bad in ("orders; DROP TABLE x", 'a"b', "a.b.c", ""):
        with pytest.raises(ValidationError):
            quote_identifier(bad)
