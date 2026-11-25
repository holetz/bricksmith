"""Run SQL on a declared warehouse through the Statement Execution API, with named parameters only.

The operations layer never builds statement text from values: a value travels as a named parameter (``:name``) and only
validated table names are part of the text. :class:`SqlRunner` wraps a workspace client (built from one explicit
profile through :mod:`bricksmith.clients`) and returns rows as dictionaries.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from bricksmith.clients import import_extra
from bricksmith.monitor.errors import MonitorError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

#: Terminal states of a statement other than success.
_FAILED = ("FAILED", "CANCELED", "CLOSED")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
type Parameters = Mapping[str, object]


def _state(response: Any) -> str:  # noqa: ANN401 - an SDK response
    state = response.status.state
    return str(getattr(state, "value", state))


def _sdk_parameter(name: str, value: str | None) -> Any:  # noqa: ANN401 - an SDK object
    sql = import_extra("databricks.sdk.service.sql", "sdk")
    return sql.StatementParameterListItem(name=name, value=value, type="STRING")


class SqlRunner:
    """Execute statements on one warehouse; ``parameter`` builds one named parameter (injectable for tests)."""

    def __init__(
        self,
        workspace: Any,  # noqa: ANN401 - a WorkspaceClient or a fake
        warehouse_id: str,
        *,
        parameter: Callable[[str, str | None], Any] = _sdk_parameter,
        poll_seconds: float = 1.0,
        max_polls: int = 600,
    ) -> None:
        """Bind the workspace client and the warehouse; a blank warehouse id is refused."""
        if not warehouse_id.strip():
            msg = "stability operations need a warehouse id (--warehouse)"
            raise MonitorError(msg)
        self.workspace = workspace
        self.warehouse_id = warehouse_id
        self._parameter = parameter
        self._poll = poll_seconds
        self._max_polls = max_polls

    def run(self, statement: str, params: Parameters | None = None) -> list[dict[str, Any]]:
        """Execute ``statement`` with named ``params``; return its rows (empty for a write)."""
        items = [self._parameter(_checked(k), None if v is None else str(v)) for k, v in (params or {}).items()]
        api = self.workspace.statement_execution
        response = api.execute_statement(
            statement=statement, warehouse_id=self.warehouse_id, parameters=items, wait_timeout="30s"
        )
        polls = 0
        while _state(response) in ("PENDING", "RUNNING"):
            polls += 1
            if polls > self._max_polls:
                msg = f"stability sql: statement {response.statement_id} did not finish"
                raise MonitorError(msg)
            time.sleep(self._poll)
            response = api.get_statement(response.statement_id)
        state = _state(response)
        if state in _FAILED or state != "SUCCEEDED":
            error = getattr(response.status, "error", None)
            msg = f"stability sql: statement {state.lower()}: {getattr(error, 'message', '') or 'no message'}"
            raise MonitorError(msg)
        return _rows(response)


def _checked(name: str) -> str:
    if not _NAME.match(name):
        msg = f"stability sql: parameter name {name!r} is not a plain name"
        raise MonitorError(msg)
    return name


def _rows(response: Any) -> list[dict[str, Any]]:  # noqa: ANN401 - an SDK response
    result = getattr(response, "result", None)
    data = getattr(result, "data_array", None) or []
    manifest = getattr(response, "manifest", None)
    columns = [c.name for c in manifest.schema.columns] if manifest is not None and manifest.schema else []
    return [dict(zip(columns, row, strict=False)) for row in data]


def write_row(table: str, row: Mapping[str, Any], columns: Sequence[str]) -> tuple[str, dict[str, object]]:
    """Return the ``INSERT`` text and the named parameters that append ``row`` to ``table``.

    ``table`` must already be a validated name (``tables.table_name``); every value, ``valid_until`` included (cast to
    ``DATE``), is a parameter, so nothing in ``row`` can alter the statement.
    """
    marks = ", ".join(f"CAST(:{c} AS DATE)" if c == "valid_until" else f":{c}" for c in columns)
    statement = f"INSERT INTO {table} ({', '.join(f'`{c}`' for c in columns)}) VALUES ({marks})"  # noqa: S608
    return statement, {c: (row.get(c) or None) if c == "valid_until" else row.get(c) for c in columns}


__all__ = ["Parameters", "SqlRunner", "write_row"]
