"""The Spark path of the evaluation, offline: the module loads Spark lazily and the engine dispatches to it.

The Spark functions themselves are proved against the row engine on the free context
(``tests/remote/test_model_evaluation.py``).
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.evaluation import engine, evaluate
from bricksmith.mlops.evaluation import spark as evaluation_spark
from bricksmith.monitor import adapter

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import pytest

HEAVY = ("pyspark", "databricks", "mlflow", "sklearn", "pandas", "numpy", "pyarrow")
CONFIG: dict[str, Any] = {"score": "p", "label": "y", "grade": "g", "grades": ["A", "B"]}


class Frame:
    """Stands in for a DataFrame: not a sequence, so the engine takes the Spark path."""

    def __init__(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rows = rows


def rows() -> list[dict[str, Any]]:
    return [
        {
            "sample": s,
            "closing_date": c,
            "p": (i % 10) / 10,
            "y": int(i % 10 > 6 - (s == "oot")),
            "g": "A" if i % 10 < 5 else "B",
        }
        for s in ("train", "oot")
        for c in ("2024-01-31", "2024-02-29")
        for i in range(40)
    ]


def test_spark_module_imports_no_heavy_module() -> None:
    code = "\n".join(
        [
            "import sys, bricksmith.mlops.evaluation.spark",
            f"bad = [m for m in {HEAVY!r} if m in sys.modules]",
            "assert not bad, bad",
        ]
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_a_dataframe_takes_the_spark_path_and_matches_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = evaluate(rows(), CONFIG).to_dict()
    calls: list[str] = []

    def samples(frame: Frame, column: str) -> list[str]:
        calls.append("samples")
        return engine._rows_samples(frame.rows, column)  # noqa: SLF001 - the row twin of the Spark read

    def subset(frame: Frame, column: str, name: str) -> Frame:
        calls.append("subset")
        return Frame(engine._rows_subset(frame.rows, column, name))  # noqa: SLF001

    def grade_counts(frame: Frame, config: Any) -> engine.Counts:  # noqa: ANN401 - the parsed config
        calls.append("grade_counts")
        return engine.rows_grade_counts(frame.rows, config)

    monkeypatch.setattr(evaluation_spark, "samples", samples)
    monkeypatch.setattr(evaluation_spark, "subset", subset)
    monkeypatch.setattr(evaluation_spark, "grade_counts", grade_counts)
    monkeypatch.setattr(
        engine, "fit_reference", lambda data, columns, **m: adapter.fit_reference(data.rows, columns, **m)
    )
    monkeypatch.setattr(engine, "measure", lambda reference, data: adapter.measure(reference, data.rows))
    via_spark = evaluate(Frame(rows()), CONFIG)
    assert {"samples", "subset", "grade_counts"} <= set(calls)
    assert via_spark.to_dict() == expected
