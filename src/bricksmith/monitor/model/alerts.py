"""Replay model monitoring alerts from stored results, without recomputing any metric.

Stored result rows already carry each value and its light. :func:`replay_alerts` keeps the rows of the latest
finished run per model, model version and monitoring period (the rule the dashboard views apply), optionally
recolors the stored values with a declaration's current limits, and rebuilds the alert rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.monitor.limits import classify, rank
from bricksmith.monitor.model.engine import VOLUME_METRICS, alert_rows
from bricksmith.monitor.model.tables import FINISHED

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from bricksmith.monitor.model.config import ModelMonitorConfig


def trusted(results: Iterable[Mapping[str, Any]], runs: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return the result rows of the latest finished run per model, model version and monitoring period."""
    finished = {(r["run_id"], r["model"]): str(r.get("finished_at") or "") for r in runs if r.get("status") in FINISHED}
    rows = [dict(r) for r in results if (r["run_id"], r["model"]) in finished]
    latest: dict[tuple[Any, ...], str] = {}
    for r in rows:
        key = (r["model"], r.get("model_version"), r["monitoring_period"])
        latest[key] = max(latest.get(key, ""), finished[r["run_id"], r["model"]])
    return [
        r
        for r in rows
        if finished[r["run_id"], r["model"]] == latest[r["model"], r.get("model_version"), r["monitoring_period"]]
    ]


def relight(row: Mapping[str, Any], config: ModelMonitorConfig) -> dict[str, Any]:
    """Return ``row`` colored again with the declaration's current limits (the stored value is kept as is)."""
    limits = config.limits_for(str(row["metric"]))
    minimum = 0 if row["metric"] in VOLUME_METRICS else config.min_rows
    reason = str(row.get("reason") or "")
    value = row.get("value")
    light = classify(value, limits, count=int(row.get("rows") or 0), min_count=minimum, reason=reason)
    return {
        **row,
        "light": light.status,
        "light_rank": rank(light.status),
        "reason": reason or light.reason,
        "green_limit": limits.green if limits else None,
        "red_limit": limits.red if limits else None,
        "direction": limits.direction if limits else "",
    }


def replay_alerts(
    results: Iterable[Mapping[str, Any]],
    runs: Iterable[Mapping[str, Any]],
    *,
    config: ModelMonitorConfig | None = None,
    created_at: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(results, alerts)`` rebuilt from stored rows of finished runs; ``config`` recolors with new limits."""
    rows = trusted(results, runs)
    if config is not None:
        rows = [relight(r, config) if r["model"] == config.model else r for r in rows]
    return rows, alert_rows(rows, created_at)


__all__ = ["relight", "replay_alerts", "trusted"]
