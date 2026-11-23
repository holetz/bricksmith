from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from bricksmith.mlops.selection import (
    SelectionError,
    log_selection,
    select_features,
    table_rows,
    to_frame,
    write_options,
)
from bricksmith.mlops.selection.tables import LEDGER, SCHEMAS, VARIABLES

CONFIG: dict[str, Any] = {"name": "demo", "closing": "closing", "target": "y", "development": ["2026-01", "2026-02"],
                          "judgment": {"drop": {"weak": "not available at scoring time"}}}  # fmt: skip


def result() -> Any:  # noqa: ANN401
    rows = [
        {
            "closing": c,
            "y": i % 2,
            "strong": (i // 2) % 10 + 5 * (i % 2),
            "weak": (i // 2) % 4 + 2 * (i % 2),
            "kind": "ab"[i % 2],
        }
        for c in ("2026-01", "2026-02")
        for i in range(100)
    ]
    return select_features(rows, CONFIG)


class FakeSpark:
    def __init__(self) -> None:
        self.calls: list[tuple[list[tuple[Any, ...]], str]] = []

    def createDataFrame(self, rows: list[tuple[Any, ...]], ddl: str) -> str:  # noqa: N802 - the Spark API
        self.calls.append((rows, ddl))
        return "frame"


def test_table_rows_are_flat_typed_and_keyed() -> None:
    done = result()
    ledger = table_rows(done, LEDGER, run_id="r1", now="2026-11-23T00:00:00Z")
    assert len(ledger) == len(done.ledger)
    assert all(list(r) == list(SCHEMAS[LEDGER]) for r in ledger)
    assert {r["selection"] for r in ledger} == {"demo"}
    assert {r["config_fingerprint"] for r in ledger} == {done.config.fingerprint()}
    keys = {(r["variable"], r["step"], r["metric"]) for r in ledger}
    assert len(keys) == len(ledger)
    variables = table_rows(done, VARIABLES, run_id="r1", now="t")
    assert {r["variable"]: (r["selected"], r["left_at"]) for r in variables} == {
        "strong": (True, ""), "weak": (False, "judgment"), "kind": (True, "")
    }  # fmt: skip
    spark = FakeSpark()
    assert to_frame(spark, VARIABLES, variables) == "frame"  # pyright: ignore[reportArgumentType]
    rows, ddl = spark.calls[0]
    assert ddl.startswith("`selection` STRING, `run_id` STRING")
    assert "`selected` BOOLEAN" in ddl
    assert all(len(r) == len(SCHEMAS[VARIABLES]) for r in rows)
    assert write_options(LEDGER) == {"write": "merge", "keys": ["selection", "run_id", "variable", "step", "metric"],
                                     "on_empty": "allow_empty"}  # fmt: skip
    with pytest.raises(SelectionError, match="table kind must be one of"):
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
        self.artifacts[path] = data


def test_log_selection_writes_params_metrics_and_artifacts(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMlflow(active=True)
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    done = result()
    assert log_selection(done) is True
    assert fake.params["selection.univariate.min_iv"] == "0.02"
    assert fake.params["selection.judgment.drop.weak"] == "not available at scoring time"
    assert fake.params["selection.fingerprint"] == done.config.fingerprint()
    assert fake.metrics["selection.candidates"] == 3.0
    assert fake.metrics["selection.selected"] == 2.0
    assert fake.metrics["selection.dropped.judgment"] == 1.0
    assert set(fake.artifacts) == {"selection/ledger.json", "selection/variables.json", "selection/features.json",
                                   "selection/woe.json"}  # fmt: skip
    assert fake.artifacts["selection/features.json"] == {"features": ["strong", "kind"], "selection": "demo"}


def test_log_selection_does_nothing_without_an_active_run_or_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMlflow(active=False)
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    assert log_selection(result()) is False
    assert not fake.params
    monkeypatch.setitem(sys.modules, "mlflow", None)
    assert log_selection(result()) is False
