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
KINDS = (BINNINGS, PROFILES, RESULTS, ALERTS, ACTIONS, RUNS)
#: The tables a declaration can have: the revisions table exists only with ``revision`` on.
ALL_KINDS = (*KINDS, REVISIONS)
SCHEMAS: dict[str, dict[str, str]] = {
    BINNINGS: {
        "dataset": "STRING", "version": "INT", "config_fingerprint": "STRING", "fingerprint": "STRING",
        "period_column": "STRING", "fit_periods": "STRING", "fit_rows": "BIGINT", "columns": "INT",
        "skipped": "STRING", "body": "STRING", "created_run_id": "STRING", "created_at": "STRING",
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
        "trend_status": "STRING", "trend_expected": "DOUBLE", "trend_deviation": "DOUBLE", "run_id": "STRING",
        "computed_at": "STRING", "rule_id": "STRING", "rule_version": "INT",
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
    RUNS: {
        "run_id": "STRING", "dataset": "STRING", "task_id": "STRING", "physical": "STRING", "version": "INT",
        "latest_period": "STRING", "periods_profiled": "INT", "status": "STRING", "gate": "STRING",
        "red": "INT", "yellow": "INT", "open_alerts": "INT", "critical_alerts": "INT", "message": "STRING",
        "judgment_fingerprint": "STRING", "started_at": "STRING", "finished_at": "STRING",
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
}
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


def view_statements(scope: str, prefix: str = "stability_") -> list[str]:
    """Return ``CREATE OR REPLACE VIEW`` statements for the dashboard views of the tables in ``scope``.

    ``<prefix>latest``: results of the latest period of each dataset. ``<prefix>dataset_status``: the worst
    status per dataset and period with red and yellow counts. ``<prefix>open_alerts``: alerts still open.
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
    open_alerts = (
        f"CREATE OR REPLACE VIEW {table_name(scope, prefix, 'open_alerts')} AS\n"
        f"SELECT * FROM {alerts} WHERE state = 'open'"
    )
    return [latest, status, open_alerts]


__all__ = [
    "ACTIONS",
    "ALERTS",
    "ALL_KINDS",
    "BINNINGS",
    "KEYS",
    "KINDS",
    "PROFILES",
    "RESULTS",
    "REVISIONS",
    "RUNS",
    "SCHEMAS",
    "key_of",
    "merge_rows",
    "table_name",
    "to_frame",
    "typed_row",
    "view_statements",
    "write_options",
]
