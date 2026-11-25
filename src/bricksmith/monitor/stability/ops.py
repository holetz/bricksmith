"""Workspace operations on stability: the one layer behind the CLI verbs and the console.

Every operation is a function of :class:`Operations`, which holds two injected clients: a SQL runner
(``run(statement, params) -> rows``, see :mod:`bricksmith.monitor.stability.sql`) and a jobs client. Reads select the
columns of the dashboard view contract (``VIEW_COLUMNS``) or of a stability table; writes append the same rows
``mark_row`` and ``action_row`` validate for the offline verbs. All values travel as named parameters.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from bricksmith.monitor.actions import RESOLUTIONS, action_row
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.marks import COLUMNS as MARK_COLUMNS
from bricksmith.monitor.stability.marks import UNMARKED, effective_marks, mark_row
from bricksmith.monitor.stability.sql import write_row
from bricksmith.monitor.stability.tables import ACTIONS, MARKS, RUNS, SCHEMAS, VIEW_COLUMNS, table_name

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_PERIODS = re.compile(r"^[\w.\- :]+(,[\w.\- :]+)*$")
_SERIES_FILTERS = ("column", "metric", "segment")
_LIMIT = 5000


class Sql(Protocol):
    """What the operations need of a SQL runner."""

    def run(self, statement: str, params: Mapping[str, object] | None = None) -> list[dict[str, Any]]:
        """Execute ``statement`` with named ``params`` and return its rows."""
        ...


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _select(table: str, columns: tuple[str, ...]) -> str:
    return f"SELECT {', '.join(f'`{c}`' for c in columns)} FROM {table}"  # noqa: S608 - validated names


class Operations:
    """Read and decide on the stability tables of one ``scope`` (``catalog.schema``) through a warehouse."""

    def __init__(
        self,
        sql: Sql,
        scope: str,
        *,
        prefix: str = "stability_",
        jobs: Any = None,  # noqa: ANN401 - a JobsAPI or a fake
        now: Callable[[], str] = _now,
    ) -> None:
        """Bind the SQL runner, the tables' scope and prefix, an optional jobs client and a clock."""
        table_name(scope, prefix, "marks")  # validates scope and prefix once
        self.sql, self.scope, self.prefix, self.jobs, self._now = sql, scope, prefix, jobs, now

    def _t(self, name: str) -> str:
        return table_name(self.scope, self.prefix, name)

    def _read(self, table: str, columns: tuple[str, ...], where: dict[str, str], tail: str = "") -> list[dict]:
        clauses = [f"`{c}` = :{c}" for c in where]
        text = _select(table, columns) + (" WHERE " + " AND ".join(clauses) if clauses else "") + tail
        return self.sql.run(text, where)

    def status(self) -> list[dict[str, Any]]:
        """Return one row per dataset with its status, latest period, last run and open alerts (worst first)."""
        return self._read(self._t("datasets"), VIEW_COLUMNS["datasets"], {}, " ORDER BY `status_rank` DESC, `dataset`")

    def series(self, dataset: str, **filters: str) -> list[dict[str, Any]]:
        """Return the points of a dataset's series with value, limits, trend band, light, mark and action."""
        unknown = sorted(set(filters) - set(_SERIES_FILTERS))
        if unknown:
            msg = f"stability series: unknown filter {', '.join(unknown)}; known: {', '.join(_SERIES_FILTERS)}"
            raise MonitorError(msg)
        where = {"dataset": _need(dataset, "dataset"), **{k: v for k, v in filters.items() if v is not None}}
        return self._read(
            self._t("series"), VIEW_COLUMNS["series"], where, f" ORDER BY `period_date`, `period` LIMIT {_LIMIT}"
        )

    def alerts(self, dataset: str | None = None) -> list[dict[str, Any]]:
        """Return the open alerts with their latest action (the queue)."""
        where = {"dataset": dataset} if dataset else {}
        return self._read(
            self._t("open_alerts"), VIEW_COLUMNS["open_alerts"], where, " ORDER BY `severity_rank` DESC, `alert_id`"
        )

    def marks(self, dataset: str | None = None, as_of: str | None = None) -> list[dict[str, Any]]:
        """Return the marks in force on ``as_of`` (default today, UTC): latest per selector, unmarked ones dropped."""
        where = {"dataset": dataset} if dataset else {}
        rows = self._read(self._t(MARKS), MARK_COLUMNS, where, f" ORDER BY `created_at` LIMIT {_LIMIT}")
        return effective_marks(rows, as_of or self._now()[:10], dataset)

    def actions(self, dataset: str | None = None) -> list[dict[str, Any]]:
        """Return the action history, newest first."""
        where = {"dataset": dataset} if dataset else {}
        return self._read(
            self._t(ACTIONS), tuple(SCHEMAS[ACTIONS]), where, f" ORDER BY `created_at` DESC LIMIT {_LIMIT}"
        )

    def runs(self, dataset: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Return the latest runs, newest first."""
        where = {"dataset": dataset} if dataset else {}
        return self._read(
            self._t(RUNS), tuple(SCHEMAS[RUNS]), where, f" ORDER BY `started_at` DESC LIMIT {max(1, int(limit))}"
        )

    def mark(self, *, unmark: bool = False, mark: str = "", **fields: str) -> dict[str, Any]:
        """Append a mark (or, with ``unmark``, the row that cancels the selector) and return the row written."""
        row = mark_row(mark=UNMARKED if unmark else mark, created_at=self._now(), **fields)  # pyright: ignore[reportArgumentType]
        if unmark:
            row["valid_until"] = ""
        self._write(MARKS, row, MARK_COLUMNS)
        return row

    def act(self, *, dataset: str, alert_id: str, action: str, reason: str, author: str, **extra: str) -> dict:
        """Append a human action on an alert (analysis, closed with a resolution, risk_accepted) and return the row."""
        row = action_row(
            entity="dataset", name=dataset, alert_id=alert_id, action=action, reason=reason, author=author,
            created_at=self._now(), resolutions=RESOLUTIONS, **extra,
        )  # fmt: skip
        self._write(ACTIONS, row, tuple(SCHEMAS[ACTIONS]))
        return row

    def reprocess(self, job_id: str | int, periods: str) -> dict[str, Any]:
        """Start the job's run with the periods to profile again (the runner's ``--reprocess``); return its run id."""
        if self.jobs is None:
            msg = "stability reprocess needs a jobs client"
            raise MonitorError(msg)
        if not _PERIODS.match(periods.strip()):
            msg = f"stability reprocess: periods must be a list (a,b) or ranges (first..last), got {periods!r}"
            raise MonitorError(msg)
        try:
            job = int(job_id)
        except (TypeError, ValueError):
            msg = f"stability reprocess: job id must be a number, got {job_id!r}"
            raise MonitorError(msg) from None
        started = self.jobs.run_now(job_id=job, job_parameters={"reprocess": periods.strip()})
        return {"job_id": job, "periods": periods.strip(), "run_id": getattr(started, "run_id", None)}

    def _write(self, kind: str, row: Mapping[str, Any], columns: tuple[str, ...]) -> None:
        statement, params = write_row(self._t(kind), row, columns)
        self.sql.run(statement, params)


def _need(value: str, what: str) -> str:
    if not value or not value.strip():
        msg = f"stability: {what} is required"
        raise MonitorError(msg)
    return value


__all__ = ["Operations", "Sql"]
