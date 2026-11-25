"""Invalid stability declarations fail at plan: unknown rule metric, non-list exclude, bad epsilon (synthetic)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.stability import parse_config

if TYPE_CHECKING:
    from pathlib import Path

BASE: dict[str, Any] = {"period": "month"}
LIMITS = {"green": 0.1, "red": 0.2}


@pytest.mark.parametrize("metric", ["psi_strict", "PSI", "mean_shfit"])
def test_an_unknown_inline_rule_metric_is_a_plan_error(metric: str) -> None:
    rules = [{"id": "r", "metric": metric, "limits": LIMITS}]
    with pytest.raises(MonitorError, match=rf"stability: rule 'r': unknown metric '{metric}'; known: psi, "):
        parse_config({**BASE, "rules": rules})


def test_an_unknown_rule_metric_in_a_rules_file_is_a_plan_error(tmp_path: Path) -> None:
    (tmp_path / "rules.json").write_text(json.dumps([{"id": "f", "metric": "psy", "limits": LIMITS}]))
    with pytest.raises(MonitorError, match="rule 'f': unknown metric 'psy'"):
        parse_config({**BASE, "rules": "rules.json"}, root=tmp_path)


def test_known_and_absent_rule_metrics_stay_valid() -> None:
    rules = [
        {"id": "a", "metric": "psi", "limits": LIMITS},
        {"id": "b", "metric": "revision_psi", "limits": LIMITS},
        {"id": "c", "informational_segments": ["s=x"]},
    ]
    assert [r.id for r in parse_config({**BASE, "rules": rules}).rules] == ["a", "b", "c"]


@pytest.mark.parametrize("exclude", ["sale_id", {"sale_id": True}, ("sale_id",), 3])
def test_exclude_must_be_a_list(exclude: object) -> None:
    with pytest.raises(MonitorError, match="stability: exclude must be a list of column names"):
        parse_config({**BASE, "exclude": exclude})


def test_exclude_list_is_kept() -> None:
    assert parse_config({**BASE, "exclude": ["sale_id", "x"]}).exclude == ("sale_id", "x")
    assert parse_config(BASE).exclude == ()


@pytest.mark.parametrize("epsilon", [0, 0.0, -1e-4, float("nan"), float("inf"), "-1", True, "abc", None])
def test_epsilon_must_be_finite_and_positive(epsilon: object) -> None:
    with pytest.raises(MonitorError, match="stability: epsilon must be a finite number > 0"):
        parse_config({**BASE, "epsilon": epsilon})


def test_valid_epsilon_is_kept() -> None:
    assert parse_config(BASE).epsilon == pytest.approx(1e-4)
    assert parse_config({**BASE, "epsilon": 1e-6}).epsilon == pytest.approx(1e-6)
    assert parse_config({**BASE, "epsilon": "0.001"}).epsilon == pytest.approx(1e-3)
