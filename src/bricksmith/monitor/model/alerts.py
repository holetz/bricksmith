"""Replay model monitoring alerts from stored results, without recomputing any metric.

Stored result rows already carry each value and its light. :func:`replay_alerts` keeps the rows of the latest
finished run per model, model version and monitoring period (the rule the dashboard views apply), optionally
recolors the stored values with a declaration's current limits (relative and control limits read the stored
reference values and baselines), re-checks their trend, and rebuilds the light, persistence and trend alerts. The
human actions given (analysis, closure, risk acceptance) are folded into the rebuilt alerts at an evaluation date.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.limits import (
    CONTROL,
    INFORMATIONAL,
    INSUFFICIENT_VOLUME,
    NO_RULE,
    RELATIVE,
    Light,
    Limits,
    judge,
    rank,
)
from bricksmith.monitor.model.actions import apply_actions
from bricksmith.monitor.model.config import OUT_OF_TIME
from bricksmith.monitor.model.engine import (
    EXPECTED_METRICS,
    VOLUME_METRICS,
    alert_rows,
    apply_trend,
    stored_expected_events,
)
from bricksmith.monitor.model.tables import FINISHED
from bricksmith.monitor.rules import INFORMATIONAL_REASON

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


def _baseline(
    row: Mapping[str, Any], config: ModelMonitorConfig, limits: Limits | None
) -> tuple[float | None, float | None, int | None]:
    """Return ``(baseline, spread, points)`` for the limits that apply to ``row`` from its stored columns."""
    if limits is not None and limits.kind == RELATIVE:
        column = "out_of_time_value" if limits.reference == OUT_OF_TIME else "development_value"
        return row.get(column), None, None
    if limits is not None and limits.kind == CONTROL:
        points = row.get("baseline_points")
        enough = points is not None and int(points) >= config.control_min_points
        mean, sd = (row.get("baseline_mean"), row.get("baseline_sd")) if enough else (None, None)
        return mean, sd, None if points is None else int(points)
    return None, None, None


def relight(row: Mapping[str, Any], config: ModelMonitorConfig) -> dict[str, Any]:
    """Return ``row`` colored again with the declaration's current limits (the stored value is kept as is).

    A control limit needs the baseline a run stored for that metric; a row measured before it was declared is
    ``no_baseline``.
    """
    metric = str(row["metric"])
    found = config.resolve(
        column=str(row.get("column") or ""),
        segment=str(row.get("segment") or ""),
        metric=metric,
        period_date=str(row.get("period_date") or "") or None,
    )
    limits = None if found.informational else config.limits_of(found, metric)
    minimum = 0 if metric in VOLUME_METRICS or metric in EXPECTED_METRICS else config.min_rows
    reason = str(row.get("reason") or "")
    reason = "" if reason == INFORMATIONAL_REASON else reason
    events = stored_expected_events(reason) if metric in EXPECTED_METRICS and row.get("value") is not None else None
    gated = events is not None and events < config.credit_options.min_expected_events
    if events is not None and not gated:
        reason = ""  # the gate no longer applies under the declaration's current minimum
    value = row.get("value")
    baseline, spread, points = _baseline(row, config, limits)
    control = limits is not None and limits.kind == CONTROL
    light, compared = judge(
        value, limits, baseline=baseline, spread=spread, count=int(row.get("rows") or 0), min_count=minimum,
        reason=reason,
    )  # fmt: skip
    if gated:
        light = Light(INSUFFICIENT_VOLUME, reason)
    if found.informational and light.status == NO_RULE:
        light = Light(INFORMATIONAL, INFORMATIONAL_REASON)
    return {
        **row,
        "limit_kind": limits.kind if limits else "",
        "limit_reference": limits.reference if limits else "",
        "compared_value": compared if compared is None or math.isfinite(compared) else None,
        "baseline_mean": baseline if control else None,
        "baseline_sd": spread if control else None,
        "baseline_points": points if control else None,
        "trend_status": "",
        "trend_expected": None,
        "trend_deviation": None,
        "trend_sigma": None,
        "trend_lower": None,
        "trend_upper": None,
        "rule_id": found.rule_id,
        "rule_version": found.rule_version,
        "light": light.status,
        "light_rank": rank(light.status),
        "reason": reason or light.reason,
        "green_limit": limits.green if limits else None,
        "red_limit": limits.red if limits else None,
        "direction": limits.direction if limits else "",
    }


def replay_alerts(  # noqa: PLR0913 - one keyword per documented input
    results: Iterable[Mapping[str, Any]],
    runs: Iterable[Mapping[str, Any]],
    *,
    config: ModelMonitorConfig | None = None,
    created_at: str = "",
    actions: Iterable[Mapping[str, Any]] = (),
    as_of: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(results, alerts)`` rebuilt from stored rows of finished runs.

    ``config`` recolors the rows of its model with its limits, re-checks their trend and replays them with its
    persistence policy; without it the stored lights and trend statuses are replayed with the default policy.
    ``actions`` are the stored human actions; each alert takes the state its latest action gives on the date
    ``as_of`` (default: the date of ``created_at``).
    """
    rows = trusted(results, runs)
    if config is None:
        return rows, apply_actions(alert_rows(rows, created_at), actions, as_of or created_at[:10])
    mine = [relight(r, config) for r in rows if r["model"] == config.model]
    apply_trend(mine, config)
    others = [r for r in rows if r["model"] != config.model]
    alerts = alert_rows(mine, created_at, config.persistence) + alert_rows(others, created_at)
    return mine + others, apply_actions(alerts, actions, as_of or created_at[:10])


__all__ = ["relight", "replay_alerts", "trusted"]
