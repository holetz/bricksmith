"""Limit rules: parsing, files, specificity, validity and informational segments (synthetic)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import INFORMATIONAL, SUPPRESSED, rank
from bricksmith.monitor.rules import LimitRule, parse_rules, resolve

if TYPE_CHECKING:
    from pathlib import Path

LIMITS = {"green": 0.1, "red": 0.2}


def _rule(name: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401 - rule mapping
    return {"id": name, "metric": "psi", "limits": LIMITS, **extra}


def _pick(rules: tuple[LimitRule, ...], **key: Any) -> Any:  # noqa: ANN401 - selector keywords
    found = {"dataset": "sales", "column": "amount", "segment": "", "metric": "psi", "period_date": "2026-03-01"}
    return resolve(rules, **{**found, **key})


def test_specificity_weights_dataset_over_segment_over_column() -> None:
    rules = parse_rules(
        [_rule("col", column="amount"), _rule("seg", segment="channel=web"), _rule("ds", dataset="sales")]
    )
    assert _pick(rules, segment="channel=web").limits_rule.id == "ds"
    assert _pick(rules, segment="channel=web", dataset="other").limits_rule.id == "seg"
    assert _pick(rules, dataset="other").limits_rule.id == "col"
    assert _pick(rules, dataset="other", column="price").limits_rule is None


def test_scores_add_up() -> None:
    both = parse_rules([_rule("seg_col", segment="s", column="c"), _rule("ds", dataset="d")])
    assert {r.id: r.score for r in both} == {"seg_col": 3, "ds": 4}
    assert _pick(both, dataset="d", segment="s", column="c").limits_rule.id == "ds"


def test_ties_go_to_the_highest_version_then_the_smallest_id() -> None:
    rules = parse_rules([
        _rule("a", version=1, dataset="sales"), _rule("a", version=2, dataset="sales"),
        _rule("b", version=2, dataset="sales"),
    ])  # fmt: skip
    found = _pick(rules).limits_rule
    assert (found.id, found.version) == ("a", 2)


def test_other_selectors_do_not_match_and_wildcards_do() -> None:
    rules = parse_rules([_rule("only_sales", dataset="sales", metric="psi"), _rule("any", metric="mean")])
    assert _pick(rules, dataset="orders").limits_rule is None
    assert _pick(rules, metric="mean", dataset="orders").limits_rule.id == "any"
    assert _pick(rules).rule_id == "only_sales"


def test_validity_is_inclusive_and_needs_a_dated_period() -> None:
    rules = parse_rules([_rule("window", valid_from="2026-02-01", valid_to="2026-03-01")])
    assert _pick(rules, period_date="2026-02-01").rule_id == "window"
    assert _pick(rules, period_date="2026-03-01").rule_id == "window"
    assert _pick(rules, period_date="2026-03-02").rule_id == ""
    assert _pick(rules, period_date="2026-01-31").rule_id == ""
    assert _pick(rules, period_date=None).rule_id == ""
    assert _pick(parse_rules([_rule("open")]), period_date=None).rule_id == "open"


def test_expired_rule_falls_back_to_the_next_match() -> None:
    rules = parse_rules([_rule("old", dataset="sales", valid_to="2026-01-31"), _rule("base")])
    assert _pick(rules, period_date="2026-01-15").rule_id == "old"
    assert _pick(rules, period_date="2026-02-15").rule_id == "base"


def test_null_limits_disable_the_judgment() -> None:
    found = _pick(parse_rules([{"id": "off", "metric": "psi", "limits": None}])).limits_rule
    assert found is not None
    assert found.limits is None
    assert found.has_limits


def test_informational_segments_listed() -> None:
    rules = parse_rules([{"id": "info", "informational_segments": ["channel=test"]}])
    assert _pick(rules, segment="channel=test").informational
    assert _pick(rules, segment="channel=test").rule_id == "info"
    assert not _pick(rules, segment="channel=web").informational
    assert not _pick(rules, segment="").informational


def test_controlled_segments_listed_make_the_others_informational() -> None:
    rules = parse_rules([{"id": "ctl", "controlled_segments": ["channel=web"]}])
    assert not _pick(rules, segment="channel=web").informational
    assert _pick(rules, segment="channel=store").informational
    assert not _pick(rules, segment="").informational


def test_the_most_specific_informational_rule_overrides_entirely() -> None:
    rules = parse_rules([
        {"id": "broad", "informational_segments": ["channel=test"]},
        {"id": "narrow", "column": "amount", "informational_segments": ["channel=other"]},
    ])  # fmt: skip
    assert not _pick(rules, segment="channel=test").informational
    assert _pick(rules, segment="channel=other").informational
    assert _pick(rules, segment="channel=test", column="price").informational


def test_informational_is_a_suppressed_state_never_a_color() -> None:
    assert INFORMATIONAL in SUPPRESSED
    assert rank(INFORMATIONAL) == -1


@pytest.mark.parametrize(
    "bad",
    [
        [{"metric": "psi", "limits": LIMITS}],
        [{"id": "x", "limits": LIMITS}],
        [{"id": "x", "metric": "psi"}],
        [{"id": "x", "metric": "psi", "limits": LIMITS, "extra": 1}],
        [{"id": "x", "metric": "psi", "limits": LIMITS, "version": 0}],
        [{"id": "x", "metric": "psi", "limits": LIMITS, "valid_from": "soon"}],
        [{"id": "x", "metric": "psi", "limits": LIMITS, "valid_from": "2026-02-01", "valid_to": "2026-01-01"}],
        [{"id": "x", "informational_segments": ["a"], "controlled_segments": ["b"]}],
        [{"id": "x", "informational_segments": []}],
        [{"id": "x", "informational_segments": ["a"], "segment": "a"}],
        [{"id": "x", "metric": "psi", "limits": {"green": 0.1, "red": 0.2, "kind": "relative"}}],
        [_rule("x"), _rule("x")],
        "not a list",
    ],
)
def test_parsing_refuses_malformed_rules(bad: object) -> None:
    with pytest.raises(MonitorError):
        parse_rules(bad if not isinstance(bad, str) else {"id": bad})


def test_model_kinds_are_allowed_when_asked() -> None:
    relative = {
        "id": "r",
        "metric": "ks",
        "limits": {"green": -0.1, "red": -0.3, "direction": "lower", "kind": "relative"},
    }
    assert parse_rules([relative], kinds=("absolute", "relative", "control"))[0].limits.kind == "relative"


def test_files_load_to_the_same_rules_as_the_inline_list(tmp_path: Path) -> None:
    inline = [
        _rule("a", dataset="sales", version=2, valid_from="2026-01-01"),
        {"id": "info", "informational_segments": ["channel=test"]},
    ]
    as_json = tmp_path / "rules.json"
    as_json.write_text(json.dumps({"rules": inline}))
    as_list = tmp_path / "list.json"
    as_list.write_text(json.dumps(inline))
    as_toml = tmp_path / "rules.toml"
    as_toml.write_text(
        '[[rules]]\nid = "a"\nversion = 2\ndataset = "sales"\nmetric = "psi"\nvalid_from = "2026-01-01"\n'
        "limits = { green = 0.1, red = 0.2 }\n\n"
        '[[rules]]\nid = "info"\ninformational_segments = ["channel=test"]\n'
    )
    expected = parse_rules(inline)
    assert parse_rules(str(as_json)) == expected
    assert parse_rules(str(as_list)) == expected
    assert parse_rules(str(as_toml)) == expected


def test_a_missing_or_unknown_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(MonitorError, match="cannot be read"):
        parse_rules(str(tmp_path / "missing.json"))
    other = tmp_path / "rules.txt"
    other.write_text("x")
    with pytest.raises(MonitorError, match=r"\.toml or \.json"):
        parse_rules(str(other))


def test_to_dict_round_trips() -> None:
    rules = parse_rules([
        _rule("a", dataset="sales", version=3, valid_to="2026-12-31"),
        {"id": "info", "controlled_segments": ["x"]},
        {"id": "off", "metric": "mean", "limits": None},
    ])  # fmt: skip
    assert parse_rules([r.to_dict() for r in rules]) == rules
