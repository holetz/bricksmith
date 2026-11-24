"""The evaluation tables: results, score and grade distributions, and grade tables, flat, typed and merge-keyed.

An ordinary task returns ``to_frame(spark, kind, table_rows(result, kind, run_id=..., now=...))`` with
``metadata=write_options(kind)``; reruns of the same evaluation and run id replace their own rows. Every column a
report filters on is a plain scalar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.mlops.evaluation.config import fail

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyspark.sql import DataFrame, SparkSession  # pyright: ignore[reportMissingImports]

    from bricksmith.mlops.evaluation.engine import EvaluationResult

RESULTS = "results"
DISTRIBUTION = "distribution"
GRADES = "grades"
KINDS = (RESULTS, DISTRIBUTION, GRADES)
_STAMP = {"evaluation": "STRING", "run_id": "STRING", "config_fingerprint": "STRING"}
SCHEMAS: dict[str, dict[str, str]] = {
    RESULTS: {
        **_STAMP, "sample": "STRING", "closing": "STRING", "metric": "STRING", "subject": "STRING",
        "value": "DOUBLE", "count": "BIGINT", "status": "STRING", "reason": "STRING", "computed_at": "STRING",
    },
    DISTRIBUTION: {
        **_STAMP, "sample": "STRING", "closing": "STRING", "metric": "STRING", "subject": "STRING", "bin": "STRING",
        "reference_share": "DOUBLE", "current_share": "DOUBLE", "contribution": "DOUBLE", "computed_at": "STRING",
    },
    GRADES: {
        **_STAMP, "sample": "STRING", "closing": "STRING", "grade": "STRING", "grade_order": "INT", "rows": "BIGINT",
        "events": "BIGINT", "observed_rate": "DOUBLE", "expected_rate": "DOUBLE", "observed_expected": "DOUBLE",
        "brier": "DOUBLE", "inversion": "BOOLEAN", "computed_at": "STRING",
    },
}  # fmt: skip
KEYS: dict[str, tuple[str, ...]] = {
    RESULTS: ("evaluation", "run_id", "sample", "closing", "metric", "subject"),
    DISTRIBUTION: ("evaluation", "run_id", "sample", "closing", "metric", "subject", "bin"),
    GRADES: ("evaluation", "run_id", "sample", "closing", "grade"),
}


def _kind(kind: str) -> str:
    if kind not in KINDS:
        fail(f"table kind must be one of {', '.join(KINDS)}, got {kind!r}")
    return kind


def table_rows(result: EvaluationResult, kind: str, *, run_id: str, now: str) -> list[dict[str, Any]]:
    """Return the rows of table ``kind`` for ``result``, stamped with the run id and the computation time."""
    stamp = {
        "evaluation": result.config.name,
        "run_id": run_id,
        "config_fingerprint": result.config.fingerprint(),
        "computed_at": now,
    }
    source = {RESULTS: result.results, DISTRIBUTION: result.distribution, GRADES: result.grades}[_kind(kind)]
    return [{c: {**row, **stamp}.get(c) for c in SCHEMAS[kind]} for row in source]


def typed_row(kind: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return ``row`` as a tuple in schema order with Python types Spark accepts for each column."""
    casts = {"DOUBLE": float, "INT": int, "BIGINT": int, "BOOLEAN": bool}
    values = []
    for column, type_name in SCHEMAS[_kind(kind)].items():
        value = row.get(column)
        values.append(value if value is None or type_name not in casts else casts[type_name](value))
    return tuple(values)


def to_frame(spark: SparkSession, kind: str, rows: Sequence[Mapping[str, Any]]) -> DataFrame:
    """Build the typed DataFrame of table ``kind`` from rows."""
    ddl = ", ".join(f"`{name}` {type_name}" for name, type_name in SCHEMAS[_kind(kind)].items())
    return spark.createDataFrame([typed_row(kind, r) for r in rows], ddl)


def write_options(kind: str) -> dict[str, object]:
    """Return the task write metadata of table ``kind``: a merge on its key; an empty write creates the table."""
    return {"write": "merge", "keys": list(KEYS[_kind(kind)]), "on_empty": "allow_empty"}


__all__ = [
    "DISTRIBUTION",
    "GRADES",
    "KEYS",
    "KINDS",
    "RESULTS",
    "SCHEMAS",
    "table_rows",
    "to_frame",
    "typed_row",
    "write_options",
]
