from __future__ import annotations

import sys
from typing import Any

import pytest

from bricksmith.mlops.selection import SelectionError, select_features
from bricksmith.mlops.selection.funnel import DROP, INFO, PASS


def synthetic() -> list[dict[str, Any]]:
    rows = []
    for closing in ("2026-01-31", "2026-02-28", "2026-03-31"):
        for i in range(300):
            y = i % 2
            rows.append({
                "closing": closing,
                "y": y,
                "signal": (i // 2) % 10 + 6 * y,
                "noise": ((i // 2) * 7) % 13,
                "kind": "a" if y else "b",
            })  # fmt: skip
    return rows


CONFIG: dict[str, Any] = {
    "closing": "closing",
    "target": "y",
    "development": ["2026-01-31", "2026-02-28"],
    "out_of_time": ["2026-03-31", "2026-03-31"],
    "steps": {"quality": False, "stability": False, "univariate": False, "redundancy": False, "multivariate": True},
    "multivariate": {"c": 0.05},
}


def test_the_penalized_model_and_permutation_importance_drop_the_noise() -> None:
    pytest.importorskip("sklearn")
    result = select_features(synthetic(), CONFIG)
    decisions = {(e.variable, e.metric): e for e in result.ledger}
    assert decisions["signal", "coefficient"].decision == PASS
    assert decisions["signal", "permutation_importance"].decision == PASS
    assert decisions["signal", "permutation_importance"].value > 0.1  # pyright: ignore[reportOptionalOperand]
    assert {decisions["noise", "coefficient"].decision, decisions["noise", "permutation_importance"].decision} >= {DROP}
    assert decisions["kind", "coefficient"].decision == INFO
    assert result.selected == ("signal", "kind")


def test_a_continuous_target_uses_an_elastic_net() -> None:
    pytest.importorskip("sklearn")
    rows = [{**r, "t": float(r["signal"]) * 2 + 1} for r in synthetic()]
    config = CONFIG | {"target": "t", "target_kind": "continuous", "exclude": ["y"], "multivariate": {"alpha": 0.5}}
    result = select_features(rows, config)
    assert "signal" in result.selected
    assert "noise" not in result.selected


def test_the_step_names_the_extra_when_scikit_learn_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sklearn.linear_model", None)
    with pytest.raises(SelectionError, match=r"needs the mlops extra"):
        select_features(synthetic(), CONFIG)
