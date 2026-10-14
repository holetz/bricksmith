"""Named external sources and sinks for running tasks, and their lineage in the graph."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bricksmith.errors import ValidationError
from bricksmith.io.contracts import Batch, IoRetry, Limits, Table
from bricksmith.io.errors import LimitExceededError
from bricksmith.io.transfer import MemoryBookmarkStore, transfer

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from bricksmith.graph import TaskGraph
    from bricksmith.io.contracts import Sink, Source
    from bricksmith.io.transfer import BookmarkStore, TransferReport


class ExternalResources:
    """Implements ``bricksmith.runner.ExternalProvider`` with adapter factories.

    Adapters are built per use, so no connection (or secret) outlives one read, write or transfer.
    """

    def __init__(
        self,
        *,
        sources: Mapping[str, Callable[[], Source]] | None = None,
        sinks: Mapping[str, Callable[[], Sink]] | None = None,
        limits: Limits | None = None,
        store: BookmarkStore | None = None,
        retry: IoRetry | None = None,
    ) -> None:
        """Register source and sink factories by resource name."""
        self._sources = dict(sources or {})
        self._sinks = dict(sinks or {})
        self.limits = limits or Limits()
        self.store: BookmarkStore = store or MemoryBookmarkStore()
        self.retry = retry or IoRetry()

    def _source(self, name: str) -> Source:
        if name not in self._sources:
            msg = f"no external source is registered as {name!r}"
            raise ValidationError(msg)
        return self._sources[name]()

    def _sink(self, name: str) -> Sink:
        if name not in self._sinks:
            msg = f"no external sink is registered as {name!r}"
            raise ValidationError(msg)
        return self._sinks[name]()

    def read_external(self, name: str) -> Table:
        """Read the whole resource within the limits; exceeding them fails instead of truncating."""
        source = self._source(name)
        rows: list[tuple[object, ...]] = []
        columns: tuple[str, ...] = ()
        try:
            cap = None if self.limits.max_rows is None else self.limits.max_rows + 1
            for batch in source.batches(after=None, max_rows=cap, batch_rows=self.limits.batch_rows):
                columns = batch.columns
                rows.extend(batch.rows)
                if self.limits.max_rows is not None and len(rows) > self.limits.max_rows:
                    msg = f"external source {name!r} has more than max_rows={self.limits.max_rows} rows"
                    raise LimitExceededError(msg)
        finally:
            source.close()
        return Table(columns, tuple(rows))

    def write_external(self, name: str, data: object) -> None:
        """Deliver ``data`` in batches; each batch is atomic, the whole write is not (see the guide)."""
        table = Table.coerce(data)
        if self.limits.max_rows is not None and len(table) > self.limits.max_rows:
            msg = f"{len(table)} rows exceed max_rows={self.limits.max_rows}"
            raise LimitExceededError(msg)
        sink = self._sink(name)
        run = uuid.uuid4().hex
        try:
            for index in range(0, len(table), self.limits.batch_rows):
                chunk = table.rows[index : index + self.limits.batch_rows]
                sink.write(Batch(table.columns, chunk), f"write:{run}:{index}")
        finally:
            sink.close()

    def transfer(self, source: str, sink: str, *, key: str) -> TransferReport:
        """Run an incremental transfer between two registered resources."""
        return transfer(
            self._source(source), self._sink(sink), store=self.store, key=key, limits=self.limits, retry=self.retry
        )


@dataclass(frozen=True)
class IoLink:
    """One external dependency of a task: ``direction`` is ``in`` (source) or ``out`` (sink)."""

    task: str
    direction: str
    resource: str

    def to_dict(self) -> dict[str, str]:
        """Return the stable serializable form."""
        return {"direction": self.direction, "resource": self.resource, "task": self.task}


def io_lineage(graph: TaskGraph) -> tuple[IoLink, ...]:
    """List external inputs (graph edges of kind ``external``) and sinks (``metadata.external_sinks``)."""
    links = {IoLink(e.consumer, "in", e.target or e.reference) for e in graph.edges if e.kind == "external"}
    for node in graph.tasks:
        sinks = node.metadata.get("external_sinks")
        if isinstance(sinks, list):
            links |= {IoLink(node.id, "out", item) for item in sinks if isinstance(item, str)}
    return tuple(sorted(links, key=lambda link: (link.task, link.direction, link.resource)))
