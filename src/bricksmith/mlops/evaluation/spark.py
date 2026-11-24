"""The Spark reads of the evaluation suite: sample names, one sample's rows and grade counts, built-in functions only.

The metrics themselves come from the shared monitor core (``bricksmith.monitor.spark``) through the adapter; this
module only supplies what the evaluation adds. No Python UDF is used, so it runs on Free Edition.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.evaluation.engine import WHOLE
from bricksmith.monitor.measure import NULL_SEGMENT

if TYPE_CHECKING:
    from pyspark.sql import Column, DataFrame  # pyright: ignore[reportMissingImports]

    from bricksmith.mlops.evaluation.config import EvaluationConfig
    from bricksmith.mlops.evaluation.engine import Counts


def _f() -> Any:  # noqa: ANN401 - the pyspark functions module
    return importlib.import_module("pyspark.sql.functions")


def _text(column: str) -> Column:
    f = _f()
    return f.coalesce(f.col(column).cast("string"), f.lit(NULL_SEGMENT))


def samples(frame: DataFrame, column: str) -> list[str]:
    """Return the distinct non-null sample names, sorted."""
    f = _f()
    found = frame.filter(f.col(column).isNotNull()).select(f.col(column).cast("string").alias("s")).distinct()
    return sorted(str(r["s"]) for r in found.collect())


def subset(frame: DataFrame, column: str, name: str) -> DataFrame:
    """Return the rows of sample ``name``."""
    f = _f()
    return frame.filter(f.col(column).cast("string") == f.lit(name))


def grade_counts(frame: DataFrame, config: EvaluationConfig) -> Counts:
    """Return ``(closing, grade) -> [rows, events, probability sum, squared error, invalid]`` like the row engine."""
    f = _f()
    label = f.col(config.label).cast("double")
    probability = f.col(config.probability_column).cast("double")
    valid = probability.isNotNull() & ~f.isnan(probability) & (probability >= 0.0) & (probability <= 1.0)
    labelled = frame.filter(label.isin(0.0, 1.0))
    aggregates = [
        f.count(f.lit(1)).alias("n"),
        f.sum(label).alias("e"),
        f.sum(f.when(valid, probability).otherwise(0.0)).alias("p"),
        f.sum(f.when(valid, (probability - label) * (probability - label)).otherwise(0.0)).alias("q"),
        f.sum(f.when(valid, 0).otherwise(1)).alias("i"),
    ]
    groupings: list[tuple[Column, ...]] = [(f.lit(WHOLE).alias("c"),)]
    if config.per_closing:
        groupings.append((_text(config.closing).alias("c"),))
    counts: Counts = {}
    for closing in groupings:
        for row in labelled.groupBy(*closing, _text(config.grade).alias("g")).agg(*aggregates).collect():
            counts[str(row["c"]), str(row["g"])] = [
                float(row["n"]),
                float(row["e"] or 0.0),
                float(row["p"] or 0.0),
                float(row["q"] or 0.0),
                float(row["i"] or 0.0),
            ]
    return counts


__all__ = ["grade_counts", "samples", "subset"]
