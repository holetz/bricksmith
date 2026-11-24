"""Limit rules on data stability: specificity, validity, informational segments, reported rule ids (synthetic)."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.stability import MemoryStore, check, parse_config
from bricksmith.monitor.stability.engine import judgment_fingerprint
from bricksmith.monitor.stability.tables import SCHEMAS, merge_rows

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

A, B = "2026-01", "2026-02"
LENIENT = {"green": 0.3, "red": 0.5}
STRICT = {"green": 0.05, "red": 0.1}


def rows_of(values: Mapping[str, Sequence[object]], segments: Sequence[str] = ()) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for period, xs in values.items():
        for x in xs:
            row: dict[str, object] = {"month": period, "x": x}
            if segments:
                row["s"] = segments[len(out) % len(segments)]
            out.append(row)
    return out


def config(**overrides: Any) -> Any:  # noqa: ANN401 - test helper
    base = {"period": "month", "columns": {"x": {"edges": [0, 1.5, 3]}}, "min_rows": 0, "baseline": "previous"}
    return parse_config({**base, **overrides})


def results_of(rows: list[dict[str, object]], cfg: Any) -> tuple[Any, dict[tuple[str, str, str, str], Any]]:  # noqa: ANN401
    found = check(rows, cfg, MemoryStore(), dataset="d", run_id="r1", now="2026-03-01T00:00:00+00:00")
    return found, {(r["period"], r["segment"], r["column"], r["metric"]): r for r in found.tables["results"]}


DRIFT = rows_of({A: [1, 1, 2, 2], B: [1, 2, 2, 2]})  # psi 0.2747 (red by default), mean shift 0.5


def psi(rules: list[dict[str, Any]]) -> Any:  # noqa: ANN401 - result row
    return results_of(DRIFT, config(rules=rules))[1][(B, "", "x", "psi")]


def test_a_rule_replaces_the_default_limits_and_is_reported() -> None:
    plain = psi([])
    assert plain["status"] == "red" and plain["rule_id"] == "" and plain["rule_version"] == 0
    ruled = psi([{"id": "lenient", "version": 2, "metric": "psi", "limits": LENIENT}])
    assert ruled["status"] == "green" and ruled["rule_id"] == "lenient" and ruled["rule_version"] == 2
    assert ruled["green_limit"] == 0.3 and ruled["red_limit"] == 0.5
    _, found = results_of(DRIFT, config(rules=[{"id": "lenient", "metric": "psi", "limits": LENIENT}]))
    assert found[(B, "", "x", "mean_shift")]["rule_id"] == ""


def test_the_most_specific_rule_wins() -> None:
    rules = [
        {"id": "by_column", "metric": "psi", "column": "x", "limits": LENIENT},
        {"id": "by_dataset", "metric": "psi", "dataset": "d", "limits": {"green": 0.1, "red": 0.5}},
    ]
    found = psi(rules)
    assert found["rule_id"] == "by_dataset" and found["status"] == "yellow"
    other = [{**rules[1], "dataset": "other"}, rules[0]]
    assert psi(other)["rule_id"] == "by_column"


def test_a_segment_rule_applies_to_its_segment_only() -> None:
    rows = rows_of({A: [1, 1, 2, 2], B: [2, 2, 1, 1]}, ["a", "a", "b", "b"])
    rule = {"id": "seg", "metric": "psi", "segment": "s=a", "limits": {"green": 100, "red": 200}}
    _, found = results_of(rows, config(segments=["s"], rules=[rule]))
    assert found[(B, "s=a", "x", "psi")]["status"] == "green" and found[(B, "s=a", "x", "psi")]["rule_id"] == "seg"
    assert found[(B, "s=b", "x", "psi")]["status"] == "red" and found[(B, "s=b", "x", "psi")]["rule_id"] == ""


def test_validity_dates_select_the_rule_by_period() -> None:
    expired = {"id": "old", "metric": "psi", "limits": LENIENT, "valid_to": "2026-01-31"}
    assert psi([expired])["rule_id"] == ""
    current = {"id": "now", "metric": "psi", "limits": LENIENT, "valid_from": "2026-02-01", "valid_to": "2026-02-28"}
    assert psi([expired, current])["rule_id"] == "now"
    undated = [{"id": "dated", "metric": "psi", "limits": LENIENT, "valid_from": "2026-01-01"}]
    rows = [{**r, "month": f"p-{r['month']}"} for r in DRIFT]
    assert results_of(rows, config(rules=undated))[1][("p-2026-02", "", "x", "psi")]["rule_id"] == ""


def test_null_limits_in_a_rule_disable_the_judgment() -> None:
    found = psi([{"id": "off", "metric": "psi", "limits": None}])
    assert found["status"] == "no_rule" and found["rule_id"] == "off" and found["green_limit"] is None


def test_informational_segments_are_computed_but_never_classified_or_gated() -> None:
    rows = rows_of({A: [1, 1, 2, 2], B: [2, 2, 1, 1]}, ["a", "a", "b", "b"])
    base = {"segments": ["s"], "gate": {"fail_on": ["red", "alert"]}, "persistence": {"immediate_red": True}}
    failing, _ = results_of(rows, config(**base))
    assert failing.outcome.failed
    info = {"id": "tests", "informational_segments": ["s=a", "s=b"]}
    passing, found = results_of(rows, config(**base, rules=[info]))
    assert not passing.outcome.failed and passing.outcome.red == 0 and passing.outcome.open_alerts == 0
    shown = found[(B, "s=a", "x", "psi")]
    assert shown["status"] == "informational" and shown["status_rank"] == -1 and shown["value"] > 1
    assert shown["rule_id"] == "tests" and shown["direction"] == "" and shown["green_limit"] is None
    assert shown["trend_status"] == ""
    assert found[(B, "", "x", "psi")]["status"] == "green" and found[(B, "", "x", "psi")]["rule_id"] == ""
    assert found[(B, "s=a", "", "row_count")]["status"] == "informational"
    assert not passing.tables["alerts"]


def test_controlled_segments_make_every_other_segment_informational() -> None:
    rows = rows_of({A: [1, 1, 2, 2], B: [2, 2, 1, 1]}, ["a", "a", "b", "b"])
    _, found = results_of(rows, config(segments=["s"], rules=[{"id": "only_a", "controlled_segments": ["s=a"]}]))
    assert found[(B, "s=a", "x", "psi")]["status"] == "red"
    assert found[(B, "s=b", "x", "psi")]["status"] == "informational"
    assert found[(B, "", "x", "psi")]["status"] == "green"


def test_without_rules_nothing_changes() -> None:
    plain = config()
    assert "rules" not in plain.to_dict()
    ruled = config(rules=[{"id": "r", "metric": "psi", "limits": STRICT}])
    assert ruled.to_dict()["rules"][0]["id"] == "r"
    assert judgment_fingerprint(plain) != judgment_fingerprint(ruled)
    assert judgment_fingerprint(plain) == judgment_fingerprint(config(rules=[]))
    assert plain.binning_fingerprint() == ruled.binning_fingerprint()


def test_rules_are_validated_by_the_declaration(tmp_path: Path) -> None:
    relative = {"id": "r", "metric": "psi", "limits": {"green": 0.1, "red": 0.2, "kind": "relative"}}
    with pytest.raises(MonitorError, match=r"stability: rule 'r'.*not supported"):
        parse_config({"period": "p", "rules": [relative]})
    with pytest.raises(MonitorError, match="unknown keys"):
        parse_config({"period": "p", "rules": [{"id": "r", "metric": "psi", "limits": LENIENT, "colour": 1}]})
    file = tmp_path / "rules.json"
    file.write_text(json.dumps({"rules": [{"id": "r", "metric": "psi", "limits": LENIENT}]}))
    assert config(rules=str(file)).rules == config(rules=[{"id": "r", "metric": "psi", "limits": LENIENT}]).rules


def test_results_table_carries_the_rule_columns() -> None:
    assert SCHEMAS["results"]["rule_id"] == "STRING" and SCHEMAS["results"]["rule_version"] == "INT"
    _, found = results_of(DRIFT, config(rules=[{"id": "r", "metric": "psi", "limits": LENIENT}]))
    merged = merge_rows("results", [], list(found.values()))
    assert {r["rule_id"] for r in merged} == {"", "r"}
