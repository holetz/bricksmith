"""DB-API 2.0 connector with source and sink adapters.

Any driver that follows PEP 249 works; the connector takes the driver's ``connect`` callable, so
the driver is imported by the caller (``sqlite3`` ships with Python, ``psycopg`` is the
``postgres`` extra). Secrets arrive as ``secret:<scope>/<key>`` parameter values and are resolved
only inside :meth:`SqlConnector.connect`.
"""

from __future__ import annotations

import contextlib
import json
import re
from typing import TYPE_CHECKING, Any

from bricksmith.errors import ValidationError
from bricksmith.io.contracts import Batch, Managed, classify
from bricksmith.io.errors import IoError
from bricksmith.io.secrets import Redactor, describe_params, resolve_params

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from bricksmith.io.secrets import SecretResolver

LEDGER = "bricksmith_io_ledger"
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?")
_PLACEHOLDERS = {"qmark": "?", "format": "%s"}


def quote_identifier(name: str) -> str:
    """Return ``name`` (``table`` or ``schema.table``) double-quoted; anything else is rejected."""
    if not _IDENT.fullmatch(name):
        msg = f"{name!r} is not a plain identifier (letters, digits, underscore, optional schema prefix)"
        raise ValidationError(msg)
    return ".".join(f'"{part}"' for part in name.split("."))


class SqlConnector:
    """Opens DB-API connections with secrets resolved at point of use.

    Resolved values are never kept except inside the :class:`Redactor`, which only scrubs text.
    """

    def __init__(
        self,
        driver: Callable[..., Any],
        params: Mapping[str, object],
        *,
        paramstyle: str = "qmark",
        resolver: SecretResolver | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        """Bind a ``connect`` callable, its keyword ``params`` and the driver's ``paramstyle`` (qmark or format)."""
        if paramstyle not in _PLACEHOLDERS:
            msg = f"paramstyle must be one of {sorted(_PLACEHOLDERS)}"
            raise ValidationError(msg)
        self._driver = driver
        self._params = dict(params)
        self.paramstyle = paramstyle
        self.placeholder = _PLACEHOLDERS[paramstyle]
        self._resolver = resolver
        self.redactor = redactor or Redactor()

    def connect(self) -> Any:  # noqa: ANN401 - a DB-API connection has no common static type
        """Resolve secrets and open a connection; failures are classified and scrubbed."""
        try:
            resolved = resolve_params(self._params, self._resolver, self.redactor)
            return self._driver(**resolved)
        except IoError:
            raise
        except Exception as error:  # noqa: BLE001 - every driver error becomes a classified IoError
            raise classify(error, self.redactor.scrub) from None

    def describe(self) -> dict[str, object]:
        """Return the parameters for reports, with references and credential-like names masked."""
        return describe_params(self._params)


class _SqlAdapter(Managed):
    """Shared lazily opened connection with classified, scrubbed failures."""

    def __init__(self, connector: SqlConnector, table: str, name: str | None) -> None:
        self.connector = connector
        self.table = table
        self._quoted = quote_identifier(table)
        self.name = name or table
        self._conn: Any = None

    def _connection(self) -> Any:  # noqa: ANN401 - DB-API connection
        if self._conn is None:
            self._conn = self.connector.connect()
        return self._conn

    def close(self) -> None:
        """Close the connection if open."""
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):  # closing a broken connection is best effort
                conn.close()

    def _call[T](self, action: Callable[[Any], T]) -> T:
        try:
            return action(self._connection())
        except IoError:
            self.close()
            raise
        except Exception as error:  # noqa: BLE001 - classified below
            self._rollback()
            self.close()
            raise classify(error, self.connector.redactor.scrub) from None

    def _rollback(self) -> None:
        try:
            if self._conn is not None:
                self._conn.rollback()
        except Exception:  # noqa: BLE001, S110 - the connection is closed right after
            pass

    def describe(self) -> dict[str, object]:
        """Return a secret-free description."""
        return {"connector": self.connector.describe(), "name": self.name, "table": self.table}


class SqlSource(_SqlAdapter):
    """Reads a table in bookmark order with keyset pagination (stateless pages, safe to restart)."""

    def __init__(
        self,
        connector: SqlConnector,
        table: str,
        *,
        columns: Sequence[str],
        bookmark_column: str | None = None,
        name: str | None = None,
    ) -> None:
        """Select ``columns`` of ``table``; ``bookmark_column`` must be unique and increasing for exact resume."""
        super().__init__(connector, table, name)
        if not columns:
            msg = "a source needs at least one column"
            raise ValidationError(msg)
        self.columns = tuple(columns)
        self.bookmark_column = bookmark_column
        self._select = ", ".join(quote_identifier(c) for c in self.columns)
        self._order = quote_identifier(bookmark_column) if bookmark_column else None
        self._bm_index = self.columns.index(bookmark_column) if bookmark_column in self.columns else None
        if bookmark_column and self._bm_index is None:
            msg = "bookmark_column must be one of the selected columns"
            raise ValidationError(msg)

    def _page(self, after: object, size: int) -> list[tuple[object, ...]]:
        ph = self.connector.placeholder
        sql = f"SELECT {self._select} FROM {self._quoted}"  # noqa: S608 - identifiers validated and quoted
        args: list[object] = []
        if self._order and after is not None:
            sql += f" WHERE {self._order} > {ph}"
            args.append(after)
        if self._order:
            sql += f" ORDER BY {self._order}"
        sql += f" LIMIT {ph}"
        args.append(size)

        def run(conn: Any) -> list[tuple[object, ...]]:  # noqa: ANN401
            cursor = conn.cursor()
            try:
                cursor.execute(sql, args)
                rows = [tuple(row) for row in cursor.fetchall()]
            finally:
                cursor.close()
            conn.commit()
            return rows

        return self._call(run)

    def batches(self, *, after: object, max_rows: int | None, batch_rows: int) -> Iterator[Batch]:
        """Yield pages of at most ``batch_rows`` rows, never more than ``max_rows`` in total."""
        if after is not None and self._order is None:
            msg = "a source without bookmark_column cannot resume after a bookmark"
            raise ValidationError(msg)
        remaining = max_rows
        position = after
        while remaining is None or remaining > 0:
            size = batch_rows if remaining is None else min(batch_rows, remaining)
            rows = self._page(position, size)
            if not rows:
                return
            end = rows[-1][self._bm_index] if self._bm_index is not None else None
            yield Batch(self.columns, tuple(rows), position, end)
            if self._order is None or len(rows) < size:
                return
            position = end
            remaining = None if remaining is None else remaining - len(rows)


class SqlSink(_SqlAdapter):
    """Inserts batches and records a token in a ledger table in the same transaction."""

    def __init__(self, connector: SqlConnector, table: str, *, columns: Sequence[str], name: str | None = None) -> None:
        """Insert into ``table`` (which must exist) the given ``columns``."""
        super().__init__(connector, table, name)
        self.columns = tuple(columns)
        self._insert = (
            f"INSERT INTO {self._quoted} ({', '.join(quote_identifier(c) for c in self.columns)}) "  # noqa: S608
            f"VALUES ({', '.join([connector.placeholder] * len(self.columns))})"
        )
        self._ready = False

    def _ensure_ledger(self, conn: Any) -> None:  # noqa: ANN401
        if self._ready:
            return
        cursor = conn.cursor()
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {LEDGER} (sink VARCHAR(200) NOT NULL, token VARCHAR(400) NOT NULL, "
            "last_bookmark VARCHAR(400), PRIMARY KEY (sink, token))"
        )
        cursor.close()
        conn.commit()
        self._ready = True

    def committed(self, token: str) -> object:
        """Return the bookmark delivered under ``token`` or ``None``."""
        ph = self.connector.placeholder

        def run(conn: Any) -> object:  # noqa: ANN401
            self._ensure_ledger(conn)
            cursor = conn.cursor()
            cursor.execute(f"SELECT last_bookmark FROM {LEDGER} WHERE sink = {ph} AND token = {ph}", [self.name, token])  # noqa: S608
            row = cursor.fetchone()
            cursor.close()
            conn.commit()
            return None if row is None else json.loads(row[0])

        return self._call(run)

    def write(self, batch: Batch, token: str) -> None:
        """Insert ``batch`` and the ledger row in one transaction."""
        if batch.columns != self.columns:
            msg = f"batch columns {batch.columns} do not match sink columns {self.columns}"
            raise ValidationError(msg)
        ph = self.connector.placeholder

        def run(conn: Any) -> None:  # noqa: ANN401
            self._ensure_ledger(conn)
            cursor = conn.cursor()
            cursor.executemany(self._insert, [list(row) for row in batch.rows])
            cursor.execute(
                f"INSERT INTO {LEDGER} (sink, token, last_bookmark) VALUES ({ph}, {ph}, {ph})",  # noqa: S608
                [self.name, token, json.dumps(batch.end)],
            )
            cursor.close()
            conn.commit()

        self._call(run)
