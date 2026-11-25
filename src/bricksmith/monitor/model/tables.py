"""The six model monitoring tables (flat, typed, keyed for merge) and the dashboard views that trust finished runs.

Results, details and alerts carry the run id in their key: a rerun adds rows, and readers pick, per model, model
version and monitoring period, the latest run whose record says it finished (``success`` or ``partial``). The run
record is written last, so rows of a run that died mid-write are never read. Every column a report filters on is a
plain scalar; only the binning ``body`` keeps JSON.

The ``actions`` table is the exception: people append to it (analysis, closure, risk acceptance) and runs only read
it, so it is keyed by the action itself, not by a run. Columns added after a table was first created (limit kinds,
baselines, trend, alert spans and applied actions) reach existing tables through the ``evolution`` write option,
which admits compatible additions only.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.errors import MonitorError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyspark.sql import DataFrame, SparkSession  # pyright: ignore[reportMissingImports]

BINNINGS = "binnings"
RESULTS = "results"
DETAILS = "details"
ALERTS = "alerts"
ACTIONS = "actions"
RUNS = "runs"
KINDS = (BINNINGS, RESULTS, DETAILS, ALERTS, ACTIONS, RUNS)
SUCCESS = "success"
PARTIAL = "partial"
FAILED = "failed"
RUN_STATUSES = (SUCCESS, PARTIAL, FAILED)
FINISHED = (SUCCESS, PARTIAL)
_PERIOD = {
    "monitoring_period": "STRING", "data_period": "STRING", "period_date": "DATE", "segment": "STRING",
    "column": "STRING", "metric": "STRING",
}  # fmt: skip
SCHEMAS: dict[str, dict[str, str]] = {
    BINNINGS: {
        "model": "STRING", "version": "INT", "config_fingerprint": "STRING", "fingerprint": "STRING",
        "source": "STRING", "fit_rows": "BIGINT", "columns": "INT", "shared_from": "STRING", "body": "STRING",
        "created_run_id": "STRING", "created_at": "STRING",
    },
    RESULTS: {
        "model": "STRING", "model_version": "STRING", "run_id": "STRING", **_PERIOD, "family": "STRING",
        "value": "DOUBLE", "light": "STRING", "light_rank": "INT", "reason": "STRING", "green_limit": "DOUBLE",
        "red_limit": "DOUBLE", "direction": "STRING", "rows": "BIGINT", "reference": "STRING",
        "development_value": "DOUBLE", "out_of_time_value": "DOUBLE", "binning_version": "INT",
        "computed_at": "STRING", "limit_kind": "STRING", "limit_reference": "STRING", "compared_value": "DOUBLE",
        "baseline_mean": "DOUBLE", "baseline_sd": "DOUBLE", "baseline_points": "INT", "trend_status": "STRING",
        "trend_expected": "DOUBLE", "trend_deviation": "DOUBLE",
        "lag": "INT", "lag_unit": "STRING", "rule_id": "STRING", "rule_version": "INT",
    },
    DETAILS: {
        "model": "STRING", "model_version": "STRING", "run_id": "STRING", **_PERIOD, "bin": "STRING",
        "bin_order": "INT", "reference_share": "DOUBLE", "current_share": "DOUBLE", "contribution": "DOUBLE",
        "count": "BIGINT", "mean_score": "DOUBLE", "observed_rate": "DOUBLE",
    },
    ALERTS: {
        "model": "STRING", "model_version": "STRING", "run_id": "STRING", "alert_id": "STRING",
        "monitoring_period": "STRING", "data_period": "STRING", "segment": "STRING", "column": "STRING",
        "metric": "STRING", "family": "STRING", "light": "STRING", "severity": "STRING", "severity_rank": "INT",
        "value": "DOUBLE", "message": "STRING", "created_at": "STRING", "kind": "STRING", "state": "STRING",
        "opened_period": "STRING", "escalated_period": "STRING", "closed_period": "STRING", "last_period": "STRING",
        "flagged_periods": "INT", "recurrence": "BOOLEAN", "action": "STRING", "action_reason": "STRING",
        "action_author": "STRING", "action_at": "STRING", "valid_until": "DATE",
    },
    ACTIONS: {
        "action_id": "STRING", "model": "STRING", "alert_id": "STRING", "action": "STRING", "reason": "STRING",
        "author": "STRING", "valid_until": "DATE", "created_at": "STRING",
    },
    RUNS: {
        "run_id": "STRING", "model": "STRING", "model_version": "STRING", "task_id": "STRING",
        "physical": "STRING", "input_version": "STRING", "code_version": "STRING", "binning_version": "INT",
        "binning_fingerprint": "STRING", "references": "STRING", "latest_period": "STRING", "periods": "INT",
        "status": "STRING", "gate": "STRING", "red": "INT", "yellow": "INT", "errors": "INT", "alerts": "INT",
        "message": "STRING", "started_at": "STRING", "finished_at": "STRING", "open_alerts": "INT",
        "critical_alerts": "INT",
    },
}  # fmt: skip
KEYS: dict[str, tuple[str, ...]] = {
    BINNINGS: ("model", "version"),
    RESULTS: ("model", "model_version", "run_id", "monitoring_period", "segment", "column", "metric"),
    DETAILS: ("model", "model_version", "run_id", "monitoring_period", "segment", "column", "metric", "bin"),
    ALERTS: ("model", "run_id", "alert_id"),
    ACTIONS: ("model", "action_id"),
    RUNS: ("run_id", "model"),
}
_SCOPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_PREFIX = re.compile(r"^[A-Za-z0-9_]*$")


def write_options(kind: str) -> dict[str, object]:
    """Return the write metadata of a model monitoring table.

    ``merge`` on its key; an empty write creates it; ``evolution`` adds columns introduced after the table existed.
    """
    return {
        "write": "merge",
        "keys": list(KEYS[kind]),
        "on_empty": "allow_empty",
        "evolution": {},
        "model_monitoring_table": kind,
    }


def key_of(kind: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return the merge key of ``row`` in table ``kind``."""
    return tuple(row.get(c) for c in KEYS[kind])


def merge_rows(kind: str, existing: Sequence[Mapping[str, Any]], new: Sequence[Mapping[str, Any]]) -> list[dict]:
    """Upsert ``new`` into ``existing`` by key (the in-memory equivalent of a Delta merge)."""
    merged = {key_of(kind, r): dict(r) for r in existing}
    for row in new:
        merged[key_of(kind, row)] = {c: row.get(c) for c in SCHEMAS[kind]}
    return list(merged.values())


def _cast(kind_name: str, value: object) -> object:
    from datetime import date  # noqa: PLC0415 - tiny

    if kind_name == "DATE":
        return date.fromisoformat(value) if isinstance(value, str) else value
    if kind_name == "DOUBLE":
        return float(value)  # type: ignore[arg-type]
    if kind_name in ("INT", "BIGINT"):
        return int(value)  # type: ignore[arg-type]
    if kind_name == "BOOLEAN":
        return bool(value)
    return value


def typed_row(kind: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return ``row`` in schema order with Python types Spark accepts (ISO text dates become dates)."""
    return tuple(None if row.get(c) is None else _cast(t, row[c]) for c, t in SCHEMAS[kind].items())


def to_frame(spark: SparkSession, kind: str, rows: Sequence[Mapping[str, Any]]) -> DataFrame:
    """Build the typed DataFrame of model monitoring table ``kind`` from rows."""
    ddl = ", ".join(f"`{name}` {kind_name}" for name, kind_name in SCHEMAS[kind].items())
    return spark.createDataFrame([typed_row(kind, r) for r in rows], ddl)


def table_name(scope: str, prefix: str, kind: str) -> str:
    """Return ``scope.prefix + kind``, rejecting identifiers that are not plain names."""
    if not _SCOPE.match(scope) or not _PREFIX.match(prefix):
        msg = f"model_monitoring: scope must be catalog.schema (plain names) and prefix letters/digits/_; got {scope!r}"
        raise MonitorError(msg)
    return f"{scope}.{prefix}{kind}"


def _trusted(scope: str, prefix: str, table: str) -> str:
    """Return the SQL selecting the rows of ``table`` from the latest finished run per model, version and period."""
    runs = table_name(scope, prefix, RUNS)
    finished = ", ".join(f"'{s}'" for s in FINISHED)
    return (
        f"SELECT t.* FROM {table_name(scope, prefix, table)} t\n"  # noqa: S608 - names checked by table_name
        f"JOIN {runs} r ON t.run_id = r.run_id AND t.model = r.model AND r.status IN ({finished})\n"
        f"JOIN (SELECT x.model, x.model_version, x.monitoring_period, max(y.finished_at) AS finished_at\n"
        f"      FROM {table_name(scope, prefix, table)} x JOIN {runs} y\n"
        f"        ON x.run_id = y.run_id AND x.model = y.model AND y.status IN ({finished})\n"
        f"      GROUP BY x.model, x.model_version, x.monitoring_period) l\n"
        f"  ON t.model = l.model AND t.model_version = l.model_version\n"
        f"  AND t.monitoring_period = l.monitoring_period AND r.finished_at = l.finished_at"
    )


def _latest_alerts(scope: str, prefix: str) -> str:
    """Return the SQL selecting, for each model and alert id, the row of the latest finished run that stored it.

    An alert id is the stable identity of a span: a later run moves its period or closes it, so only its latest
    stored row says what state it is in. A row is dropped when a finished run newer than the one that stored it
    covered the alert's monitoring period (stored results for that model version and period) and stored no row of
    the alert: a rerun of the period stopped raising it (a light alert is written only while red or yellow, and a
    span a rerun no longer opens is not written at all), so it is cleared, not open.
    """
    runs = table_name(scope, prefix, RUNS)
    alerts = table_name(scope, prefix, ALERTS)
    results = table_name(scope, prefix, RESULTS)
    finished = ", ".join(f"'{s}'" for s in FINISHED)
    return (
        f"SELECT t.* FROM {alerts} t\n"  # noqa: S608 - names checked by table_name
        f"JOIN {runs} r ON t.run_id = r.run_id AND t.model = r.model AND r.status IN ({finished})\n"
        f"JOIN (SELECT x.model, x.alert_id, max(y.finished_at) AS finished_at\n"
        f"      FROM {alerts} x JOIN {runs} y\n"
        f"        ON x.run_id = y.run_id AND x.model = y.model AND y.status IN ({finished})\n"
        f"      GROUP BY x.model, x.alert_id) l\n"
        f"  ON t.model = l.model AND t.alert_id = l.alert_id AND r.finished_at = l.finished_at\n"
        f"WHERE NOT EXISTS (SELECT 1 FROM {results} c JOIN {runs} n\n"
        f"    ON c.run_id = n.run_id AND c.model = n.model AND n.status IN ({finished})\n"
        f"  WHERE c.model = t.model AND c.model_version = t.model_version\n"
        f"    AND c.monitoring_period = t.monitoring_period AND n.finished_at > r.finished_at)"
    )


def _finished(scope: str, prefix: str) -> str:
    runs = table_name(scope, prefix, RUNS)
    statuses = ", ".join(repr(s) for s in FINISHED)
    return f"SELECT model, run_id, finished_at FROM {runs} WHERE status IN ({statuses})"  # noqa: S608


def _view(name: str, *lines: str) -> str:
    """Return ``CREATE OR REPLACE VIEW name AS`` over the SQL ``lines`` (names come from :func:`table_name`)."""
    return f"CREATE OR REPLACE VIEW {name} AS\n" + "\n".join(lines)


def _declared(scope: str, prefix: str, declared: Sequence[str]) -> str:
    """Return the SQL listing the declared models: the names given, and every model with a run of any status."""
    names = [str(n) for n in dict.fromkeys(declared)]
    if any(not n or "\\" in n or any(not c.isprintable() for c in n) for n in names):
        msg = f"model_monitoring: declared model names must be non-empty text without backslashes; got {names!r}"
        raise MonitorError(msg)
    sql = f"SELECT DISTINCT model FROM {table_name(scope, prefix, RUNS)}"  # noqa: S608 - names checked by table_name
    return " UNION ".join([sql, *[f"SELECT '{n.replace(chr(39), chr(39) * 2)}' AS model" for n in names]])


def _governance(scope: str, prefix: str, declared: Sequence[str] = ()) -> list[str]:
    """Return the governance views: actions, acceptances, coverage and timeliness, response times, recurrence."""
    actions = table_name(scope, prefix, ACTIONS)
    columns = "action_id, model, alert_id, action, reason, author, valid_until, created_at"
    latest_actions = table_name(scope, prefix, "actions_latest")
    alert_response = table_name(scope, prefix, "alert_response")
    finished = _finished(scope, prefix)
    first_action = f"SELECT model, alert_id, min(created_at) AS first_action_at FROM {actions} GROUP BY 1, 2"  # noqa: S608
    closed = (
        f"SELECT model, alert_id, min(created_at) AS closed_at FROM {actions} WHERE action = 'closed' GROUP BY 1, 2"  # noqa: S608
    )
    return [
        _view(
            latest_actions,
            f"SELECT {columns} FROM (",  # noqa: S608
            f"  SELECT {columns}, row_number() OVER (PARTITION BY model, alert_id",
            f"    ORDER BY created_at DESC, action_id DESC) AS rn FROM {actions}) WHERE rn = 1",
        ),
        _view(
            table_name(scope, prefix, "acceptances"),
            "SELECT x.model, x.alert_id, x.action_id, x.reason, x.author, x.valid_until, x.created_at,",
            "  CASE WHEN l.action_id <> x.action_id THEN 'superseded'",
            "       WHEN x.valid_until >= current_date THEN 'active' ELSE 'expired' END AS status,",
            "  datediff(x.valid_until, current_date) AS days_left",
            f"FROM {actions} x JOIN {latest_actions} l ON x.model = l.model AND x.alert_id = l.alert_id",
            "WHERE x.action = 'risk_accepted'",
        ),
        _view(
            table_name(scope, prefix, "coverage"),
            "SELECT c.model, c.model_version, c.periods, c.first_period, c.latest_period, f.last_run,",
            "  datediff(current_date, to_date(f.last_run)) AS days_since_run",
            "FROM (SELECT model, model_version, count(DISTINCT monitoring_period) AS periods,",
            "        min(monitoring_period) AS first_period, max(monitoring_period) AS latest_period",
            f"      FROM {table_name(scope, prefix, 'latest')} GROUP BY model, model_version) c",
            f"JOIN (SELECT model, max(finished_at) AS last_run FROM ({finished}) GROUP BY model) f",  # noqa: S608
            "  ON c.model = f.model",
        ),
        _view(
            alert_response,
            "SELECT s.model, s.alert_id, s.kind, s.metric, s.first_seen, f.first_action_at, c.closed_at,",
            "  datediff(to_date(f.first_action_at), to_date(s.first_seen)) AS days_to_first_action,",
            "  datediff(to_date(c.closed_at), to_date(s.first_seen)) AS days_to_resolve",
            "FROM (SELECT t.model, t.alert_id, max(t.kind) AS kind, max(t.metric) AS metric,",
            "        min(t.created_at) AS first_seen",
            f"      FROM {table_name(scope, prefix, ALERTS)} t",
            f"      JOIN ({finished}) r ON t.run_id = r.run_id AND t.model = r.model",
            "      GROUP BY t.model, t.alert_id) s",
            f"LEFT JOIN ({first_action}) f ON s.model = f.model AND s.alert_id = f.alert_id",
            f"LEFT JOIN ({closed}) c ON s.model = c.model AND s.alert_id = c.alert_id",
        ),
        _view(
            table_name(scope, prefix, "response_summary"),
            "SELECT model, count(*) AS alerts, count(first_action_at) AS acted, count(closed_at) AS resolved,",
            "  avg(days_to_first_action) AS mean_days_to_first_action, avg(days_to_resolve) AS mean_days_to_resolve",
            f"FROM {alert_response} GROUP BY model",
        ),
        *_governance_summaries(scope, prefix, declared),
    ]


def _governance_summaries(scope: str, prefix: str, declared: Sequence[str]) -> list[str]:
    """Return the views a governance review reads first: coverage of the latest closing, severities, recurrence."""
    latest = table_name(scope, prefix, "latest")
    coverage = table_name(scope, prefix, "coverage_latest")
    finished = _finished(scope, prefix)
    return [
        _view(
            coverage,
            "SELECT d.model, g.latest_closing, l.latest_period, f.last_run,",
            "  CASE WHEN l.latest_period = g.latest_closing THEN 1 ELSE 0 END AS monitored,",
            "  datediff(current_date, to_date(f.last_run)) AS days_since_run",
            f"FROM ({_declared(scope, prefix, declared)}) d",
            f"CROSS JOIN (SELECT max(monitoring_period) AS latest_closing FROM {latest}) g",  # noqa: S608
            f"LEFT JOIN (SELECT model, max(monitoring_period) AS latest_period FROM {latest} GROUP BY model) l",  # noqa: S608 - names checked by table_name
            "  ON d.model = l.model",
            f"LEFT JOIN (SELECT model, max(finished_at) AS last_run FROM ({finished}) GROUP BY model) f",  # noqa: S608
            "  ON d.model = f.model",
        ),
        _view(
            table_name(scope, prefix, "coverage_summary"),
            "SELECT latest_closing, count(*) AS declared, sum(monitored) AS monitored,",
            "  cast(sum(monitored) AS DOUBLE) / count(*) AS share_monitored,",
            "  max(days_since_run) AS max_days_since_run",
            f"FROM {coverage} GROUP BY latest_closing",
        ),
        _view(
            table_name(scope, prefix, "open_alerts_by_severity"),
            "SELECT model, severity, count(*) AS open_alerts, min(opened_period) AS oldest_opened_period",
            f"FROM {table_name(scope, prefix, 'open_alerts')} GROUP BY model, severity",
        ),
        _view(
            table_name(scope, prefix, "alert_recurrence"),
            "SELECT model, metric, count(DISTINCT alert_id) AS alerts,",
            "  count(DISTINCT CASE WHEN recurrence THEN alert_id END) AS recurring,",
            "  max(flagged_periods) AS max_flagged_periods",
            f"FROM ({_latest_alerts(scope, prefix)}) a GROUP BY model, metric",
        ),
        _view(
            table_name(scope, prefix, "acceptance_summary"),
            "SELECT model, sum(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active,",
            "  sum(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) AS expired,",
            "  sum(CASE WHEN status = 'superseded' THEN 1 ELSE 0 END) AS superseded,",
            "  min(CASE WHEN status = 'active' THEN days_left END) AS days_to_next_expiry",
            f"FROM {table_name(scope, prefix, 'acceptances')} GROUP BY model",
        ),
    ]


def view_statements(scope: str, prefix: str = "model_monitoring_", declared: Sequence[str] = ()) -> list[str]:
    """Return ``CREATE OR REPLACE VIEW`` statements for the dashboard views of the tables in ``scope``.

    ``<prefix>latest``: results of the latest finished run per model, version and monitoring period.
    ``<prefix>latest_details``: the per-bin details of the same runs. ``<prefix>model_status``: the worst light
    per model, version and monitoring period with red, yellow and error counts. ``<prefix>open_alerts``: the open
    alerts (light alerts, and persistence and trend spans still open), each in its latest stored state: an alert is
    open only if the newest finished run covering its monitoring period still stored it (a rerun that stops raising
    it clears it), and its latest stored state is open; alerts written before alert states existed count as open,
    and alerts a person closed or accepted are not. The governance views follow: ``<prefix>actions_latest`` (the
    latest action per alert),
    ``<prefix>acceptances`` (each risk acceptance as active, expired or superseded, with the days left),
    ``<prefix>coverage`` (periods monitored and days since the last finished run), ``<prefix>alert_response``
    (days from first seen to first action and to closure) and ``<prefix>response_summary`` (their means per model).
    ``<prefix>coverage_latest`` marks each declared model as monitored or not for the latest closing (the greatest
    monitoring period of any model), ``<prefix>coverage_summary`` gives the share monitored,
    ``<prefix>open_alerts_by_severity``, ``<prefix>alert_recurrence`` and ``<prefix>acceptance_summary`` count what
    stays open, what recurs and which acceptances hold. A model is declared when it is in ``declared`` or has a run.
    """
    latest = table_name(scope, prefix, "latest")
    status = (
        f"CREATE OR REPLACE VIEW {table_name(scope, prefix, 'model_status')} AS\n"
        f"SELECT model, model_version, monitoring_period, max(period_date) AS period_date,\n"
        f"  max(light_rank) AS light_rank,\n"
        f"  CASE max(light_rank) WHEN 2 THEN 'red' WHEN 1 THEN 'yellow' WHEN 0 THEN 'green' ELSE 'no_rule' END"
        f" AS light,\n"
        f"  sum(CASE WHEN light = 'red' THEN 1 ELSE 0 END) AS red,\n"
        f"  sum(CASE WHEN light = 'yellow' THEN 1 ELSE 0 END) AS yellow,\n"
        f"  sum(CASE WHEN reason LIKE 'error:%' THEN 1 ELSE 0 END) AS errors,\n"
        f"  max(CASE WHEN `column` = '' AND segment = '' AND metric = 'row_count' THEN value END) AS row_count\n"
        f"FROM {latest} GROUP BY model, model_version, monitoring_period"
    )
    open_alerts = (
        f"CREATE OR REPLACE VIEW {table_name(scope, prefix, 'open_alerts')} AS\n"
        f"SELECT * FROM ({_latest_alerts(scope, prefix)}) a\nWHERE coalesce(a.state, 'open') = 'open'"
    )
    return [
        f"CREATE OR REPLACE VIEW {latest} AS\n{_trusted(scope, prefix, RESULTS)}",
        f"CREATE OR REPLACE VIEW {table_name(scope, prefix, 'latest_details')} AS\n{_trusted(scope, prefix, DETAILS)}",
        status,
        open_alerts,
        *_governance(scope, prefix, declared),
    ]


__all__ = [
    "ACTIONS",
    "ALERTS",
    "BINNINGS",
    "DETAILS",
    "FAILED",
    "FINISHED",
    "KEYS",
    "KINDS",
    "PARTIAL",
    "RESULTS",
    "RUNS",
    "RUN_STATUSES",
    "SCHEMAS",
    "SUCCESS",
    "key_of",
    "merge_rows",
    "table_name",
    "to_frame",
    "typed_row",
    "view_statements",
    "write_options",
]
