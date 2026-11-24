"""The documentation artifact layout, its loggers and the model specification, offline."""

from __future__ import annotations

import math
import subprocess
import sys
import types
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.mlops.documentation import (
    PATHS,
    DocumentationError,
    DocumentInputs,
    Scaling,
    check_narrative,
    development_files,
    log_development,
    log_narrative,
    log_specification,
    model_specification,
    read_directory,
    write_directory,
)
from bricksmith.mlops.documentation.artifacts import COMPOSITION, DEVELOPMENT, NARRATIVE, SPECIFICATION
from bricksmith.mlops.stages import parse_development
from bricksmith.mlops.stages.rows import composition

if TYPE_CHECKING:
    from pathlib import Path

HEAVY = ("pyspark", "databricks", "mlflow", "sklearn", "pandas", "numpy", "pyarrow")
DEVELOPMENT_DECLARATION: dict[str, Any] = {
    "model": "attrition",
    "entity_keys": ["account_id"],
    "as_of": "2024-12-31",
    "samples": {
        "train": {"role": "train", "start": "2024-01-31", "end": "2024-02-29"},
        "oot": {"role": "out_of_time", "start": "2024-05-31", "end": "2024-05-31"},
    },
    "label": {"name": "left", "horizon": 3, "definition": "the account closes within the next three closings"},
    "exclusions": [{"name": "closed", "where": {"column": "status", "op": "eq", "value": "closed"}}],
}


def test_importing_the_package_loads_no_heavy_module() -> None:
    code = "\n".join(
        [
            "import sys, bricksmith.mlops.documentation",
            f"bad = [m for m in {HEAVY!r} if m in sys.modules]",
            "assert not bad, bad",
        ]
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize(
    ("narrative", "message"),
    [
        ({"purpose": "Rank accounts by the risk of leaving within 3 months"}, "contains a digit"),
        ({"purpose": " "}, "must be non-empty text"),
        ({"owner": "risk team"}, "unknown narrative fields: owner"),
    ],
)
def test_narrative_refusals(narrative: dict[str, Any], message: str) -> None:
    with pytest.raises(DocumentationError, match=message):
        check_narrative(narrative)


def test_narrative_keeps_declared_order_and_trims() -> None:
    assert check_narrative({"materiality": " moderate ", "purpose": "rank accounts"}) == {
        "purpose": "rank accounts",
        "materiality": "moderate",
    }


def test_layout_round_trips_through_a_directory(tmp_path: Path) -> None:
    development = parse_development(DEVELOPMENT_DECLARATION)
    population = [
        {"account_id": i, "closing_date": "2024-01-31", "status": "closed" if i % 4 == 0 else "open", "sample": "train"}
        for i in range(8)
    ]
    files = development_files(development, composition(population, development, by=("sample",)))
    assert files[DEVELOPMENT]["model"] == "attrition"
    assert files[COMPOSITION][0]["closing_date"] == "2024-01-31"
    inputs = DocumentInputs.from_files(
        {**files, NARRATIVE: {"purpose": "rank accounts"}, "selection/features.json": {"features": ["x"]}}, "run-1"
    )
    assert inputs.title == "attrition"
    written = write_directory(inputs, tmp_path)
    assert {p.relative_to(tmp_path).as_posix() for p in written} == {
        DEVELOPMENT,
        COMPOSITION,
        NARRATIVE,
        "selection/features.json",
    }
    again = read_directory(tmp_path, "run-1")
    assert again == inputs
    assert again.files() == inputs.files()
    assert list(again.files()) == [p for p in PATHS if p in again.files()]


def test_invalid_json_in_a_directory_is_refused(tmp_path: Path) -> None:
    (tmp_path / "documentation").mkdir()
    (tmp_path / SPECIFICATION).write_text("{", encoding="utf-8")
    with pytest.raises(DocumentationError, match="is not valid JSON"):
        read_directory(tmp_path, "run-1")


class Array:
    """Stands in for a numpy array: only ``tolist``."""

    def __init__(self, data: Any) -> None:  # noqa: ANN401
        self.data = data

    def tolist(self) -> Any:  # noqa: ANN401
        return self.data


class LogisticRegression:
    coef_ = Array([[-0.8, -1.2]])
    intercept_ = Array([-2.0])


class GradientBoosting:
    feature_importances_ = Array([0.2, 0.5, 0.3])


WOE = {
    "income": {"groups": [{"bins": ["bin_0"], "woe": -0.5}, {"bins": ["bin_1", "null"], "woe": 0.4}]},
    "tenure": {"groups": [{"bins": ["bin_0"], "woe": -0.3}, {"bins": ["bin_1"], "woe": 0.6}]},
}


def test_linear_specification_with_scorecard_points() -> None:
    scaling = {"pdo": 20, "base_score": 600, "base_odds": 50}
    spec = model_specification(LogisticRegression(), ["income_woe", "tenure_woe"], woe=WOE, scaling=scaling)
    assert spec["kind"] == "linear"
    assert spec["estimator"] == "LogisticRegression"
    assert spec["intercept"] == -2.0
    assert spec["coefficients"] == [{"coefficient": -0.8, "feature": "income_woe"},
                                    {"coefficient": -1.2, "feature": "tenure_woe"}]  # fmt: skip
    points = {(p["feature"], tuple(p["bins"])): p["points"] for p in spec["points"]}
    scale = Scaling(20, 600, 50)
    total = points["income_woe", ("bin_1", "null")] + points["tenure_woe", ("bin_0",)]
    logit = -2.0 + -0.8 * 0.4 + -1.2 * -0.3
    assert total == pytest.approx(scale.offset - scale.factor * logit)
    assert scale.factor == pytest.approx(20 / math.log(2))


def test_tree_and_other_specifications() -> None:
    spec = model_specification(GradientBoosting(), ["a", "b", "c"])
    assert spec["kind"] == "tree"
    assert [r["feature"] for r in spec["importance"]] == ["b", "c", "a"]
    assert model_specification(object(), ["a"]) == {"estimator": "object", "features": ["a"], "kind": "other"}


def test_specification_refusals() -> None:
    with pytest.raises(DocumentationError, match="2 coefficients for 1 features"):
        model_specification(LogisticRegression(), ["income_woe"])
    with pytest.raises(DocumentationError, match="no WoE table for model input 'other'"):
        model_specification(LogisticRegression(), ["income_woe", "other"], woe=WOE,
                            scaling={"pdo": 20, "base_score": 600, "base_odds": 50})  # fmt: skip
    with pytest.raises(DocumentationError, match="pdo > 0"):
        Scaling(0, 600, 50)


class FakeMlflow(types.ModuleType):
    def __init__(self, *, active: bool) -> None:
        super().__init__("mlflow")
        self.active = active
        self.artifacts: dict[str, Any] = {}

    def active_run(self) -> object | None:
        return object() if self.active else None

    def log_dict(self, data: Any, path: str) -> None:  # noqa: ANN401
        self.artifacts[path] = data


def test_loggers_write_the_layout_to_the_active_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMlflow(active=True)
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    development = parse_development(DEVELOPMENT_DECLARATION)
    assert log_development(development, []) is True
    assert log_narrative({"purpose": "rank accounts"}) is True
    assert log_specification(model_specification(GradientBoosting(), ["a", "b", "c"])) is True
    assert set(fake.artifacts) == {DEVELOPMENT, COMPOSITION, NARRATIVE, SPECIFICATION}
    with pytest.raises(DocumentationError, match="contains a digit"):
        log_narrative({"purpose": "rank in month 3"})
    monkeypatch.setitem(sys.modules, "mlflow", FakeMlflow(active=False))
    assert log_narrative({"purpose": "rank accounts"}) is False
    monkeypatch.setitem(sys.modules, "mlflow", None)
    assert log_specification({}) is False
