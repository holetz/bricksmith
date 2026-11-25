"""Downstream suppression, ownership on the rows, the severity score and routing of stability alerts."""
# ruff: noqa: PT018 - dense assertions and boolean declaration values

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import CRITICALITY_WEIGHTS, parse_config
from bricksmith.monitor.stability.incident import Upstream, build_incident, upstream_origin


def test_declaration_of_ownership_and_suppression() -> None:
    config = parse_config({"period": "month", "owner": " sales ", "domain": "orders", "criticality": "high"})
    assert (config.owner, config.domain, config.criticality, config.suppress_downstream) == (
        "sales", "orders", "high", True,
    )  # fmt: skip
    data = config.to_dict()
    assert data["owner"] == "sales" and data["criticality"] == "high" and "suppress_downstream" not in data
    assert parse_config({"period": "month", "suppress_downstream": False}).to_dict()["suppress_downstream"] is False
    assert CRITICALITY_WEIGHTS == {"low": 1, "medium": 2, "high": 3, "critical": 4}


def test_declaration_without_ownership_keeps_its_serialized_form() -> None:
    data = parse_config({"period": "month"}).to_dict()
    assert not {"owner", "domain", "criticality", "suppress_downstream"} & set(data)


@pytest.mark.parametrize(
    "bad",
    [{"criticality": "urgent"}, {"owner": 3}, {"domain": ["a"]}, {"suppress_downstream": "yes"}],
)
def test_declaration_rejects_bad_ownership(bad: dict[str, object]) -> None:
    with pytest.raises(MonitorError):
        parse_config({"period": "month", **bad})


def _alert(column: str, flagged: int = 1, severity: str = "warning") -> dict[str, object]:
    return {
        "alert_id": f"trend--{column}-psi", "segment": "", "column": column, "metric": "psi", "kind": "trend",
        "severity": severity, "state": "open", "flagged_periods": flagged,
    }  # fmt: skip


def _result(column: str, deviation: float) -> dict[str, object]:
    return {
        "period": "2026-04", "segment": "", "column": column, "metric": "psi", "value": 0.3, "red_limit": 0.25,
        "trend_deviation": deviation, "direction": "higher", "rule_id": "",
    }  # fmt: skip


def _score(criticality: str, flagged: int, columns: tuple[str, ...]) -> float:
    config = parse_config({"period": "month", "criticality": criticality})
    alerts = [_alert(c, flagged) for c in columns]
    incident = build_incident(
        alerts, [_result(c, 4.0) for c in columns], [], config, dataset="orders", period="2026-04"
    )
    assert incident is not None
    return incident.severity_score


def test_severity_score_multiplies_exceedance_persistence_criticality_and_columns() -> None:
    base = _score("low", 1, ("a",))
    assert base > 0
    assert _score("critical", 1, ("a",)) == pytest.approx(base * 4)
    assert _score("low", 3, ("a",)) == pytest.approx(base * 3)
    assert _score("low", 1, ("a", "b")) == pytest.approx(base * 2 * 2)  # twice the exceedance, twice the columns
    assert _score("medium", 1, ("a",)) > base


def test_incident_carries_its_upstream_label_and_score_in_the_summary() -> None:
    config = parse_config({"period": "month"})
    incident = build_incident(
        [_alert("a")], [_result("a", 4.0)], [], config, dataset="orders", period="2026-04", upstream="raw"
    )
    assert incident is not None and incident.upstream == "raw"
    data = incident.to_dict()
    assert data["upstream"] == "raw" and data["severity_score"] == incident.severity_score
    plain = build_incident([_alert("a")], [_result("a", 4.0)], [], config, dataset="orders", period="2026-04")
    assert plain is not None and "upstream" not in plain.to_dict()


def test_upstream_match_needs_the_same_period_and_picks_the_first_origin() -> None:
    records = [Upstream("b", "2026-04", "raw_b"), Upstream("a", "2026-04", "raw_a"), Upstream("c", "2026-03", "old")]
    assert upstream_origin(records, "2026-04") == "raw_a"
    assert upstream_origin(records, "2026-05") == ""
    assert upstream_origin([], "2026-04") == ""
