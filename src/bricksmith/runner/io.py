"""Table I/O seen by running tasks: a small interface, an in-memory fake and an unconfigured default."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

from bricksmith.errors import ConfigError, ValidationError

if TYPE_CHECKING:
    from bricksmith.context import ExecutionContext


class TableIO(Protocol):
    """Reads and writes physical tables; the Spark/Delta implementation is provided by a later slice."""

    def read(self, physical: str) -> object:
        """Return the content of the physical object."""
        ...

    def write(self, physical: str, data: object, *, operation: str) -> None:
        """Apply ``operation`` (for example ``overwrite`` or ``merge``) with ``data`` to the object."""
        ...


class InMemoryTableIO:
    """Thread-safe fake backed by a dictionary; ``writes`` records every call in order."""

    def __init__(self, tables: dict[str, object] | None = None) -> None:
        """Start with ``tables`` keyed by physical name."""
        self.tables: dict[str, object] = dict(tables or {})
        self.writes: list[tuple[str, str]] = []
        self.reads: list[str] = []
        self._lock = threading.Lock()

    def read(self, physical: str) -> object:
        """Return the stored value or fail for an unknown table."""
        with self._lock:
            self.reads.append(physical)
            if physical not in self.tables:
                msg = f"table {physical!r} does not exist"
                raise ValidationError(msg)
            return self.tables[physical]

    def write(self, physical: str, data: object, *, operation: str) -> None:
        """Store ``data`` and record the write."""
        with self._lock:
            self.tables[physical] = data
            self.writes.append((physical, operation))


class UnconfiguredTableIO:
    """Default backend: fails on first use, so tasks that touch no table still run."""

    def read(self, physical: str) -> object:
        """Fail: no table backend is configured."""
        raise self._error(physical)

    def write(self, physical: str, data: object, *, operation: str) -> None:  # noqa: ARG002 - interface
        """Fail: no table backend is configured."""
        raise self._error(physical)

    @staticmethod
    def _error(physical: str) -> ConfigError:
        return ConfigError(f"no table I/O backend is configured; cannot reach {physical!r}")


def default_table_io(context: ExecutionContext) -> TableIO:  # noqa: ARG001 - the real backend uses the context
    """Return the table I/O for ``context``; the Spark/Delta backend replaces this body."""
    return UnconfiguredTableIO()
