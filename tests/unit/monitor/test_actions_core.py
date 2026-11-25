"""The shared action core: any entity column, typed resolutions, and the model wrapper keeps its rows."""
# ruff: noqa: PT018 - dense assertions on alert rows

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.monitor import actions as core
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.actions import action_row as model_row

T1 = "2026-01-01T00:00:00+00:00"
RES = core.RESOLUTIONS


def row(action: str = "closed", **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    kwargs: dict[str, Any] = {"entity": "dataset", "name": "d", "alert_id": "a1", "action": action, "reason": "why",
                              "author": "ana", "created_at": T1, "resolutions": RES, **extra}  # fmt: skip
    return core.action_row(**kwargs)


def test_entity_column_names_the_row_and_the_id_is_shared() -> None:
    stability = row("analysis")
    model = model_row(model="d", alert_id="a1", action="analysis", reason="why", author="ana", created_at=T1)
    assert "dataset" in stability and "model" not in stability and "resolution" not in model
    assert stability["action_id"] == model["action_id"]


def test_resolutions_belong_to_closure_only() -> None:
    assert row(resolution="expected")["resolution"] == "expected"
    with pytest.raises(MonitorError, match="requires a resolution"):
        row()
    with pytest.raises(MonitorError, match="takes no resolution"):
        row("analysis", resolution="fixed")
    with pytest.raises(MonitorError, match="takes no resolution"):
        core.action_row(entity="model", name="m", alert_id="a", action="closed", reason="r", author="a",
                        created_at=T1, resolution="fixed")  # fmt: skip


def test_apply_folds_by_entity_with_resolution() -> None:
    actions = [row(resolution="false_positive")]
    alerts = [{"dataset": "d", "alert_id": "a1", "state": "open"}, {"dataset": "e", "alert_id": "a1", "state": "open"}]
    out = core.apply_actions(alerts, actions, "2026-02-01", "dataset", resolution=True)
    assert out[0]["state"] == "closed" and out[0]["resolution"] == "false_positive"
    assert out[1]["state"] == "open" and out[1]["resolution"] is None


def test_acceptance_ends_after_valid_until() -> None:
    accepted = row("risk_accepted", valid_until="2026-03-01")
    alerts = [{"dataset": "d", "alert_id": "a1", "state": "open"}]
    assert core.apply_actions(alerts, [accepted], "2026-03-01", "dataset")[0]["state"] == "accepted"
    assert core.apply_actions(alerts, [accepted], "2026-03-02", "dataset")[0]["state"] == "open"
