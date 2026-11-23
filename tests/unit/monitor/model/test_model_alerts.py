"""Alerts replayed from stored lights: finished runs only, latest run wins, new limits recolor stored values."""
# ruff: noqa: PT018 - dense assertions on alert rows

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.monitor.model.actions import action_row
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


def _config(**changes: Any) -> Any:  # noqa: ANN401
    return parse_config({
        "model": "m", "period": "month", "score": "score", "min_rows": 0,
        "references": {"development": {"start": "a", "end": "b"}, "out_of_time": {"start": "c", "end": "d"}},
        **changes,
    })  # fmt: skip


def test_relative_and_control_limits_recolor_from_stored_baselines() -> None:
    stored = [
        result("r1", "2026-01", 0.32, "no_rule", metric="ks", development_value=0.40, out_of_time_value=0.30,
               baseline_mean=0.40, baseline_sd=0.02, baseline_points=5),
        result("r1", "2026-02", 0.36, "no_rule", metric="ks", development_value=0.40, out_of_time_value=0.30),
    ]  # fmt: skip
    relative = {"kind": "relative", "green": -0.15, "red": -0.30, "direction": "lower"}
    rows, _ = replay_alerts(stored, RUNS, config=_config(limits={"ks": relative}))
    first = next(r for r in rows if r["monitoring_period"] == "2026-01")
    assert first["light"] == "yellow" and first["compared_value"] == pytest.approx(-0.2)
    assert first["limit_kind"] == "relative" and first["limit_reference"] == "development"
    rows, _ = replay_alerts(stored, RUNS, config=_config(limits={"ks": {**relative, "reference": "out_of_time"}}))
    assert {r["light"] for r in rows} == {"green"}
    chart = {"kind": "control", "green": -2, "red": -3, "direction": "lower"}
    rows, _ = replay_alerts(stored, RUNS, config=_config(limits={"ks": chart}))
    by_period = {r["monitoring_period"]: r for r in rows}
    assert by_period["2026-01"]["light"] == "red" and by_period["2026-01"]["compared_value"] == pytest.approx(-4.0)
    assert by_period["2026-02"]["light"] == "no_baseline"  # measured before a control baseline was stored
    rows, _ = replay_alerts(stored, RUNS, config=_config(limits={"ks": chart}, control_min_points=6))
    assert next(r for r in rows if r["monitoring_period"] == "2026-01")["light"] == "no_baseline"


def test_replay_rebuilds_persistence_and_trend_spans() -> None:
    runs = [{"run_id": "r", "model": "m", "status": "success", "finished_at": "2026-09-01"}]
    months = [f"2026-0{i}" for i in range(1, 8)]
    values = [0.01, 0.01, 0.01, 0.01, 0.30, 0.30, 0.30]
    stored = [result("r", p, v, "green", direction="higher") for p, v in zip(months, values, strict=True)]
    rows, alerts = replay_alerts(stored, runs, config=_config(trend={"window": 4, "min_points": 3}), created_at="t")
    assert {r["light"] for r in rows if r["value"] == 0.30} == {"red"}
    spans = {a["kind"]: a for a in alerts if a["kind"] != "light"}
    assert spans["persistence"]["severity"] == "critical" and spans["persistence"]["opened_period"] == "2026-06"
    assert spans["trend"]["opened_period"] == "2026-05" and spans["trend"]["state"] == "open"
    assert len({a["alert_id"] for a in alerts}) == len(alerts)
    plain_rows, plain = replay_alerts(rows, runs)  # stored lights and trends replay with the default policy
    assert [a["alert_id"] for a in plain] == [a["alert_id"] for a in alerts]
    assert plain_rows == rows


def test_replay_applies_the_latest_action_at_the_evaluation_date() -> None:
    _, alerts = replay_alerts(RESULTS, RUNS, created_at="2026-03-05T00:00:00")
    red = next(a for a in alerts if a["light"] == "red")
    accept = action_row(model="m", alert_id=red["alert_id"], action="risk_accepted", reason="owner signed",
                        author="ana", created_at="2026-03-04T00:00:00", valid_until="2026-03-31")  # fmt: skip
    _, held = replay_alerts(RESULTS, RUNS, created_at="2026-03-05T00:00:00", actions=[accept])
    assert next(a for a in held if a["alert_id"] == red["alert_id"])["state"] == "accepted"
    assert [a["state"] for a in held if a["alert_id"] != red["alert_id"]] == ["open"]
    _, lapsed = replay_alerts(RESULTS, RUNS, created_at="2026-03-05T00:00:00", actions=[accept], as_of="2026-04-01")
    assert next(a for a in lapsed if a["alert_id"] == red["alert_id"])["state"] == "open"
    close = action_row(model="m", alert_id=red["alert_id"], action="closed", reason="fixed upstream", author="bo",
                       created_at="2026-03-06T00:00:00")  # fmt: skip
    _, closed = replay_alerts(RESULTS, RUNS, created_at="2026-03-05T00:00:00", actions=[accept, close])
    found = next(a for a in closed if a["alert_id"] == red["alert_id"])
    assert found["state"] == "closed" and found["action_author"] == "bo"


def test_replay_with_a_declaration_applies_actions_too() -> None:
    config = parse_config({"model": "m", "period": "month", "score": "score",
                           "references": {"development": {"start": "a", "end": "b"}},
                           "limits": {"psi": {"green": 0.05, "red": 0.1}}})  # fmt: skip
    _, alerts = replay_alerts(RESULTS, RUNS, config=config, created_at="2026-03-05T00:00:00")
    target = alerts[0]
    close = action_row(model="m", alert_id=target["alert_id"], action="closed", reason="r", author="a",
                       created_at="2026-03-06T00:00:00")  # fmt: skip
    _, after = replay_alerts(RESULTS, RUNS, config=config, created_at="2026-03-05T00:00:00", actions=[close])
    assert {a["alert_id"]: a["state"] for a in after}[target["alert_id"]] == "closed"
