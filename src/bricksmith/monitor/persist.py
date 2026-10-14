"""Rows for the four monitor tables written by ordinary ``monitor`` tasks (merge strategy, idempotent by key)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.alerts import ALERT, alert_events, evaluate
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.measure import OK, Measurement
from bricksmith.monitor.reference import Reference

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from pyspark.sql import DataFrame, SparkSession

WINDOW_KEYS = ("reference", "version", "window_start", "window_end")
REFERENCE_KEYS = ("reference", "version")
MEASUREMENT_KEYS = (*WINDOW_KEYS, "metric", "subject", "segment")
EVALUATION_KEYS = (*WINDOW_KEYS, "rule", "metric", "subject", "segment")
ALERT_KEYS = EVALUATION_KEYS
SCHEMAS: dict[str, dict[str, str]] = {
    "references": {
        "reference": "STRING", "version": "INT", "fingerprint": "STRING", "fit_source": "STRING",
        "fit_rows": "BIGINT", "body": "STRING",
    },
    "measurements": {
        "reference": "STRING", "version": "INT", "window_start": "STRING", "window_end": "STRING",
        "metric": "STRING", "subject": "STRING", "segment": "STRING", "value": "DOUBLE", "status": "STRING",
        "reason": "STRING", "count": "BIGINT", "detail": "STRING",
    },
    "evaluations": {
        "reference": "STRING", "version": "INT", "window_start": "STRING", "window_end": "STRING",
        "rule": "STRING", "metric": "STRING", "subject": "STRING", "segment": "STRING", "state": "STRING",
        "value": "DOUBLE", "threshold": "DOUBLE", "severity": "STRING", "op": "STRING", "reason": "STRING",
        "count": "BIGINT", "history": "BIGINT",
    },
    "alerts": {
        "reference": "STRING", "version": "INT", "window_start": "STRING", "window_end": "STRING",
        "rule": "STRING", "metric": "STRING", "subject": "STRING", "segment": "STRING", "severity": "STRING",
        "kind": "STRING", "error_class": "STRING", "dedup_key": "STRING", "message": "STRING",
        "details": "STRING", "occurred_at": "STRING",
    },
}  # fmt: skip


def reference_row(reference: Reference) -> dict[str, Any]:
    """Return the row persisting a reference (the body is its canonical JSON)."""
    return {
        "body": reference.to_json(),
        "fingerprint": reference.fingerprint,
        "fit_rows": reference.fit_rows,
        "fit_source": reference.fit_source,
        "reference": reference.name,
        "version": reference.version,
    }


def measurement_rows(
    reference: Reference, window: Mapping[str, str], measurements: Iterable[Measurement]
) -> list[dict[str, Any]]:
    """Return one row per measurement, keyed by reference version and window (``""`` bounds when unwindowed)."""
    base = {
        "reference": reference.name, "version": reference.version,
        "window_start": window.get("start", ""), "window_end": window.get("end", ""),
    }  # fmt: skip
    return [
        {
            **base, "metric": m.metric, "subject": m.subject, "segment": m.segment,
            "value": m.value, "status": m.status,
            "reason": m.reason, "count": m.count, "detail": json.dumps(m.detail, sort_keys=True),
        }
        for m in measurements
    ]  # fmt: skip


def load_reference(rows: Iterable[Mapping[str, Any]], name: str, version: int | None = None) -> Reference:
    """Return the reference ``name`` (the highest version unless ``version`` is given) from reference rows."""
    found = [r for r in rows if r["reference"] == name and (version is None or r["version"] == version)]
    if not found:
        msg = f"no stored reference {name!r}" + (f" version {version}" if version else "")
        raise MonitorError(msg)
    return Reference.from_json(max(found, key=lambda r: r["version"])["body"])


def _measurement(row: Mapping[str, Any]) -> Measurement:
    return Measurement.from_dict({**row, "detail": json.loads(row.get("detail") or "{}")})


def evaluate_stored(
    reference: Reference, measurement_table: Iterable[Mapping[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate the reference's alert policy for every stored window; history is each key's earlier ok windows.

    Returns ``(evaluation_rows, alert_rows)``. Rows of other references or versions are ignored.
    """
    mine = [r for r in measurement_table if r["reference"] == reference.name and r["version"] == reference.version]
    windows = sorted({(r["window_start"], r["window_end"]) for r in mine})
    evaluation_rows: list[dict[str, Any]] = []
    alert_rows: list[dict[str, Any]] = []
    for start, end in windows:
        history: dict[tuple[str, str, str], list[float]] = {}
        for r in mine:
            if r["window_end"] < end and r["status"] == OK and r["value"] is not None:
                history.setdefault((r["metric"], r["subject"], r["segment"]), []).append(float(r["value"]))
        current = [_measurement(r) for r in mine if (r["window_start"], r["window_end"]) == (start, end)]
        evaluations = evaluate(current, reference.alert_policy, history)
        base = {"reference": reference.name, "version": reference.version, "window_start": start, "window_end": end}
        evaluation_rows += [{**base, **e.to_dict()} for e in evaluations]
        fired = [x for x in evaluations if x.state == ALERT]
        for event, e in zip(alert_events(reference, fired, end), fired, strict=True):
            alert_rows.append(
                {
                    **base, "rule": e.rule, "metric": e.metric, "subject": e.subject, "segment": e.segment,
                    "severity": event.severity, "kind": event.kind, "error_class": event.error_class,
                    "dedup_key": event.dedup_key, "message": event.message, "occurred_at": event.occurred_at,
                    "details": json.dumps(dict(event.details), sort_keys=True),
                }
            )  # fmt: skip
    return evaluation_rows, alert_rows


def to_frame(spark: SparkSession, table: str, rows: Sequence[Mapping[str, Any]]) -> DataFrame:
    """Build a typed DataFrame for ``table`` (``references``, ``measurements``, ``evaluations`` or ``alerts``)."""
    schema = SCHEMAS[table]
    ddl = ", ".join(f"{name} {kind}" for name, kind in schema.items())
    return spark.createDataFrame([tuple(r.get(c) for c in schema) for r in rows], ddl)


def table_rows(frame: DataFrame) -> list[dict[str, Any]]:
    """Collect a (small) monitor table into dictionaries."""
    return [r.asDict() for r in frame.collect()]
