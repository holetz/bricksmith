"""Alert actions: validated rows, the latest action wins, acceptances expire on their date."""
# ruff: noqa: PT018 - dense assertions on alert rows

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.actions import (
    action_row,
    apply_actions,
    insert_statement,
    latest_actions,
)


def make(action: str, at: str, alert: str = "a1", **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    kwargs: dict[str, Any] = {"model": "m", "alert_id": alert, "action": action, "reason": "why", "author": "ana",
                              "created_at": at, **extra}  # fmt: skip
    return action_row(**kwargs)


def alert(alert_id: str = "a1", model: str = "m", state: str = "open") -> dict[str, Any]:
    return {"model": model, "alert_id": alert_id, "state": state, "kind": "persistence", "severity": "critical"}


def test_row_validation() -> None:
    with pytest.raises(MonitorError, match="one of"):
        make("ignore", "t1")
    with pytest.raises(MonitorError, match="reason and author"):
        action_row(model="m", alert_id="a", action="closed", reason=" ", author="ana", created_at="t")
    with pytest.raises(MonitorError, match="requires valid_until"):
        make("risk_accepted", "t1")
    with pytest.raises(MonitorError, match="takes no valid_until"):
        make("closed", "t1", valid_until="2026-12-31")
    with pytest.raises(MonitorError, match="ISO date"):
        make("risk_accepted", "t1", valid_until="next week")


def test_ids_are_deterministic_and_dates_normalized() -> None:
    one = make("risk_accepted", "t1", valid_until="2026-12-31")
    assert one == make("risk_accepted", "t1", valid_until="2026-12-31")
    assert one["action_id"] != make("risk_accepted", "t2", valid_until="2026-12-31")["action_id"]
    assert one["valid_until"] == "2026-12-31" and len(one["action_id"]) == 16


def test_latest_action_wins_per_alert() -> None:
    rows = [make("analysis", "2026-01-01"), make("closed", "2026-01-03"), make("analysis", "2026-01-02")]
    assert latest_actions(rows)["a1"]["action"] == "closed"
    assert latest_actions(rows, "other") == {}


def test_states_follow_the_action() -> None:
    rows = [make("analysis", "2026-01-01", alert="a2"), make("closed", "2026-01-01", alert="a3")]
    out = {a["alert_id"]: a for a in apply_actions([alert("a1"), alert("a2"), alert("a3")], rows, "2026-02-01")}
    assert out["a1"]["state"] == "open" and out["a1"]["action"] is None
    assert out["a2"]["state"] == "open" and out["a2"]["action"] == "analysis" and out["a2"]["action_author"] == "ana"
    assert out["a3"]["state"] == "closed" and out["a3"]["action_reason"] == "why"


def test_acceptance_holds_through_its_date_then_expires() -> None:
    rows = [make("risk_accepted", "2026-01-01", valid_until="2026-03-31")]
    inside = apply_actions([alert()], rows, "2026-03-31")[0]
    after = apply_actions([alert()], rows, "2026-04-01")[0]
    assert inside["state"] == "accepted" and inside["valid_until"] == "2026-03-31"
    assert after["state"] == "open" and after["action"] == "risk_accepted"


def test_later_analysis_supersedes_an_acceptance() -> None:
    rows = [make("risk_accepted", "2026-01-01", valid_until="2026-12-31"), make("analysis", "2026-02-01")]
    assert apply_actions([alert()], rows, "2026-03-01")[0]["state"] == "open"


def test_other_models_and_unknown_ids_change_nothing() -> None:
    rows = [make("closed", "t1", alert="zzz"), {**make("closed", "t1"), "model": "other"}]
    out = apply_actions([alert()], rows, "2026-03-01")[0]
    assert out["state"] == "open" and out["action"] is None


def test_insert_statement_quotes_and_types_the_date() -> None:
    row = make("risk_accepted", "t1", valid_until="2026-12-31")
    row["reason"] = "owner's call"
    sql = insert_statement("main.s.model_monitoring_actions", row)
    assert sql.startswith("INSERT INTO main.s.model_monitoring_actions (action_id, model")
    assert "'owner''s call'" in sql and "DATE '2026-12-31'" in sql
    assert "NULL" in insert_statement("t", make("closed", "t1"))
