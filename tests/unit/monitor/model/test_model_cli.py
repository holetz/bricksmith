"""The model-monitoring command group: dashboard views and alerts replayed from stored rows."""
# ruff: noqa: PT018 - dense assertions on command output

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bricksmith.cli.main import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _row(run: str, value: float, light: str) -> dict[str, object]:
    return {"model": "m", "model_version": "1", "run_id": run, "monitoring_period": "2026-02", "segment": "",
            "column": "score", "metric": "psi", "family": "score_psi", "value": value, "light": light,
            "rows": 500, "reason": ""}  # fmt: skip


def _state(tmp_path: Path) -> Path:
    path = tmp_path / "state.json"
    runs = [{"run_id": "r1", "model": "m", "status": "success", "finished_at": "2026-03-01"},
            {"run_id": "r2", "model": "m", "status": "failed", "finished_at": "2026-03-02"}]  # fmt: skip
    path.write_text(json.dumps({"results": [_row("r1", 0.3, "red"), _row("r2", 0.01, "green")], "runs": runs}))
    return path


def test_views_command_prints_sql(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["model-monitoring", "views", "--scope", "main.monitoring"]) == 0
    text = capsys.readouterr().out
    assert "CREATE OR REPLACE VIEW main.monitoring.model_monitoring_latest" in text
    assert "main.monitoring.model_monitoring_open_alerts" in text
    assert main(["model-monitoring", "views", "--scope", "bad scope"]) != 0


def test_alerts_replay_stored_lights(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    state = _state(tmp_path)
    assert main(["model-monitoring", "alerts", "--state", str(state), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"] == 1 and [a["severity"] for a in payload["alerts"]] == ["critical"]
    assert main(["model-monitoring", "alerts", "--state", str(state), "--fail-on-red"]) == 4
    assert "critical: m psi score 2026-02" in capsys.readouterr().out
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"model": "m", "period": "month", "score": "score",
                                  "references": {"development": {"start": "a", "end": "b"}},
                                  "limits": {"psi": {"green": 0.4, "red": 0.6}}}))  # fmt: skip
    args = ["model-monitoring", "alerts", "--state", str(state), "--config", str(config), "--fail-on-red"]
    assert main(args) == 0 and "alerts: 0 from 1" in capsys.readouterr().out
    assert main(["model-monitoring", "alerts", "--state", str(state), "--fail-on-critical"]) == 0
    capsys.readouterr()
    critical = tmp_path / "critical.json"
    runs = [{"run_id": "r1", "model": "m", "status": "success", "finished_at": "2026-03-01"}]
    rows = [{**_row("r1", 0.3, "red"), "monitoring_period": p, "direction": "higher"} for p in ("2026-01", "2026-02")]
    critical.write_text(json.dumps({"results": rows, "runs": runs}))
    assert main(["model-monitoring", "alerts", "--state", str(critical), "--fail-on-critical"]) == 4
    assert "is persistently off: critical since 2026-02" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("[]")
    assert main(["model-monitoring", "alerts", "--state", str(bad)]) != 0
