"""The selection tables: the ledger and the per-variable outcomes, flat and typed, keyed for a Delta merge.

An ordinary task returns ``to_frame(spark, kind, table_rows(result, kind, run_id=..., now=...))`` with
``metadata=write_options(kind)``; reruns of the same selection and run id replace their own rows. Every column
a report filters on is a plain scalar.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.mlops.selection.config import fail

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pyspark.sql import DataFrame, SparkSession  # pyright: ignore[reportMissingImports]

    from bricksmith.mlops.selection.funnel import SelectionResult

LEDGER = "ledger"
VARIABLES = "variables"
KINDS = (LEDGER, VARIABLES)
SCHEMAS: dict[str, dict[str, str]] = {
    LEDGER: {
        "selection": "STRING", "run_id": "STRING", "config_fingerprint": "STRING", "variable": "STRING",
        "step": "STRING", "step_order": "INT", "metric": "STRING", "value": "DOUBLE", "threshold": "DOUBLE",
        "rule": "STRING", "decision": "STRING", "reason": "STRING", "computed_at": "STRING",
    },
    VARIABLES: {
        "selection": "STRING", "run_id": "STRING", "config_fingerprint": "STRING", "variable": "STRING",
        "kind": "STRING", "selected": "BOOLEAN", "left_at": "STRING", "reason": "STRING", "computed_at": "STRING",
    },
}  # fmt: skip
KEYS: dict[str, tuple[str, ...]] = {
    LEDGER: ("selection", "run_id", "variable", "step", "metric"),
    VARIABLES: ("selection", "run_id", "variable"),
}


def _kind(kind: str) -> str:
    if kind not in KINDS:
        fail(f"table kind must be one of {', '.join(KINDS)}, got {kind!r}")
    return kind


def table_rows(result: SelectionResult, kind: str, *, run_id: str, now: str) -> list[dict[str, Any]]:
    """Return the rows of table ``kind`` for ``result``, stamped with the run id and the computation time."""
    stamp = {
        "selection": result.config.name,
        "run_id": run_id,
        "config_fingerprint": result.config.fingerprint(),
        "computed_at": now,
    }
    source = result.ledger_rows() if _kind(kind) == LEDGER else result.variable_rows()
    return [{c: {**row, **stamp}.get(c) for c in SCHEMAS[kind]} for row in source]


def typed_row(kind: str, row: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return ``row`` as a tuple in schema order with Python types Spark accepts for each column."""
    values = []
    for column, type_name in SCHEMAS[_kind(kind)].items():
        value = row.get(column)
        if value is not None:
            if type_name == "DOUBLE":
                value = float(value)
            elif type_name == "INT":
                value = int(value)
            elif type_name == "BOOLEAN":
                value = bool(value)
        values.append(value)
    return tuple(values)


def to_frame(spark: SparkSession, kind: str, rows: Sequence[Mapping[str, Any]]) -> DataFrame:
    """Build the typed DataFrame of table ``kind`` from rows."""
    ddl = ", ".join(f"`{name}` {type_name}" for name, type_name in SCHEMAS[_kind(kind)].items())
    return spark.createDataFrame([typed_row(kind, r) for r in rows], ddl)


def write_options(kind: str) -> dict[str, object]:
    """Return the task write metadata of table ``kind``: a merge on its key; an empty write creates the table."""
    return {"write": "merge", "keys": list(KEYS[_kind(kind)]), "on_empty": "allow_empty"}


__all__ = ["KEYS", "KINDS", "LEDGER", "SCHEMAS", "VARIABLES", "table_rows", "to_frame", "typed_row", "write_options"]
