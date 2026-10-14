"""Table I/O seen by running tasks: a small interface, an in-memory fake and an unconfigured default."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

from bricksmith.errors import ConfigError, ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.context import ExecutionContext


class TableIO(Protocol):
    """Reads and writes physical tables; the Spark/Delta implementation is provided by a later slice."""

    def read(self, physical: str) -> object:
        """Return the content of the physical object."""
        ...

    def write(
        self, physical: str, data: object, *, operation: str, options: Mapping[str, object] | None = None
    ) -> None:
        """Apply ``operation`` (for example ``overwrite`` or ``merge``) with ``data``.

        ``options`` are the task's write metadata plus ``target``, ``protected`` and ``granted``.
        """
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

    def write(
        self,
        physical: str,
        data: object,
        *,
        operation: str,
        options: Mapping[str, object] | None = None,  # noqa: ARG002 - interface
    ) -> None:
        """Store ``data`` and record the write."""
        with self._lock:
            self.tables[physical] = data
            self.writes.append((physical, operation))


class UnconfiguredTableIO:
    """Default backend: fails on first use, so tasks that touch no table still run."""

    def read(self, physical: str) -> object:
        """Fail: no table backend is configured."""
        raise self._error(physical)

    def write(
        self,
        physical: str,
        data: object,  # noqa: ARG002 - interface
        *,
        operation: str,  # noqa: ARG002 - interface
        options: Mapping[str, object] | None = None,  # noqa: ARG002 - interface
    ) -> None:
        """Fail: no table backend is configured."""
        raise self._error(physical)

    @staticmethod
    def _error(physical: str) -> ConfigError:
        return ConfigError(f"no table I/O backend is configured; cannot reach {physical!r}")


def default_table_io(context: ExecutionContext) -> TableIO:
    """Return the Spark/Delta table I/O for ``context``; the session is created on first use."""
    from bricksmith.storage.spark import SparkTableIO  # noqa: PLC0415 - heavy integrations load where used

    return SparkTableIO.for_context(context)
