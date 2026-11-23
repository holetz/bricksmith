"""The ``model_monitoring`` declaration: required roles, references, families, limits and specific errors."""
# ruff: noqa: PT018 - dense assertions on parsed declarations

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import Limits
from bricksmith.monitor.model import FAMILIES, parse_config, parse_declarations

BASE = {
    "model": "churn",
    "period": "month",
    "score": "score",
    "sample": "sample",
    "references": {"development": {"sample": "dev"}, "out_of_time": {"start": "2025-07", "end": "2025-09"}},
}


def test_minimal_declaration_has_defaults() -> None:
    config = parse_config(BASE)
    assert config.development.label == "sample=dev" and config.out_of_time is not None
    assert config.out_of_time.label == "2025-07..2025-09"
    assert config.metrics == FAMILIES and config.prefix == "model_monitoring_" and config.label_lag == 0
    assert config.families() == ("score_psi", "volumetry")  # no label, features, grade or critical fields
    assert config.limits_for("psi") == Limits(0.10, 0.25) and config.limits_for("ks") is None
    assert config.gate.fail_on == () and config.gate.on_error == "warn"


def test_full_declaration() -> None:
    config = parse_config({
        **BASE, "label": "churned", "grade": "grade", "segment": "region", "features": ["age", "plan"],
        "critical_fields": ["age"], "label_lag": 2, "model_version": "3",
        "limits": {"ks": {"green": 0.3, "red": 0.2, "direction": "lower"}, "psi": None},
        "gate": {"fail_on": "red", "on_error": "fail"}, "feature_binning": {"stability": "customers"},
    })  # fmt: skip
    assert config.families() == FAMILIES
    assert config.features == {"age": {}, "plan": {}} and config.stability_dataset == "customers"
    assert config.limits_for("psi") is None and config.limits_for("ks") == Limits(0.3, 0.2, "lower")
    assert config.gate.fail_on == ("red",) and config.model_version == "3"
    assert config.to_dict()["development"]["value"] == "dev"


def test_binning_fingerprint_ignores_judgment() -> None:
    a = parse_config(BASE)
    b = parse_config({**BASE, "limits": {"psi": {"green": 0.2, "red": 0.3}}, "label_lag": 3})
    c = parse_config({**BASE, "score_bins": 5})
    assert a.binning_fingerprint() == b.binning_fingerprint() != c.binning_fingerprint()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"colour": 1}, "unknown keys colour"),
        ({"model": ""}, "model must be a non-empty name"),
        ({"references": {"out_of_time": {"sample": "oot"}}}, "development (required)"),
        ({"references": {"development": {"start": "2025-03", "end": "2025-01"}}}, "start <= end"),
        ({"references": {"development": {"sample": "dev", "start": "2025-01", "end": "2025-02"}}}, "either sample"),
        ({"sample": None}, "declare the sample column"),
        ({"metrics": ["psi"]}, "metrics must be a non-empty list"),
        ({"limits": {"wobble": {"green": 1, "red": 2}}}, "unknown metric 'wobble'"),
        ({"gate": {"fail_on": ["critical"]}}, "gate fail_on takes red, yellow"),
        ({"label_lag": -1}, "label_lag must be an integer >= 0"),
        ({"model_version": "1", "model_version_column": "v"}, "not both"),
        ({"feature_binning": {"stability": ""}}, "feature_binning stability must be"),
        ({"feature_binning": "shared"}, "feature_binning must be 'own'"),
        ({"prefix": "bad-prefix"}, "prefix must be letters"),
        ({"critical_fields": "age"}, "critical_fields must be a list"),
    ],
)
def test_declaration_errors_are_specific(change: dict[str, object], message: str) -> None:
    with pytest.raises(MonitorError, match=message.replace("(", r"\(").replace(")", r"\)")):
        parse_config({**BASE, **change})


def test_missing_required_keys_and_duplicate_outputs() -> None:
    with pytest.raises(MonitorError, match="period, score required"):
        parse_config({"model": "m", "references": {"development": {"start": "a", "end": "b"}}})
    with pytest.raises(MonitorError, match="must be a mapping"):
        parse_config(["not", "a", "mapping"])
    assert len(parse_declarations([{**BASE, "output": "a"}, {**BASE, "output": "b"}])) == 2
    with pytest.raises(MonitorError, match="distinct output"):
        parse_declarations([BASE, BASE])
