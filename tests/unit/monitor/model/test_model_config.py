"""The ``model_monitoring`` declaration: required roles, references, families, limits and specific errors."""
# ruff: noqa: PT018 - dense assertions on parsed declarations

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import Limits
from bricksmith.monitor.model import FAMILIES, parse_config, parse_declarations
from bricksmith.monitor.model.config import Lag
from bricksmith.monitor.persistence import PersistencePolicy
from bricksmith.monitor.trend import TrendPolicy

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
    assert config.limits_for("calibration_ratio") == Limits(0.3, 0.5, "both", center=1.0)
    assert config.trend == TrendPolicy() and config.trend_metrics == ("psi", "ks", "auc", "calibration_ratio")
    assert config.persistence == PersistencePolicy() and config.control_min_points == 3
    assert config.control_metrics() == frozenset()


def test_limit_kinds_trend_and_persistence() -> None:
    config = parse_config({
        **BASE,
        "limits": {
            "ks": {"kind": "relative", "green": -0.15, "red": -0.30, "direction": "lower"},
            "auc": {"kind": "relative", "green": -0.05, "red": -0.10, "direction": "lower", "reference": "out_of_time"},
            "gini": {"kind": "control", "green": 2, "red": 3, "direction": "both"},
        },
        "trend": {"window": 6, "min_points": 4}, "trend_metrics": ["ks"], "control_min_points": 4,
        "persistence": {"yellow": 2, "immediate_red": True}, "gate": {"fail_on": ["critical", "alert"]},
    })  # fmt: skip
    assert config.limits_for("ks") == Limits(-0.15, -0.30, "lower", kind="relative", reference="development")
    assert config.limits_for("auc").reference == "out_of_time"  # type: ignore[union-attr]
    assert config.control_metrics() == frozenset({"gini"}) and config.control_min_points == 4
    assert config.trend == TrendPolicy(window=6, min_points=4) and config.trend_metrics == ("ks",)
    assert config.persistence == PersistencePolicy(yellow=2, immediate_red=True)
    assert config.gate.fail_on == ("critical", "alert")
    data = config.to_dict()
    assert data["limits"]["ks"]["kind"] == "relative" and data["trend"]["window"] == 6
    assert data["persistence"]["yellow"] == 2 and data["control_min_points"] == 4
    assert parse_config({**BASE, "trend": None}).trend is None


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
        ({"gate": {"fail_on": ["purple"]}}, "gate fail_on takes red, yellow, critical, alert"),
        ({"limits": {"ks": {"green": 1, "red": 2, "kind": "relative", "reference": "later"}}}, "reference must be"),
        (
            {
                "references": {"development": {"sample": "dev"}},
                "limits": {"ks": {"green": 1, "red": 2, "kind": "relative", "reference": "out_of_time"}},
            },
            "declare that reference",
        ),
        ({"limits": {"ks": {"green": 2, "red": 3, "kind": "chart"}}}, "kind must be"),
        ({"trend_metrics": ["wobble"]}, "trend_metrics must be"),
        ({"trend": {"window": 2}}, "trend needs"),
        ({"persistence": {"close_after": 0}}, "close_after"),
        ({"control_min_points": 1}, "control_min_points must be an integer >= 2"),
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


def test_lags_resolve_metric_then_family_then_label_lag() -> None:
    config = parse_config({
        **BASE, "label": "churned", "label_lag": 2,
        "lags": {"auc": {"months": 12}, "discrimination": 3, "volumetry": {"periods": 1}},
    })  # fmt: skip
    assert config.lag_for("discrimination", "auc") == Lag(12, "months")
    assert config.lag_for("discrimination", "ks") == Lag(3, "periods")
    assert config.lag_for("calibration", "brier") == Lag(2, "periods")
    assert config.lag_for("volumetry", "row_count") == Lag(1, "periods")
    assert config.lag_for("score_psi", "psi") == Lag(0, "periods")
    assert config.to_dict()["lags"]["auc"] == {"amount": 12, "unit": "months"}
    assert parse_config(BASE).lags == {} and parse_config(BASE).to_dict()["lags"] == {}


@pytest.mark.parametrize(
    ("lags", "message"),
    [
        ({"wobble": 1}, "unknown metric or family 'wobble'"),
        ({"auc": -1}, "a lag must be an integer >= 0"),
        ({"auc": True}, "a lag must be an integer >= 0"),
        ({"auc": {"weeks": 2}}, "one of periods or months"),
        ({"auc": {"months": 1, "periods": 1}}, "one of periods or months"),
        ({"auc": {"months": "x"}}, "a lag must be an integer >= 0"),
        (["auc"], "lags must map"),
    ],
)
def test_bad_lags_are_refused(lags: object, message: str) -> None:
    with pytest.raises(MonitorError, match=message):
        parse_config({**BASE, "lags": lags})


def test_lags_never_trigger_a_refit() -> None:
    assert parse_config(BASE).binning_fingerprint() == parse_config({**BASE, "lags": {"auc": 1}}).binning_fingerprint()


CREDIT_BASE = {**BASE, "label": "bad", "grade": "grade"}


def test_credit_families_are_opt_in() -> None:
    config = parse_config(CREDIT_BASE)
    assert config.credit is None and config.metrics == FAMILIES
    assert "credit" not in config.to_dict() and config.limits_for("grade_oe") is None
    assert config.control_metrics() == frozenset()


def test_credit_block_enables_the_families_that_apply() -> None:
    from bricksmith.monitor.model.config import ALL_FAMILIES, Credit, Horizon  # noqa: PLC0415

    config = parse_config({
        **CREDIT_BASE,
        "credit": {
            "horizon": 12, "grades": ["A", "B", "C"], "min_expected_events": 3, "correlation": 0.12,
            "early_warning": [{"label": "bad_3", "periods": 3}, {"label": "bad_6", "periods": 6, "lag": {"months": 7}}],
            "exclusion": "exclusion_first",
        },
    })  # fmt: skip
    assert config.metrics == ALL_FAMILIES and config.families()[-5:] == (
        "grade_calibration", "grade_order", "early_warning", "exclusion_mix", "bad_rate_chart",
    )  # fmt: skip
    credit = config.credit_options
    assert credit == Credit(
        horizon=12, grades=("A", "B", "C"), min_expected_events=3.0, correlation=0.12,
        early_warning=(Horizon("bad_3", 3, Lag(3)), Horizon("bad_6", 6, Lag(7, "months"))),
        exclusion="exclusion_first",
    )  # fmt: skip
    assert credit.exponent(3) == 0.25 and credit.calibration_exponent == 1.0
    assert config.lag_for("early_warning", "early_ks", "bad_6") == Lag(7, "months")
    assert config.lag_for("early_warning", "early_ks", "bad_3") == Lag(3)
    assert config.lag_for("grade_calibration", "grade_oe") == Lag(0)
    assert config.limits_for("grade_jeffreys_p") == Limits(0.05, 0.01, "lower")
    assert config.limits_for("grade_inversions") == Limits(0, 1)
    assert config.control_metrics() == frozenset({"bad_rate"})
    assert config.to_dict()["credit"]["early_warning"][1]["lag"] == {"amount": 7, "unit": "months"}


def test_credit_families_need_their_roles() -> None:
    config = parse_config({**BASE, "credit": {}})
    assert config.families() == ("score_psi", "volumetry")
    listed = parse_config({**CREDIT_BASE, "metrics": ["grade_calibration", "bad_rate_chart"]})
    assert listed.families() == ("grade_calibration", "bad_rate_chart") and listed.credit is None
    assert listed.credit_options.min_expected_events == 5.0 and listed.limits_for("grade_oe") is not None


@pytest.mark.parametrize(
    ("credit", "message"),
    [
        ({"early_warning": [{"label": "bad_3", "periods": 3}]}, "declare the probability horizon"),
        ({"horizon": 12, "early_warning": [{"label": "b", "periods": 3}, {"label": "b", "periods": 6}]}, "distinct"),
        ({"horizon": 0}, "horizon must be an integer >= 1"),
        ({"correlation": 1.0}, "strictly between 0 and 1"),
        ({"grades": ["A", "A"]}, "distinct grades"),
        ({"min_expected_events": -1}, "min_expected_events must be a number"),
        ({"horizon": 12, "early_warning": [{"label": "b"}]}, "label, periods"),
        ({"surprise": 1}, "credit must be a mapping"),
    ],
)
def test_credit_errors(credit: dict, message: str) -> None:
    with pytest.raises(MonitorError, match=message):
        parse_config({**CREDIT_BASE, "credit": credit})
