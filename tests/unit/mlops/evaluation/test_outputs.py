"""The evaluation tables and MLflow logging, offline."""

from __future__ import annotations

import json
import sys
import types
from typing import Any

import pytest

from bricksmith.mlops.evaluation import EvaluationError, evaluate, log_evaluation, table_rows, to_frame, write_options
from bricksmith.mlops.evaluation.tables import DISTRIBUTION, GRADES, KEYS, RESULTS, SCHEMAS

CONFIG: dict[str, Any] = {"name": "demo", "score": "p", "label": "y", "grade": "g", "grades": ["A", "B"],
                          "features": ["x"]}  # fmt: skip


def result() -> Any:  # noqa: ANN401
    rows = [
        {
            "sample": s,
            "closing_date": c,
            "p": (i % 10) / 10,
            "y": int(i % 10 > 6),
            "g": "A" if i % 10 < 5 else "B",
            "x": float(i % 7),
        }
        for s in ("train", "oot")
        for c in ("2024-01-31", "2024-02-29")
        for i in range(40)
    ]
    return evaluate(rows, CONFIG)


class FakeSpark:
    def __init__(self) -> None:
        self.calls: list[tuple[list[tuple[Any, ...]], str]] = []

    def createDataFrame(self, rows: list[tuple[Any, ...]], ddl: str) -> str:  # noqa: N802 - the Spark API
        self.calls.append((rows, ddl))
        return "frame"


@pytest.mark.parametrize("kind", [RESULTS, DISTRIBUTION, GRADES])
def test_table_rows_are_flat_typed_and_keyed(kind: str) -> None:
    done = result()
    rows = table_rows(done, kind, run_id="r1", now="2026-11-23T00:00:00Z")
    assert rows
    assert all(list(r) == list(SCHEMAS[kind]) for r in rows)
    assert {(r["evaluation"], r["run_id"], r["config_fingerprint"]) for r in rows} == {
        ("demo", "r1", done.config.fingerprint())
    }
    keys = {tuple(r[k] for k in KEYS[kind]) for r in rows}
    assert len(keys) == len(rows)
    spark = FakeSpark()
    assert to_frame(spark, kind, rows) == "frame"  # pyright: ignore[reportArgumentType]
    typed, ddl = spark.calls[0]
    assert ddl.startswith("`evaluation` STRING, `run_id` STRING")
    assert all(len(r) == len(SCHEMAS[kind]) for r in typed)
    assert write_options(kind) == {"write": "merge", "keys": list(KEYS[kind]), "on_empty": "allow_empty"}


def test_grade_rows_are_cast_for_spark() -> None:
    rows = table_rows(result(), GRADES, run_id="r1", now="t")
    spark = FakeSpark()
    to_frame(spark, GRADES, rows)  # pyright: ignore[reportArgumentType]
    first = dict(zip(SCHEMAS[GRADES], spark.calls[0][0][0], strict=True))
    assert isinstance(first["rows"], int)
    assert isinstance(first["inversion"], bool)
    assert "`grade_order` INT" in spark.calls[0][1]


def test_unknown_table_kind_is_refused() -> None:
    with pytest.raises(EvaluationError, match="table kind must be one of"):
        write_options("alerts")


class FakeMlflow(types.ModuleType):
    def __init__(self, *, active: bool) -> None:
        super().__init__("mlflow")
        self.active = active
        self.params: dict[str, str] = {}
        self.metrics: dict[str, float] = {}
        self.artifacts: dict[str, Any] = {}

    def active_run(self) -> object | None:
        return object() if self.active else None

    def log_params(self, params: dict[str, str]) -> None:
        self.params.update(params)

    def log_metrics(self, metrics: dict[str, float]) -> None:
        self.metrics.update(metrics)

    def log_dict(self, data: Any, path: str) -> None:  # noqa: ANN401
        json.dumps(data)
        self.artifacts[path] = data


def test_log_evaluation_writes_params_metrics_and_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMlflow(active=True)
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    done = result()
    assert log_evaluation(done) is True
    assert fake.params["evaluation.score"] == "p"
    assert fake.params["evaluation.fingerprint"] == done.config.fingerprint()
    assert fake.metrics["evaluation.oot.auc"] == done.value("oot", "auc")
    assert "evaluation.oot.csi.x" in fake.metrics
    assert set(fake.artifacts) == {f"evaluation/{n}" for n in ("results.json", "distribution.json", "grades.json",
                                                               "evaluation.json")}  # fmt: skip
    assert fake.artifacts["evaluation/evaluation.json"]["samples"] == ["train", "oot"]


def test_log_evaluation_does_nothing_without_an_active_run_or_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMlflow(active=False)
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    assert log_evaluation(result()) is False
    assert not fake.params
    monkeypatch.setitem(sys.modules, "mlflow", None)
    assert log_evaluation(result()) is False
