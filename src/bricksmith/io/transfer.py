"""Incremental transfers: bookmarks, partial-transfer recovery and replay.

Delivery semantics (see ``docs/guide/external-io.md``): the source is read in bookmark order, one
batch at a time; each batch is committed to the sink together with a ledger token that names the
batch by the bookmark it starts after. A progress record ("pending") is saved after every
committed batch; the committed bookmark advances only when the whole transfer succeeded. A replay
resumes from the progress record, and a batch whose token is already in the sink's ledger is
skipped (or trimmed), so rows reach the sink exactly once even when a crash lands between the sink
commit and the progress save. A transfer is not one distributed transaction: it is atomic per
batch only.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from bricksmith.errors import ValidationError
from bricksmith.io.contracts import Batch, IoRetry, Limits, estimate_bytes
from bricksmith.io.errors import IoError, LimitExceededError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.io.contracts import Sink, Source

LOG = logging.getLogger("bricksmith.io")
_KEY = re.compile(r"[A-Za-z0-9_.\-]{1,120}")


@dataclass(frozen=True)
class BookmarkState:
    """The committed bookmark and, while a transfer is unfinished, its progress."""

    bookmark: object = None
    pending_base: object = None
    pending_position: object = None
    pending: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return the JSON form."""
        data: dict[str, object] = {"bookmark": self.bookmark}
        if self.pending:
            data["pending"] = {"base": self.pending_base, "position": self.pending_position}
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, object]) -> BookmarkState:
        """Rebuild from :meth:`to_dict` output."""
        pending = data.get("pending")
        if isinstance(pending, dict):
            return cls(data.get("bookmark"), pending.get("base"), pending.get("position"), pending=True)
        return cls(data.get("bookmark"))


class BookmarkStore(Protocol):
    """Persists one :class:`BookmarkState` per transfer key; injectable."""

    def load(self, key: str) -> BookmarkState:
        """Return the stored state (empty when the key is new)."""
        ...

    def save(self, key: str, state: BookmarkState) -> None:
        """Persist ``state`` durably."""
        ...


def check_key(key: str) -> str:
    """Return ``key`` when it is a safe transfer key."""
    if not _KEY.fullmatch(key):
        msg = "a transfer key uses 1-120 letters, digits, dot, dash or underscore"
        raise ValidationError(msg)
    return key


class MemoryBookmarkStore:
    """In-process store for tests and single-run jobs."""

    def __init__(self) -> None:
        """Start empty."""
        self.states: dict[str, BookmarkState] = {}
        self.saves: list[tuple[str, BookmarkState]] = []

    def load(self, key: str) -> BookmarkState:
        """Return the state of ``key``."""
        return self.states.get(key, BookmarkState())

    def save(self, key: str, state: BookmarkState) -> None:
        """Remember ``state``."""
        self.states[key] = state
        self.saves.append((key, state))


class FileBookmarkStore:
    """One JSON file per key in a directory (a local folder or a ``/Volumes`` path); replaced atomically."""

    def __init__(self, directory: Path | str) -> None:
        """Use ``directory``, created on first save."""
        self.directory = Path(directory)

    def _path(self, key: str) -> Path:
        return self.directory / f"{check_key(key)}.json"

    def load(self, key: str) -> BookmarkState:
        """Read the state or return an empty one."""
        path = self._path(key)
        if not path.is_file():
            return BookmarkState()
        return BookmarkState.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, key: str, state: BookmarkState) -> None:
        """Write the state through a temporary file and an atomic rename."""
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(state.to_dict(), sort_keys=True), encoding="utf-8")
        temp.replace(path)


@dataclass(frozen=True)
class TransferReport:
    """Outcome of one transfer; contains no connection parameters."""

    key: str
    source: str
    sink: str
    rows: int
    batches: int
    bytes: int
    replayed_rows: int
    bookmark_before: object
    bookmark_after: object
    resumed: bool
    truncated: bool
    attempts: int

    def to_dict(self) -> dict[str, object]:
        """Return the stable serializable form."""
        return {name: getattr(self, name) for name in sorted(self.__dataclass_fields__)}


class TransferFailedError(IoError):
    """A transfer stopped; ``progress`` shows what was committed. Run it again to resume."""

    code = "io_transfer_failed"

    def __init__(self, message: str, progress: Mapping[str, object]) -> None:
        """Keep the scrubbed message and the committed progress."""
        super().__init__(message)
        self.progress = dict(progress)


@dataclass
class _Run:
    position: object
    rows: int = 0
    batches: int = 0
    size: int = 0
    replayed: int = 0
    attempts: int = 0
    truncated: bool = False
    extra: dict[str, object] = field(default_factory=dict)


def _deliver(sink: Sink, key: str, batch: Batch, index: int, run: _Run) -> object:
    """Commit ``batch`` once; trim rows an earlier commit already delivered. Return the last bookmark."""
    start, rows = batch.start, batch.rows
    while rows:
        token = f"{key}:{json.dumps(start)}"
        done = sink.committed(token)
        if done is None:
            sink.write(Batch(batch.columns, rows, start, rows[-1][index]), token)
            run.rows += len(rows)
            return rows[-1][index]
        kept = tuple(row for row in rows if row[index] > done)  # type: ignore[operator]
        run.replayed += len(rows) - len(kept)
        rows, start = kept, done
    return batch.end


def transfer(  # noqa: PLR0913 - one keyword per documented setting; one linear recovery loop
    source: Source,
    sink: Sink,
    *,
    store: BookmarkStore,
    key: str,
    limits: Limits | None = None,
    retry: IoRetry | None = None,
) -> TransferReport:
    """Move rows after the stored bookmark from ``source`` to ``sink`` and advance the bookmark on success.

    ``limits.max_rows`` and ``max_bytes`` bound this run (the next run continues); a first batch larger
    than ``max_bytes`` raises ``LimitExceededError``. Transient failures are retried from the last
    committed batch; when attempts run out, ``TransferFailedError`` carries the committed progress and
    the bookmark is left unchanged.
    """
    check_key(key)
    limits = limits or Limits()
    retry = retry or IoRetry()
    if getattr(source, "bookmark_column", None) is None:
        msg = "a transfer needs a source with a bookmark_column"
        raise ValidationError(msg)
    columns: tuple[str, ...] = source.columns  # type: ignore[attr-defined]
    index = columns.index(source.bookmark_column)  # type: ignore[attr-defined]
    state = store.load(key)
    resumed = state.pending and state.pending_base == state.bookmark
    run = _Run(state.pending_position if resumed else state.bookmark)
    try:
        while True:
            run.attempts += 1
            try:
                left = None if limits.max_rows is None else limits.max_rows - run.rows
                if left == 0:
                    run.truncated = True
                    break
                for batch in source.batches(after=run.position, max_rows=left, batch_rows=limits.batch_rows):
                    size = estimate_bytes(batch.rows)
                    if limits.max_bytes is not None and run.size + size > limits.max_bytes:
                        if run.batches == 0:
                            msg = f"the first batch needs about {size} bytes, over max_bytes={limits.max_bytes}"
                            raise LimitExceededError(msg)
                        run.truncated = True
                        break
                    run.position = _deliver(sink, key, batch, index, run)
                    run.batches += 1
                    run.size += size
                    store.save(key, BookmarkState(state.bookmark, state.bookmark, run.position, pending=True))
                else:
                    run.truncated = left is not None and run.rows >= limits.max_rows  # type: ignore[operator]
                break
            except IoError as error:
                if error.retryable and run.attempts < retry.max_attempts:
                    LOG.warning("transfer %s: transient failure, retrying (%s)", key, error)
                    retry.pause(run.attempts)
                    continue
                progress = {"committed_rows": run.rows, "position": run.position, "key": key}
                stopped = f"transfer {key} stopped: {error}"
                raise TransferFailedError(stopped, progress) from None
        store.save(key, BookmarkState(run.position))
    finally:
        source.close()
        sink.close()
    return TransferReport(
        key, source.name, sink.name, run.rows, run.batches, run.size, run.replayed,
        state.bookmark, run.position, bool(resumed), run.truncated, run.attempts,
    )  # fmt: skip
