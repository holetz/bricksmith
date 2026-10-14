"""Adapter contracts: tabular data, limits, source and sink protocols, error classification and retry."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, Self

from bricksmith.errors import BricksmithError, ValidationError
from bricksmith.io.errors import IoError, PermanentIoError, TransientIoError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

type Scalar = str | int | float | bool | None
type Row = tuple[object, ...]


@dataclass(frozen=True)
class Table:
    """Small tabular data moved by adapters: column names and rows."""

    columns: tuple[str, ...]
    rows: tuple[Row, ...] = ()

    def __len__(self) -> int:
        """Return the row count."""
        return len(self.rows)

    def records(self) -> list[dict[str, object]]:
        """Return the rows as dictionaries."""
        return [dict(zip(self.columns, row, strict=True)) for row in self.rows]

    @classmethod
    def coerce(cls, data: object) -> Table:
        """Accept a ``Table`` or a non-empty sequence of mappings sharing the same keys."""
        if isinstance(data, Table):
            return data
        if isinstance(data, list | tuple) and data and all(isinstance(item, dict) for item in data):
            columns = tuple(data[0])
            if any(tuple(item) != columns for item in data):
                msg = "all records must have the same keys in the same order"
                raise ValidationError(msg)
            return cls(columns, tuple(tuple(item.values()) for item in data))
        msg = "external data must be a bricksmith.io.Table or a non-empty list of dicts"
        raise ValidationError(msg)


def estimate_bytes(rows: Sequence[Row]) -> int:
    """Return a deterministic size estimate: the UTF-8 length of each value's text plus one per value."""
    return sum(len(str(value).encode()) + 1 for row in rows for value in row)


@dataclass(frozen=True)
class Limits:
    """Bounds for one read or transfer; ``None`` means unbounded (only for explicit opt-in)."""

    max_rows: int | None = 100_000
    max_bytes: int | None = 64 * 1024 * 1024
    batch_rows: int = 1_000

    def __post_init__(self) -> None:
        """Reject non-positive bounds."""
        for name in ("max_rows", "max_bytes"):
            value = getattr(self, name)
            if value is not None and value < 1:
                msg = f"{name} must be positive or None"
                raise ValidationError(msg)
        if self.batch_rows < 1:
            msg = "batch_rows must be at least 1"
            raise ValidationError(msg)


@dataclass(frozen=True)
class Batch:
    """A bounded slice of rows; ``start`` and ``end`` are the exclusive-before and inclusive-last bookmarks."""

    columns: tuple[str, ...]
    rows: tuple[Row, ...]
    start: object = None
    end: object = None


class Source(Protocol):
    """Reads ordered batches. ``bookmark_column`` makes it resumable: rows come in bookmark order."""

    name: str

    def batches(self, *, after: object, max_rows: int | None, batch_rows: int) -> Iterator[Batch]:
        """Yield batches of rows whose bookmark is greater than ``after`` (all rows when ``None``)."""
        ...

    def describe(self) -> dict[str, object]:
        """Return a secret-free description for reports."""
        ...

    def close(self) -> None:
        """Release connections; safe to call twice."""
        ...


class Sink(Protocol):
    """Receives batches. ``committed`` and ``write`` make delivery idempotent per token."""

    name: str

    def committed(self, token: str) -> object:
        """Return the last bookmark delivered under ``token``, or ``None`` when it was never committed."""
        ...

    def write(self, batch: Batch, token: str) -> None:
        """Deliver ``batch`` and record ``token`` atomically; a failure leaves neither."""
        ...

    def describe(self) -> dict[str, object]:
        """Return a secret-free description for reports."""
        ...

    def close(self) -> None:
        """Release connections; safe to call twice."""
        ...


type Classifier = Callable[[BaseException], bool]
_TRANSIENT_NAMES = frozenset({"OperationalError", "InterfaceError", "TimeoutError", "ConnectionError"})
_PERMANENT_NAMES = frozenset(
    {"IntegrityError", "ProgrammingError", "DataError", "NotSupportedError", "InternalError", "SyntaxError"}
)


def is_transient(error: BaseException) -> bool:
    """Classify ``error``: connection, timeout and operational failures are transient; others are permanent.

    Classification looks at the exception class names along the MRO, so any DB-API driver works without
    importing it. Bricksmith errors keep their own ``retryable`` flag.
    """
    if isinstance(error, IoError):
        return error.retryable
    if isinstance(error, BricksmithError):
        return False
    names = {cls.__name__ for cls in type(error).__mro__}
    if names & _PERMANENT_NAMES:
        return False
    return bool(names & _TRANSIENT_NAMES) or isinstance(error, OSError)


def classify(error: BaseException, scrub: Callable[[str], str] = str) -> IoError:
    """Wrap ``error`` as a transient or permanent ``IoError`` with a scrubbed message and no chained cause."""
    if isinstance(error, IoError):
        return error
    message = scrub(f"{type(error).__name__}: {error}")
    return TransientIoError(message) if is_transient(error) else PermanentIoError(message)


@dataclass(frozen=True)
class IoRetry:
    """Attempts for transient failures; permanent ones are never retried."""

    max_attempts: int = 3
    delay: float = 0.5
    backoff: float = 2.0
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        """Reject a policy that would never try."""
        if self.max_attempts < 1 or self.delay < 0 or self.backoff < 1:
            msg = "retry needs max_attempts >= 1, delay >= 0 and backoff >= 1"
            raise ValidationError(msg)

    def pause(self, attempt: int) -> None:
        """Sleep before retry number ``attempt`` (1-based)."""
        self.sleep(self.delay * self.backoff ** (attempt - 1))


class Managed:
    """Mixin giving adapters ``with`` support."""

    def close(self) -> None:
        """Release resources (overridden)."""

    def __enter__(self) -> Self:
        """Return the adapter."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the adapter."""
        self.close()
