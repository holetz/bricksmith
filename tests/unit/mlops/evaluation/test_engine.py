"""Offline tests for the model evaluation suite: the declaration, the row engine and the import surface."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import math
import subprocess
import sys
from datetime import date
from typing import Any

import pytest

from bricksmith.mlops.evaluation import EvaluationError, evaluate, parse_config
from bricksmith.mlops.evaluation.engine import grade_rows, rows_grade_counts
from bricksmith.mlops.stages import parse_development
from bricksmith.monitor import stats

HEAVY = ("pyspark", "databricks", "mlflow", "sklearn", "pandas", "numpy", "pyarrow")
CLOSINGS = {"train": (date(2024, 1, 31), date(2024, 2, 29)), "test": (date(2024, 2, 29),),
            "oot": (date(2024, 5, 31),)}  # fmt: skip


def grade_of(score: float) -> str:
    return "A" if score <= 0.2 else "B" if score <= 0.5 else "C"


def rows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sample, closings in CLOSINGS.items():
        shift = 0.1 if sample == "oot" else 0.0
        for closing in closings:
            for i in range(60):
                score = min(0.99, round((i % 20) / 20 + shift, 4))
                bad = int((i * 7) % 20 < score * 20)
                out.append({"sample": sample, "closing_date": closing, "score": score, "bad": bad,
                            "grade": grade_of(score), "income": float(i % 12), "region": "nsew"[i % 4]})  # fmt: skip
    return out


CONFIG: dict[str, Any] = {"name": "demo", "score": "score", "label": "bad", "grade": "grade",
                          "grades": ["A", "B", "C"], "features": ["income", "region"],
                          "categorical": ["region"]}  # fmt: skip


def test_importing_the_package_loads_no_heavy_module() -> None:
    code = "\n".join(
        [
            "import sys, bricksmith.mlops.evaluation, bricksmith.mlops.evaluation.engine",
            f"bad = [m for m in {HEAVY!r} if m in sys.modules]",
            "assert not bad, bad",
        ]
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"score": "1x"}, "score must be an identifier"),
        ({"direction": "up"}, "direction must be one of"),
        ({"grades": ["A"], "grade": None}, "grades need a grade column"),
        ({"categorical": ["other"]}, "categorical name columns that are not features"),
        ({"features": ["score"], "categorical": []}, "a column plays two roles: score"),
        ({"feature_bins": {"other": {}}}, "feature_bins name columns that are not features"),
        ({"bogus": 1}, "unknown keys: bogus"),
        ({"per_closing": "yes"}, "per_closing must be true or false"),
        ({"score_bins": 0}, "score_bins must be a whole number >= 1"),
    ],
)
def test_declaration_refusals(change: dict[str, Any], message: str) -> None:
    with pytest.raises(EvaluationError, match=message):
        parse_config({**CONFIG, **change})


def test_grade_order_comes_from_development_bands() -> None:
    development = parse_development(
        {
            "model": "m",
            "entity_keys": ["id"],
            "closing": "closing_date",
            "samples": {"live": {"role": "prediction", "start": "2024-01-31", "end": "2024-01-31"}},
            "bands": [{"name": "low", "upper": 0.1}, {"name": "high"}],
        }
    )
    config = parse_config({"score": "score", "label": "bad", "grade": "grade"}, development)
    assert config.grades == ("low", "high") and config.closing == "closing_date"


def test_missing_reference_sample_is_refused() -> None:
    with pytest.raises(EvaluationError, match="reference sample 'dev' has no rows"):
        evaluate(rows(), {**CONFIG, "reference": "dev"})


def test_discrimination_and_stability_match_the_shared_statistics() -> None:
    data = rows()
    result = evaluate(data, CONFIG)
    assert result.samples == ("train", "oot", "test")
    oot = [r for r in data if r["sample"] == "oot"]
    auc = stats.auc_rank([r["score"] for r in oot], [r["bad"] for r in oot])
    assert result.value("oot", "auc") == pytest.approx(auc)
    assert result.value("oot", "gini") == pytest.approx(2 * auc - 1)
    groups = stats.groups_from_lists([r["score"] for r in oot], [r["bad"] for r in oot])
    assert result.value("oot", "ks") == pytest.approx(stats.ks_groups(groups))
    assert result.value("train", "psi") == pytest.approx(0.0)
    assert (result.value("oot", "psi") or 0) > 0.01
    assert result.value("oot", "csi", "income") == pytest.approx(0.0)
    assert result.value("oot", "row_count") == 60
    brier = sum((r["score"] - r["bad"]) ** 2 for r in oot) / len(oot)
    assert result.value("oot", "brier") == pytest.approx(brier)
    assert result.value("oot", "grade_psi", "grade") is not None


def test_results_are_reported_per_closing() -> None:
    result = evaluate(rows(), CONFIG)
    closings = {r["closing"] for r in result.results if r["sample"] == "train" and r["metric"] == "auc"}
    assert closings == {"", "2024-01-31", "2024-02-29"}
    flat = evaluate(rows(), {**CONFIG, "per_closing": False})
    assert {r["closing"] for r in flat.results} == {""}


def test_score_distribution_rows_carry_reference_and_current_shares() -> None:
    result = evaluate(rows(), CONFIG)
    score = [r for r in result.distribution if r["sample"] == "oot" and r["closing"] == "" and r["metric"] == "psi"]
    assert score and math.isclose(sum(r["contribution"] for r in score), result.value("oot", "psi") or 0)
    assert {r["metric"] for r in result.distribution} == {"psi", "grade_psi"}


def test_grade_table_and_inversions_by_hand() -> None:
    config = parse_config({"score": "p", "label": "y", "grade": "g", "grades": ["A", "B", "C"], "per_closing": False})
    data = (
        [{"g": "A", "p": 0.1, "y": y} for y in (0, 0, 0, 1)]
        + [{"g": "B", "p": 0.2, "y": y} for y in (0, 0, 0, 0)]
        + [{"g": "C", "p": 0.5, "y": y} for y in (1, 1, 0, 0)]
        + [{"g": "C", "p": 0.5, "y": None}]
    )
    table, results = grade_rows(rows_grade_counts(data, config), config, "s")
    by_grade = {r["grade"]: r for r in table}
    assert by_grade["A"]["rows"] == 4 and by_grade["A"]["events"] == 1
    assert by_grade["A"]["observed_rate"] == pytest.approx(0.25)
    assert by_grade["A"]["expected_rate"] == pytest.approx(0.1)
    assert by_grade["A"]["observed_expected"] == pytest.approx(2.5)
    assert by_grade["A"]["brier"] == pytest.approx((3 * 0.01 + 0.81) / 4)
    assert by_grade["B"]["inversion"] is True and by_grade["C"]["inversion"] is False
    assert by_grade["C"]["rows"] == 4
    assert results == [{"sample": "s", "closing": "", "metric": "inversions", "subject": "g", "value": 1.0,
                        "count": 12, "status": "ok", "reason": ""}]  # fmt: skip
    decreasing = parse_config({"score": "p", "label": "y", "grade": "g", "grades": ["A", "B", "C"],
                               "direction": "decreasing", "per_closing": False})  # fmt: skip
    _, flipped = grade_rows(rows_grade_counts(data, decreasing), decreasing, "s")
    assert flipped[0]["value"] == 1.0


def test_invalid_probability_makes_expected_and_brier_unavailable() -> None:
    config = parse_config({"score": "points", "label": "y", "grade": "g", "grades": ["A"], "per_closing": False})
    table, _ = grade_rows(rows_grade_counts([{"g": "A", "points": 640, "y": 1}], config), config, "s")
    assert table[0]["expected_rate"] is None and table[0]["brier"] is None and table[0]["observed_rate"] == 1.0


def test_separate_probability_column_drives_calibration() -> None:
    data = [{**r, "points": 600 - 100 * r["score"]} for r in rows()]
    result = evaluate(data, {**CONFIG, "score": "points", "probability": "score"})
    oot = [r for r in data if r["sample"] == "oot"]
    brier = sum((r["score"] - r["bad"]) ** 2 for r in oot) / len(oot)
    assert result.value("oot", "brier") == pytest.approx(brier)
    assert result.value("oot", "auc") == pytest.approx(
        1 - stats.auc_rank([r["score"] for r in oot], [r["bad"] for r in oot])
    )


def test_result_dict_and_metrics_are_plain() -> None:
    result = evaluate(rows(), CONFIG)
    data = result.to_dict()
    assert data["name"] == "demo" and data["fingerprint"] == result.config.fingerprint()
    metrics = result.metrics()
    assert "oot.auc" in metrics and "oot.csi.income" in metrics and "oot.inversions" in metrics
