from __future__ import annotations

from typing import Any

import pytest

from bricksmith.mlops.selection import SelectionError, parse_config
from bricksmith.mlops.selection.config import CRITICAL, DEFAULT_STEPS, MULTIVARIATE, ClosingRange

BASE: dict[str, Any] = {"closing": "closing", "target": "y", "development": ["2026-01-31", "2026-06-30"]}


def config(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {**BASE, **extra}


def test_defaults_are_configuration_with_recorded_values() -> None:
    parsed = parse_config(config(out_of_time={"start": "2026-07-31", "end": "2026-09-30"}))
    assert parsed.development == ClosingRange("2026-01-31", "2026-06-30")
    assert parsed.out_of_time == ClosingRange("2026-07-31", "2026-09-30")
    assert dict(parsed.steps) == DEFAULT_STEPS
    assert not parsed.enabled(MULTIVARIATE)
    assert parsed.quality.max_missing == 0.95
    assert parsed.stability.psi_max == 0.25
    assert parsed.univariate.min_iv == 0.02
    assert parsed.redundancy.max_correlation == 0.8
    assert parsed.redundancy.max_vif == 10.0
    assert parse_config(parsed.to_dict()) == parsed
    assert parse_config(parsed.to_dict()).fingerprint() == parsed.fingerprint()


def test_thresholds_steps_signs_and_judgment_are_parsed() -> None:
    parsed = parse_config(
        config(
            steps={"multivariate": True, "redundancy": False},
            stability={"psi_max": "critical", "alpha": 0.01},
            univariate={"binning": "monotone", "min_ks": 0.1},
            expected_signs={"income": "-"},
            judgment={"keep": {"age": "regulatory"}, "drop": {"zip": "proxy for a protected attribute"}},
        )
    )
    assert parsed.enabled(MULTIVARIATE)
    assert not parsed.enabled("redundancy")
    assert parsed.stability.psi_max == CRITICAL
    assert parsed.univariate.binning == "monotone"
    assert parsed.expected_signs == {"income": "-"}
    assert parsed.judgment.keep == {"age": "regulatory"}


def test_candidates_exclude_the_reserved_columns() -> None:
    parsed = parse_config(config(exclude=["id"]))
    assert parsed.candidates(("id", "closing", "a", "y", "b")) == ("a", "b")
    declared = parse_config(config(features=["b"]))
    assert declared.candidates(("closing", "a", "b", "y")) == ("b",)
    with pytest.raises(SelectionError, match="features not in the input: c"):
        parse_config(config(features=["c"])).candidates(("a",))


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"thresholds": {}}, "unknown keys: thresholds"),
        ({"quality": {"max_mising": 0.5}}, "unknown quality keys: max_mising"),
        ({"steps": {"wrapper": True}}, "unknown step 'wrapper'"),
        ({"steps": {"quality": "yes"}}, "steps.quality must be true or false"),
        ({"development": ["2026-06-30", "2026-01-31"]}, "starts after it ends"),
        ({"development": "2026-01-31"}, "development must be"),
        ({"out_of_time": ["2026-06-30", "2026-07-31"]}, "overlap"),
        ({"target_kind": "multiclass"}, "target_kind must be one of"),
        ({"stability": {"psi_max": "auto"}}, "psi_max must be a number, null or 'critical'"),
        ({"stability": {"alpha": 1.5}}, "alpha must lie in"),
        ({"univariate": {"binning": "tree"}}, "binning must be one of"),
        ({"sample_rows": 0}, "at least 1"),
        ({"expected_signs": {"a": "up"}}, "must be '\\+' or '-'"),
        ({"judgment": {"keep": {"a": ""}}}, "judgment.keep.a needs a reason"),
        ({"judgment": {"keep": {"a": "x"}, "drop": {"a": "y"}}}, "keeps and drops the same variables: a"),
        ({"judgment": {"force": {}}}, "judgment must be a mapping"),
        ({"features": ["y"]}, "cannot be features: y"),
        ({"features": "a"}, "features must be a list"),
    ],
)
def test_invalid_configurations_are_refused(extra: dict[str, Any], message: str) -> None:
    with pytest.raises(SelectionError, match=message):
        parse_config(config(**extra))


def test_required_keys_are_named() -> None:
    with pytest.raises(SelectionError, match="target is required"):
        parse_config({"closing": "c", "development": ["a", "b"]})
