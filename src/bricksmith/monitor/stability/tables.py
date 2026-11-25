"""The five stability tables (flat, typed, keyed for merge) and the dashboard views built on them.

Every column a report filters or slices on is a plain scalar: no maps, arrays, structs or JSON. Only the
binning ``body`` keeps the reference JSON, which reports never filter on. See ``docs/how-to/stability.md``
for the star model.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.errors import MonitorError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyspark.sql import DataFrame, SparkSession  # pyright: ignore[reportMissingImports]

BINNINGS = "binnings"
PROFILES = "profiles"
RESULTS = "results"
ALERTS = "alerts"
ACTIONS = "actions"
RUNS = "runs"
REVISIONS = "revisions"
RESULTS_HISTORY = "results_history"
ALERT_EVENTS = "alert_events"
KINDS = (BINNINGS, PROFILES, RESULTS, ALERTS, ACTIONS, RUNS)
#: The tables that exist only with ``history`` on: every judgment ever reported, and every alert transition.
HISTORY_KINDS = (RESULTS_HISTORY, ALERT_EVENTS)
#: The tables a declaration can have: ``revisions`` only with ``revision`` on, the history kinds with ``history``.
ALL_KINDS = (*KINDS, REVISIONS, *HISTORY_KINDS)
SCHEMAS: dict[str, dict[str, str]] = {
    BINNINGS: {
        "dataset": "STRING", "version": "INT", "config_fingerprint": "STRING", "fingerprint": "STRING",
        "period_column": "STRING", "fit_periods": "STRING", "fit_rows": "BIGINT", "columns": "INT",
        "skipped": "STRING", "body": "STRING", "created_run_id": "STRING", "created_at": "STRING",
        "schema": "STRING",
    },
    PROFILES: {
        "dataset": "STRING", "version": "INT", "period": "STRING", "period_date": "DATE", "segment": "STRING",
        "segment_label": "STRING", "column": "STRING", "bin": "STRING", "bin_order": "INT", "bin_label": "STRING",
        "count": "BIGINT", "weight": "DOUBLE", "value_sum": "DOUBLE", "value_sum_sq": "DOUBLE", "share": "DOUBLE",
        "baseline_share": "DOUBLE", "contribution": "DOUBLE", "run_id": "STRING",
    },
    RESULTS: {
        "dataset": "STRING", "version": "INT", "period": "STRING", "period_date": "DATE", "segment": "STRING",
        "segment_label": "STRING", "column": "STRING", "metric": "STRING", "value": "DOUBLE", "current": "DOUBLE",
        "baseline": "DOUBLE", "status": "STRING", "status_rank": "INT", "reason": "STRING",
        "green_limit": "DOUBLE", "red_limit": "DOUBLE", "direction": "STRING", "rows": "BIGINT",
        "baseline_rows": "DOUBLE", "in_baseline": "BOOLEAN", "baseline_kind": "STRING", "baseline_label": "STRING",
        "trend_status": "STRING", "trend_expected": "DOUBLE", "trend_deviation": "DOUBLE", "trend_sigma": "DOUBLE",
        "trend_lower": "DOUBLE", "trend_upper": "DOUBLE", "trend_model": "STRING", "drift_status": "STRING",
        "change_point_suggested": "BOOLEAN", "run_id": "STRING",
        "computed_at": "STRING", "rule_id": "STRING", "rule_version": "INT", "baseline_gap": "INT",
        "settling": "BOOLEAN",
    },
    ALERTS: {
        "dataset": "STRING", "alert_id": "STRING", "version": "INT", "kind": "STRING", "metric": "STRING",
        "column": "STRING", "segment": "STRING", "segment_label": "STRING", "severity": "STRING",
        "severity_rank": "INT", "state": "STRING", "opened_period": "STRING", "escalated_period": "STRING",
        "closed_period": "STRING", "last_period": "STRING", "last_status": "STRING", "last_value": "DOUBLE",
        "flagged_periods": "INT", "recurrence": "BOOLEAN", "message": "STRING", "run_id": "STRING",
        "updated_at": "STRING",
    },
    ACTIONS: {
        "action_id": "STRING", "dataset": "STRING", "alert_id": "STRING", "action": "STRING",
        "resolution": "STRING", "reason": "STRING", "author": "STRING", "valid_until": "DATE",
        "created_at": "STRING",
    },
    REVISIONS: {
        "dataset": "STRING", "version": "INT", "period": "STRING", "period_date": "DATE", "segment": "STRING",
        "segment_label": "STRING", "column": "STRING", "bin": "STRING", "bin_order": "INT", "bin_label": "STRING",
        "count": "BIGINT", "weight": "DOUBLE", "value_sum": "DOUBLE", "value_sum_sq": "DOUBLE", "run_id": "STRING",
        "published_at": "STRING",
    },
    RESULTS_HISTORY: {
        "dataset": "STRING", "version": "INT", "period": "STRING", "period_date": "DATE", "segment": "STRING",
        "segment_label": "STRING", "column": "STRING", "metric": "STRING", "value": "DOUBLE", "current": "DOUBLE",
        "baseline": "DOUBLE", "status": "STRING", "status_rank": "INT", "reason": "STRING",
        "green_limit": "DOUBLE", "red_limit": "DOUBLE", "direction": "STRING", "rows": "BIGINT",
        "baseline_kind": "STRING", "baseline_label": "STRING", "trend_status": "STRING", "rule_id": "STRING",
        "rule_version": "INT", "run_id": "STRING", "judged_at": "STRING", "judgment_fingerprint": "STRING",
        "data_version": "BIGINT",
    },
    ALERT_EVENTS: {
        "dataset": "STRING", "alert_id": "STRING", "event": "STRING", "run_id": "STRING", "period": "STRING",
        "severity": "STRING", "state": "STRING", "resolution": "STRING", "action_id": "STRING",
        "message": "STRING", "occurred_at": "STRING",
    },
    RUNS: {
        "run_id": "STRING", "dataset": "STRING", "task_id": "STRING", "physical": "STRING", "version": "INT",
        "latest_period": "STRING", "periods_profiled": "INT", "status": "STRING", "gate": "STRING",
        "red": "INT", "yellow": "INT", "open_alerts": "INT", "critical_alerts": "INT", "message": "STRING",
        "judgment_fingerprint": "STRING", "started_at": "STRING", "finished_at": "STRING",
        "periods_reprocessed": "STRING", "schema": "STRING",
    },
}  # fmt: skip
KEYS: dict[str, tuple[str, ...]] = {
    BINNINGS: ("dataset", "version"),
    PROFILES: ("dataset", "version", "period", "segment", "column", "bin"),
    RESULTS: ("dataset", "version", "period", "segment", "column", "metric"),
    ALERTS: ("dataset", "alert_id"),
    ACTIONS: ("dataset", "action_id"),
    RUNS: ("run_id", "dataset"),
    REVISIONS: ("dataset", "version", "period", "segment", "column", "bin"),
    RESULTS_HISTORY: ("dataset", "run_id", "version", "period", "segment", "column", "metric"),
    ALERT_EVENTS: ("dataset", "alert_id", "event", "run_id", "action_id"),
}
_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})?)?$")
_AS_OF_KEY = ("dataset", "period", "segment", "column", "metric")
_SCOPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_PREFIX = re.compile(r"^[A-Za-z0-9_]*$")


def write_options(kind: str) -> dict[str, object]:
    """Return the write metadata of a stability table.

    ``merge`` on its key; an empty write creates it; ``evolution`` adds columns introduced after the table existed.
    """
    return {
        "write": "merge",
        "keys": list(KEYS[kind]),
        "on_empty": "allow_empty",
        "evolution": {},
        "stability_table": kind,
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


def typed_row(kind: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return ``row`` as a tuple in schema order, with ISO text dates converted for ``DATE`` columns."""
    from datetime import date  # noqa: PLC0415 - tiny

    values = []
    for column, kind_name in SCHEMAS[kind].items():
        value = row.get(column)
        if value is not None:
            value = _cast(kind_name, value, date)
        values.append(value)
    return tuple(values)


def _cast(kind_name: str, value: object, date_type: type) -> object:
    """Return ``value`` as the Python type of the column (Spark rejects an int in a DOUBLE column)."""
    if kind_name == "DATE":
        return date_type.fromisoformat(value) if isinstance(value, str) else value  # type: ignore[attr-defined]
    if kind_name == "DOUBLE":
        return float(value)  # type: ignore[arg-type]
    if kind_name in ("INT", "BIGINT"):
        return int(value)  # type: ignore[arg-type]
    if kind_name == "BOOLEAN":
        return bool(value)
    return value


def to_frame(spark: SparkSession, kind: str, rows: Sequence[Mapping[str, Any]]) -> DataFrame:
    """Build the typed DataFrame of stability table ``kind`` from rows."""
    ddl = ", ".join(f"`{name}` {kind_name}" for name, kind_name in SCHEMAS[kind].items())
    return spark.createDataFrame([typed_row(kind, r) for r in rows], ddl)


def table_name(scope: str, prefix: str, kind: str) -> str:
    """Return ``scope.prefix + kind``, rejecting identifiers that are not plain names."""
    if not _SCOPE.match(scope) or not _PREFIX.match(prefix):
        msg = f"stability: scope must be catalog.schema (plain names) and prefix letters/digits/_; got {scope!r}"
        raise MonitorError(msg)
    return f"{scope}.{prefix}{kind}"


def _as_of(value: str) -> str:
    if not _AS_OF.match(value):
        msg = f"stability: as_of must be an ISO date or UTC datetime (2026-03-31, 2026-03-31T12:00:00); got {value!r}"
        raise MonitorError(msg)
    return value


def _published_by(judged_at: object, as_of: str) -> bool:
    """Tell whether a judgment made at ``judged_at`` was already reported at ``as_of`` (a date covers its whole day)."""
    text = str(judged_at or "")
    return text[:10] <= as_of if len(as_of) == 10 else text <= as_of  # noqa: PLR2004 - an ISO date is ten characters


def results_as_of(history: Sequence[Mapping[str, Any]], as_of: str) -> list[dict[str, Any]]:
    """Return the judgments that stood at ``as_of``: per period, segment, column and metric, the latest one reported.

    ``history`` holds ``results_history`` rows; ``as_of`` is an ISO date (the whole day counts) or a UTC datetime.
    A result first judged after ``as_of`` is absent, as it was for whoever read the monitor then.
    """
    when = _as_of(as_of)
    latest: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in history:
        if not _published_by(row.get("judged_at"), when):
            continue
        key = tuple(row.get(c) for c in _AS_OF_KEY)
        kept = latest.get(key)
        if kept is None or (str(row.get("judged_at")), str(row.get("run_id"))) > (
            str(kept.get("judged_at")),
            str(kept.get("run_id")),
        ):
            latest[key] = dict(row)
    return list(latest.values())


def as_of_statement(scope: str, as_of: str, prefix: str = "stability_") -> str:
    """Return the SQL that reads ``results_history`` as it stood at ``as_of`` (the as-of query, same rule as above).

    A view cannot take a date, so this is generated on demand and meant to be run or wrapped by the reader.
    """
    when = _as_of(as_of)
    history = table_name(scope, prefix, RESULTS_HISTORY)
    stamp = "substr(judged_at, 1, 10)" if len(when) == 10 else "judged_at"  # noqa: PLR2004 - an ISO date
    keys = ", ".join(f"`{c}`" for c in _AS_OF_KEY)
    return (
        f"SELECT * EXCEPT (rn) FROM (\n"
        f"  SELECT h.*, row_number() OVER (PARTITION BY {keys}\n"
        f"    ORDER BY judged_at DESC, run_id DESC) AS rn FROM {history} h WHERE {stamp} <= '{when}') WHERE rn = 1"
    )


def view_statements(scope: str, prefix: str = "stability_") -> list[str]:
    """Return ``CREATE OR REPLACE VIEW`` statements for the dashboard views of the tables in ``scope``.

    ``<prefix>latest``: results of the latest period of each dataset. ``<prefix>dataset_status``: the worst
    status per dataset and period with red and yellow counts. ``<prefix>open_alerts``: alerts still open by their
    automatic state, with the latest
    human action and the ``effective_state`` it gives. ``<prefix>actions_latest``, ``<prefix>alert_response`` and
    ``<prefix>response_summary`` hold the actions and the time to first action and to close;
    ``<prefix>false_positive_rate`` counts the closures typed ``false_positive`` per rule.
    """
    results = table_name(scope, prefix, RESULTS)
    alerts = table_name(scope, prefix, ALERTS)
    latest = (
        f"CREATE OR REPLACE VIEW {table_name(scope, prefix, 'latest')} AS\n"
        f"SELECT r.* FROM {results} r\n"
        f"JOIN (SELECT dataset, max(version) AS version FROM {results} GROUP BY dataset) v\n"
        f"  ON r.dataset = v.dataset AND r.version = v.version\n"
        f"JOIN (SELECT dataset, version, max(period) AS period FROM {results} GROUP BY dataset, version) p\n"
        f"  ON r.dataset = p.dataset AND r.version = p.version AND r.period = p.period"
    )
    status = (
        f"CREATE OR REPLACE VIEW {table_name(scope, prefix, 'dataset_status')} AS\n"
        f"SELECT dataset, version, period, max(period_date) AS period_date, max(status_rank) AS status_rank,\n"
        f"  CASE max(status_rank) WHEN 2 THEN 'red' WHEN 1 THEN 'yellow' WHEN 0 THEN 'green' ELSE 'no_rule' END"
        f" AS status,\n"
        f"  sum(CASE WHEN status = 'red' THEN 1 ELSE 0 END) AS red,\n"
        f"  sum(CASE WHEN status = 'yellow' THEN 1 ELSE 0 END) AS yellow,\n"
        f"  max(CASE WHEN `column` = '' AND segment = '' AND metric = 'row_count' THEN value END) AS row_count\n"
        f"FROM {results} GROUP BY dataset, version, period"
    )
    actions = table_name(scope, prefix, ACTIONS)
    columns = "action_id, dataset, alert_id, action, resolution, reason, author, valid_until, created_at"
    latest_actions = table_name(scope, prefix, "actions_latest")
    actions_latest = (
        f"CREATE OR REPLACE VIEW {latest_actions} AS\n"
        f"SELECT {columns} FROM (\n"
        f"  SELECT {columns}, row_number() OVER (PARTITION BY dataset, alert_id\n"
        f"    ORDER BY created_at DESC, action_id DESC) AS rn FROM {actions}) WHERE rn = 1"
    )
    open_alerts = (
        f"CREATE OR REPLACE VIEW {table_name(scope, prefix, 'open_alerts')} AS\n"
        f"SELECT a.*, l.action, l.resolution, l.reason AS action_reason, l.author AS action_author,\n"
        f"  l.created_at AS action_at, l.valid_until,\n"
        f"  CASE WHEN l.action = 'closed' THEN 'closed'\n"
        f"       WHEN l.action = 'risk_accepted' AND l.valid_until >= current_date THEN 'accepted'\n"
        f"       ELSE 'open' END AS effective_state\n"
        f"FROM {alerts} a LEFT JOIN {latest_actions} l ON a.dataset = l.dataset AND a.alert_id = l.alert_id\n"
        f"WHERE a.state = 'open'"
    )
    response = table_name(scope, prefix, "alert_response")
    alert_response = (
        f"CREATE OR REPLACE VIEW {response} AS\n"
        f"SELECT s.dataset, s.alert_id, s.kind, s.metric, s.first_seen, f.first_action_at, c.closed_at,\n"
        f"  datediff(to_date(f.first_action_at), to_date(s.first_seen)) AS days_to_first_action,\n"
        f"  datediff(to_date(c.closed_at), to_date(s.first_seen)) AS days_to_resolve\n"
        f"FROM (SELECT dataset, alert_id, max(kind) AS kind, max(metric) AS metric, min(updated_at) AS first_seen\n"
        f"      FROM {alerts} GROUP BY dataset, alert_id) s\n"
        f"LEFT JOIN (SELECT dataset, alert_id, min(created_at) AS first_action_at FROM {actions}\n"
        f"           GROUP BY dataset, alert_id) f ON s.dataset = f.dataset AND s.alert_id = f.alert_id\n"
        f"LEFT JOIN (SELECT dataset, alert_id, min(created_at) AS closed_at FROM {actions}\n"
        f"           WHERE action = 'closed' GROUP BY dataset, alert_id) c\n"
        f"  ON s.dataset = c.dataset AND s.alert_id = c.alert_id"
    )
    response_summary = (
        f"CREATE OR REPLACE VIEW {table_name(scope, prefix, 'response_summary')} AS\n"
        f"SELECT dataset, count(*) AS alerts, count(first_action_at) AS acted, count(closed_at) AS resolved,\n"
        f"  avg(days_to_first_action) AS mean_days_to_first_action, avg(days_to_resolve) AS mean_days_to_resolve\n"
        f"FROM {response} GROUP BY dataset"
    )
    false_positives = (
        f"CREATE OR REPLACE VIEW {table_name(scope, prefix, 'false_positive_rate')} AS\n"
        f"SELECT r.rule_id, count(*) AS closed_alerts,\n"
        f"  sum(CASE WHEN l.resolution = 'false_positive' THEN 1 ELSE 0 END) AS false_positives,\n"
        f"  sum(CASE WHEN l.resolution = 'false_positive' THEN 1 ELSE 0 END) / count(*) AS false_positive_rate\n"
        f"FROM {latest_actions} l\n"
        f"JOIN (SELECT dataset, alert_id, max(`column`) AS `column`, max(metric) AS metric, max(segment) AS segment\n"
        f"      FROM {alerts} GROUP BY dataset, alert_id) a ON l.dataset = a.dataset AND l.alert_id = a.alert_id\n"
        f"JOIN (SELECT dataset, segment, `column`, metric, max(rule_id) AS rule_id FROM {results}\n"
        f"      GROUP BY dataset, segment, `column`, metric) r\n"
        f"  ON a.dataset = r.dataset AND a.segment = r.segment AND a.`column` = r.`column` AND a.metric = r.metric\n"
        f"WHERE l.action = 'closed' AND r.rule_id IS NOT NULL GROUP BY r.rule_id"
    )
    return [latest, status, actions_latest, open_alerts, alert_response, response_summary, false_positives]


__all__ = [
    "ACTIONS",
    "ALERTS",
    "ALERT_EVENTS",
    "ALL_KINDS",
    "BINNINGS",
    "HISTORY_KINDS",
    "KEYS",
    "KINDS",
    "PROFILES",
    "RESULTS",
    "RESULTS_HISTORY",
    "REVISIONS",
    "RUNS",
    "SCHEMAS",
    "as_of_statement",
    "key_of",
    "merge_rows",
    "results_as_of",
    "table_name",
    "to_frame",
    "typed_row",
    "view_statements",
    "write_options",
]
