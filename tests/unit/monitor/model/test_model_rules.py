"""Limit rules on model monitoring: selectors, validity, informational segments, control rules and replay."""
# ruff: noqa: PT018 - dense assertions on result rows

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.alerts import replay_alerts
from bricksmith.monitor.model.config import parse_config
from bricksmith.monitor.model.engine import MemoryStore, check
from bricksmith.monitor.model.tables import SCHEMAS
from tests.unit.monitor.model.test_model_engine import DECLARATION, pick, predictions

if TYPE_CHECKING:
    from pathlib import Path

LENIENT = {"green": 50.0, "red": 99.0}


def run(rows: list[dict[str, Any]], **changes: Any) -> Any:  # noqa: ANN401 - check result
    config = parse_config({**DECLARATION, **changes})
    return check(rows, config, MemoryStore(), run_id="r1", now="2026-10-01T00:00:00+00:00")


DRIFT = predictions(drift=True)


def psi_of(result: Any, period: str, segment: str = "") -> dict[str, Any]:  # noqa: ANN401 - check result
    return pick(result.tables["results"], metric="psi", segment=segment, monitoring_period=period)[0]


def test_a_rule_replaces_the_declared_limits_and_is_reported() -> None:
    plain = psi_of(run(DRIFT), "2025-09")
    assert plain["light"] == "red" and plain["rule_id"] == "" and plain["rule_version"] == 0
    rule = {"id": "lenient", "version": 3, "metric": "psi", "limits": LENIENT}
    ruled = psi_of(run(DRIFT, rules=[rule]), "2025-09")
    assert ruled["light"] == "green" and ruled["rule_id"] == "lenient" and ruled["rule_version"] == 3
    assert ruled["green_limit"] == 50.0 and ruled["limit_kind"] == "absolute"


def test_the_model_is_the_dataset_selector_and_specificity_decides() -> None:
    rules = [
        {"id": "by_column", "metric": "psi", "column": "score", "limits": LENIENT},
        {"id": "by_model", "metric": "psi", "dataset": "churn", "limits": {"green": 0.1, "red": 99.0}},
        {"id": "other_model", "metric": "psi", "dataset": "other", "limits": LENIENT},
    ]
    found = psi_of(run(DRIFT, rules=rules), "2025-09")
    assert found["rule_id"] == "by_model" and found["light"] == "yellow"


def test_a_segment_rule_applies_to_that_segment() -> None:
    rule = {"id": "north", "metric": "psi", "segment": "n", "limits": LENIENT}
    result = run(DRIFT, rules=[rule])
    assert psi_of(result, "2025-09", "n")["rule_id"] == "north" and psi_of(result, "2025-09", "n")["light"] == "green"
    assert psi_of(result, "2025-09", "s")["rule_id"] == "" and psi_of(result, "2025-09", "s")["light"] == "red"


def test_validity_dates_follow_the_monitoring_period() -> None:
    rule = {"id": "summer", "metric": "psi", "limits": LENIENT, "valid_to": "2025-08-31"}
    result = run(predictions(drifted=("2025-08", "2025-09")), rules=[rule])
    assert psi_of(result, "2025-08")["rule_id"] == "summer" and psi_of(result, "2025-08")["light"] == "green"
    assert psi_of(result, "2025-09")["rule_id"] == "" and psi_of(result, "2025-09")["light"] == "red"


def test_informational_segments_are_computed_never_classified_or_gated() -> None:
    rule = {"id": "north_info", "informational_segments": ["n", "s"]}
    gated = {"gate": {"fail_on": ["red"]}, "limits": {"psi": {"green": 0.1, "red": 0.25}}}
    plain = run(DRIFT, **gated)
    ruled = run(DRIFT, rules=[rule], **gated)
    segment = psi_of(ruled, "2025-09", "n")
    assert segment["light"] == "informational" and segment["light_rank"] == -1 and segment["value"] > 0.25
    assert segment["rule_id"] == "north_info" and segment["direction"] == "" and segment["green_limit"] is None
    assert psi_of(ruled, "2025-09")["light"] == "red"
    assert ruled.outcome.red < plain.outcome.red
    assert not [a for a in ruled.tables["alerts"] if a["segment"] in ("n", "s")]
    assert all(r["light"] == "informational" for r in pick(ruled.tables["results"], segment="n", metric="ks"))


def test_controlled_segments_make_the_others_informational() -> None:
    result = run(DRIFT, rules=[{"id": "only_n", "controlled_segments": ["n"]}])
    assert psi_of(result, "2025-09", "n")["light"] == "red"
    assert psi_of(result, "2025-09", "s")["light"] == "informational"
    assert psi_of(result, "2025-09")["light"] == "red"


def test_control_rules_get_their_baselines_measured() -> None:
    rule = {
        "id": "ks_chart",
        "metric": "ks",
        "limits": {"green": 2.0, "red": 3.0, "direction": "both", "kind": "control"},
    }
    config = parse_config({**DECLARATION, "rules": [rule]})
    assert "ks" in config.control_metrics()
    ks = pick(run(DRIFT, rules=[rule]).tables["results"], metric="ks", segment="", monitoring_period="2025-09")[0]
    assert ks["limit_kind"] == "control" and ks["baseline_points"] == 5 and ks["rule_id"] == "ks_chart"


def test_without_rules_rows_and_fingerprints_do_not_change() -> None:
    result = run(predictions())
    assert {r["rule_id"] for r in result.tables["results"]} == {""}
    assert {r["rule_version"] for r in result.tables["results"]} == {0}
    plain = parse_config(DECLARATION)
    ruled = parse_config({**DECLARATION, "rules": [{"id": "r", "metric": "psi", "limits": LENIENT}]})
    assert "rules" not in plain.to_dict() and ruled.to_dict()["rules"][0]["id"] == "r"
    assert plain.binning_fingerprint() == ruled.binning_fingerprint()
    assert SCHEMAS["results"]["rule_id"] == "STRING" and SCHEMAS["results"]["rule_version"] == "INT"


def test_replay_recolors_stored_rows_with_the_current_rules() -> None:
    result = run(DRIFT, limits={"psi": {"green": 0.1, "red": 0.25}})
    stored = result.tables["results"]
    runs = result.tables["runs"]
    rule = {"id": "north_info", "informational_segments": ["n"]}
    ruled = parse_config({**DECLARATION, "rules": [rule], "limits": {"psi": {"green": 0.1, "red": 0.25}}})
    rows, alerts = replay_alerts(stored, runs, config=ruled, created_at="now")
    north = pick(rows, metric="psi", segment="n", monitoring_period="2025-09")[0]
    assert north["light"] == "informational" and north["rule_id"] == "north_info"
    assert not [a for a in alerts if a["segment"] == "n" and a["metric"] == "psi"]
    undone = parse_config({**DECLARATION, "limits": {"psi": {"green": 0.1, "red": 0.25}}})
    again, _ = replay_alerts(rows, runs, config=undone, created_at="now")
    back = pick(again, metric="psi", segment="n", monitoring_period="2025-09")[0]
    assert back["light"] == "red" and back["rule_id"] == "" and back["reason"] == ""


def test_rules_are_validated_by_the_declaration(tmp_path: Path) -> None:
    with pytest.raises(MonitorError, match="unknown metric 'nope'"):
        parse_config({**DECLARATION, "rules": [{"id": "r", "metric": "nope", "limits": LENIENT}]})
    oot_rule = {"id": "r", "metric": "ks", "limits": {"green": -0.1, "red": -0.3, "direction": "lower",
                                                         "kind": "relative", "reference": "out_of_time"}}  # fmt: skip
    without_oot = {**DECLARATION, "references": {"development": {"sample": "dev"}}, "rules": [oot_rule]}
    with pytest.raises(MonitorError, match="out_of_time"):
        parse_config(without_oot)
    relative = {
        "id": "r",
        "metric": "ks",
        "limits": {"green": -0.1, "red": -0.3, "direction": "lower", "kind": "relative"},
    }
    found = parse_config({**DECLARATION, "rules": [relative]}).rules[0].limits
    assert found is not None and found.reference == "development"
    file = tmp_path / "rules.json"
    file.write_text(json.dumps({"rules": [{"id": "r", "metric": "psi", "limits": LENIENT}]}))
    assert parse_config({**DECLARATION, "rules": str(file)}).rules[0].id == "r"
