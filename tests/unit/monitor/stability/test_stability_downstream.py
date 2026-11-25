"""Downstream suppression, ownership on the rows, the severity score and routing of stability alerts."""
# ruff: noqa: PT018 - dense assertions and boolean declaration values

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import CRITICALITY_WEIGHTS, parse_config
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.incident import Upstream, build_incident, upstream_origin
from bricksmith.monitor.stability.tables import SCHEMAS, merge_rows, view_statements


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


def _wide_rows(months: int, broken: int) -> list[dict[str, object]]:
    rows = []
    for m in range(months):
        for i in range(60):
            row: dict[str, object] = {"month": f"2026-{m + 1:02d}"}
            for c in range(6):
                row[f"c{c}"] = float(i % 20 + (80 if m >= 2 and c < broken else 0))
            rows.append(row)
    return rows


def _run(
    dataset: str, rows: list[dict[str, object]], upstream: list[Upstream] | None = None, **declared: object
) -> CheckResult:
    config = parse_config({"period": "month", "persistence": {"immediate_red": True}, "notify": True, **declared})
    return check(rows, config, MemoryStore(), dataset=dataset, run_id="r1", upstream=upstream or [])


def test_a_broken_source_notifies_and_its_downstream_is_labeled_and_silent() -> None:
    source = _run("raw", _wide_rows(4, 3), owner="ingest", domain="sales", criticality="high")
    assert source.incident is not None and source.notifications and source.outcome.red
    assert source.outcome.upstream == "" and all(e.details["owner"] == "ingest" for e in source.notifications)
    assert {e.details["criticality"] for e in source.notifications} == {"high"}
    seen = [Upstream("raw", source.incident.period, "raw")]
    derived = _run("orders", _wide_rows(4, 3), seen, owner="sales-team")
    assert derived.incident is not None and derived.incident.upstream == "raw"
    assert derived.notifications == [] and derived.outcome.silenced > 0 and derived.outcome.upstream == "raw"
    assert "caused upstream by raw" in derived.outcome.message and derived.outcome.to_dict()["upstream"] == "raw"
    opened = [r for r in derived.tables["alerts"] if r["state"] == "open"]
    assert opened and {r["upstream"] for r in opened} == {"raw"} and derived.tables["results"]  # stored, never dropped
    assert derived.tables["runs"][0]["upstream"] == "raw" and derived.tables["runs"][0]["owner"] == "sales-team"


def test_downstream_notifies_when_suppression_is_off_or_the_period_differs() -> None:
    seen = [Upstream("raw", "2026-04", "raw")]
    kept = _run("orders", _wide_rows(4, 3), seen, suppress_downstream=False)
    assert kept.notifications and kept.incident is not None and kept.incident.upstream == "raw"
    assert all(e.details["upstream"] == "raw" for e in kept.notifications) and kept.outcome.silenced == 0
    other = _run("orders", _wide_rows(4, 3), [Upstream("raw", "2026-03", "raw")])
    assert other.notifications and other.outcome.upstream == "" and other.incident is not None
    assert other.incident.upstream == ""


def test_a_downstream_digest_is_silenced_too_and_a_healthy_table_is_never_labeled() -> None:
    seen = [Upstream("raw", "2026-04", "raw")]
    assert _run("orders", _wide_rows(4, 3), seen, notify={"digest": True}).notifications == []
    healthy = _run("orders", _wide_rows(4, 0), seen)
    assert healthy.incident is None and healthy.outcome.upstream == "" and healthy.tables["runs"][0]["upstream"] == ""


def test_run_rows_carry_ownership_and_the_severity_score_and_the_view_exposes_it() -> None:
    result = _run("raw", _wide_rows(4, 3), owner="ingest", domain="sales", criticality="critical")
    row = result.tables["runs"][0]
    assert (row["owner"], row["domain"], row["criticality"]) == ("ingest", "sales", "critical")
    assert result.incident is not None and row["severity_score"] == result.incident.severity_score > 0
    stored = merge_rows("runs", [], [row])
    assert set(stored[0]) == set(SCHEMAS["runs"]) and "upstream" in SCHEMAS["alerts"]
    assert any("stability_datasets" in v and "criticality" in v for v in view_statements("main.s"))
