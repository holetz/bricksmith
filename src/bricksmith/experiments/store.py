"""The platform seam of experiments: a small store protocol, an in-memory fake and the guarded table I/O."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

from bricksmith.experiments.errors import ExperimentError, IsolationError
from bricksmith.quality import ComparisonReport, Tolerance, compare_rows, plan_comparison

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.experiments.revision import CompareSpec
    from bricksmith.runner.io import TableIO


class ExperimentStore(Protocol):
    """Versioned table operations experiments need; the Spark/Delta implementation is ``SparkExperimentStore``."""

    def exists(self, physical: str) -> bool:
        """Whether the table exists."""
        ...

    def version(self, physical: str) -> int | None:
        """Return the latest table version, or None when it has none (missing table, view)."""
        ...

    def version_at(self, physical: str, timestamp: str) -> int | None:
        """Return the newest version committed at or before the ISO ``timestamp``, or None."""
        ...

    def read_version(self, physical: str, version: int) -> object:
        """Return the table as of ``version`` (a read; never writes)."""
        ...

    def schema_exists(self, catalog: str, schema: str) -> bool:
        """Whether the schema exists."""
        ...

    def create_schema(self, catalog: str, schema: str) -> None:
        """Create the schema if it does not exist."""
        ...

    def drop_schema(self, catalog: str, schema: str) -> None:
        """Drop an empty schema; fail (never cascade) when it still holds objects."""
        ...

    def copy(self, source: str, target: str, *, version: int | None = None) -> None:
        """Create or replace ``target`` with the content of ``source`` (as of ``version`` when given)."""
        ...

    def drop(self, physical: str) -> None:
        """Drop the table if it exists."""
        ...

    def compare(self, left: str, right: str, spec: CompareSpec) -> ComparisonReport:
        """Compare two tables by ``spec`` and return the quality report."""
        ...


_TYPES = {bool: "boolean", int: "bigint", float: "double", str: "string"}


class InMemoryStore:
    """Thread-safe fake: tables keep every version; also usable as the run's :class:`TableIO` (rows are dicts)."""

    def __init__(self, tables: Mapping[str, list[dict[str, object]]] | None = None) -> None:
        """Start with ``tables`` as version 0 each."""
        self.versions: dict[str, list[list[dict[str, object]]]] = {}
        self.stamps: dict[str, list[str]] = {}
        self.schemas: set[tuple[str, str]] = set()
        self.reads: list[str] = []
        self.writes: list[str] = []
        self._lock = threading.RLock()
        for name, rows in (tables or {}).items():
            self.put(name, rows)

    def put(self, physical: str, rows: list[dict[str, object]], timestamp: str = "1970-01-01T00:00:00") -> int:
        """Commit ``rows`` as a new version of ``physical`` and return its number."""
        with self._lock:
            self.versions.setdefault(physical, []).append([dict(r) for r in rows])
            self.stamps.setdefault(physical, []).append(timestamp)
            catalog, schema, _ = physical.split(".")
            self.schemas.add((catalog, schema))
            return len(self.versions[physical]) - 1

    def rows(self, physical: str, version: int | None = None) -> list[dict[str, object]]:
        """Return a copy of the rows at ``version`` (latest by default)."""
        with self._lock:
            history = self.versions.get(physical)
            if not history:
                msg = f"table {physical!r} does not exist"
                raise ExperimentError(msg)
            return [dict(r) for r in history[-1 if version is None else version]]

    def tables_in(self, catalog: str, schema: str) -> list[str]:
        """Return the sorted tables of a schema."""
        with self._lock:
            return sorted(n for n in self.versions if n.startswith(f"{catalog}.{schema}."))

    # -- ExperimentStore --------------------------------------------------------------------------
    def exists(self, physical: str) -> bool:
        """Whether the table exists."""
        return physical in self.versions

    def version(self, physical: str) -> int | None:
        """Return the latest version."""
        return len(self.versions[physical]) - 1 if physical in self.versions else None

    def version_at(self, physical: str, timestamp: str) -> int | None:
        """Return the newest version stamped at or before ``timestamp``."""
        found = [i for i, s in enumerate(self.stamps.get(physical, [])) if s <= timestamp]
        return found[-1] if found else None

    def read_version(self, physical: str, version: int) -> object:
        """Return the rows as of ``version``."""
        with self._lock:
            self.reads.append(f"{physical}@{version}")
        return self.rows(physical, version)

    def schema_exists(self, catalog: str, schema: str) -> bool:
        """Whether the schema exists."""
        return (catalog, schema) in self.schemas

    def create_schema(self, catalog: str, schema: str) -> None:
        """Create the schema."""
        self.schemas.add((catalog, schema))

    def drop_schema(self, catalog: str, schema: str) -> None:
        """Drop an empty schema."""
        if self.tables_in(catalog, schema):
            msg = f"schema {catalog}.{schema} is not empty"
            raise ExperimentError(msg)
        self.schemas.discard((catalog, schema))

    def copy(self, source: str, target: str, *, version: int | None = None) -> None:
        """Commit a new version of ``target`` holding ``source``'s rows."""
        self.put(target, self.rows(source, version))
        self.writes.append(target)

    def drop(self, physical: str) -> None:
        """Drop the table."""
        with self._lock:
            self.versions.pop(physical, None)
            self.stamps.pop(physical, None)

    def compare(self, left: str, right: str, spec: CompareSpec) -> ComparisonReport:
        """Compare the latest rows of two tables."""
        lrows, rrows = self.rows(left), self.rows(right)
        plan = plan_comparison(
            _schema(lrows),
            _schema(rrows),
            spec.keys,
            exclude=spec.exclude,
            default_tolerance=Tolerance(spec.absolute, spec.relative),
            left=left,
            right=right,
        )
        return compare_rows(lrows, rrows, plan)

    # -- TableIO ------------------------------------------------------------------------------------
    def read(self, physical: str) -> object:
        """Return the latest rows."""
        with self._lock:
            self.reads.append(physical)
        return self.rows(physical)

    def write(
        self,
        physical: str,
        data: object,
        *,
        operation: str,
        options: Mapping[str, object] | None = None,  # noqa: ARG002 - interface
    ) -> None:
        """Append (``append``) or replace the rows with a new version."""
        new = [dict(r) for r in data]  # type: ignore[attr-defined]
        with self._lock:
            old = self.rows(physical) if operation == "append" and physical in self.versions else []
            self.put(physical, [*old, *new])
            self.writes.append(physical)


def _schema(rows: list[dict[str, object]]) -> dict[str, str]:
    schema: dict[str, str] = {}
    for row in rows:
        for column, value in row.items():
            if value is not None:
                schema.setdefault(column, _TYPES.get(type(value), "string"))
    for row in rows[:1]:
        for column in row:
            schema.setdefault(column, "string")
    return schema


class ExperimentIO:
    """Table I/O for a revision run: frozen inputs read at their version, writes allowed only in the destination."""

    def __init__(
        self,
        base: TableIO,
        store: ExperimentStore,
        *,
        pins: Mapping[str, int],
        allowed: frozenset[str],
        baseline: frozenset[str],
    ) -> None:
        """Keep the live backend, the version reader, the pinned versions and the write allow-list."""
        self.base = base
        self.store = store
        self.pins = dict(pins)
        self.allowed = allowed
        self.baseline = baseline

    def read(self, physical: str) -> object:
        """Read a frozen input at its pinned version, anything else live."""
        if physical in self.pins:
            return self.store.read_version(physical, self.pins[physical])
        return self.base.read(physical)

    def write(
        self, physical: str, data: object, *, operation: str, options: Mapping[str, object] | None = None
    ) -> None:
        """Write only to an isolated target; a baseline or unknown object is refused before any effect."""
        if physical not in self.allowed or physical in self.baseline:
            msg = f"refusing to write {physical!r}: it is not an isolated target of this revision"
            raise IsolationError(msg)
        self.base.write(physical, data, operation=operation, options=options)
