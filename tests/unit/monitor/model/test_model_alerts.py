"""Alerts replayed from stored lights: finished runs only, latest run wins, new limits recolor stored values."""
# ruff: noqa: PT018 - dense assertions on alert rows

from __future__ import annotations

from typing import Any

from bricksmith.monitor.model.alerts import replay_alerts, trusted
from bricksmith.monitor.model.config import parse_config


def result(run: str, period: str, value: float, light: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": "m", "model_version": "1", "run_id": run, "monitoring_period": period, "data_period": period,
            "segment": "", "column": "score", "metric": "psi", "family": "score_psi", "value": value,
            "light": light, "rows": 500, "reason": "", **extra}  # fmt: skip


RUNS = [
    {"run_id": "r1", "model": "m", "status": "success", "finished_at": "2026-03-01"},
    {"run_id": "r2", "model": "m", "status": "partial", "finished_at": "2026-03-02"},
    {"run_id": "r3", "model": "m", "status": "failed", "finished_at": "2026-03-03"},
]
RESULTS = [
    result("r1", "2026-01", 0.30, "red"),
    result("r1", "2026-02", 0.05, "green"),
    result("r2", "2026-02", 0.15, "yellow"),
    result("r3", "2026-02", 0.50, "red"),  # failed run: ignored
    result("r9", "2026-02", 0.90, "red"),  # no run record: ignored
]


def test_trusted_rows_are_the_latest_finished_run() -> None:
    rows = trusted(RESULTS, RUNS)
    assert sorted((r["monitoring_period"], r["run_id"]) for r in rows) == [("2026-01", "r1"), ("2026-02", "r2")]


def test_replay_rebuilds_alerts_from_stored_lights() -> None:
    rows, alerts = replay_alerts(RESULTS, RUNS, created_at="now")
    assert len(rows) == 2
    assert sorted((a["monitoring_period"], a["severity"], a["run_id"]) for a in alerts) == [
        ("2026-01", "critical", "r1"),
        ("2026-02", "warning", "r2"),
    ]
    assert all(a["created_at"] == "now" and len(a["alert_id"]) == 16 for a in alerts)
    again = replay_alerts(RESULTS, RUNS, created_at="later")[1]
    assert [a["alert_id"] for a in again] == [a["alert_id"] for a in alerts]  # ids are stable across replays


def test_new_limits_recolor_stored_values_without_measuring() -> None:
    config = parse_config({
        "model": "m", "period": "month", "score": "score", "references": {"development": {"start": "a", "end": "b"}},
        "limits": {"psi": {"green": 0.4, "red": 0.6}},
    })  # fmt: skip
    rows, alerts = replay_alerts(RESULTS, RUNS, config=config)
    assert {r["light"] for r in rows} == {"green"} and alerts == []
    assert rows[0]["green_limit"] == 0.4 and rows[0]["value"] in (0.30, 0.15)
    small = [result("r1", "2026-01", 0.9, "red", rows=3)]
    relit, none = replay_alerts(small, RUNS, config=config)
    assert relit[0]["light"] == "insufficient_volume" and none == []
