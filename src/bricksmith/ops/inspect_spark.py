"""Spark/Delta implementation of the table inspector (metadata only; no row contents are returned)."""
# ruff: noqa: S608 - SQL is rendered from backtick-quoted identifiers

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bricksmith.ops.readiness import TableStats
from bricksmith.storage.sql import quote

if TYPE_CHECKING:
    from collections.abc import Sequence

# Delta history operations that do not change table data (maintenance and metadata changes).
_NON_DATA_PREFIXES = (
    "OPTIMIZE", "VACUUM", "SET TBLPROPERTIES", "UNSET TBLPROPERTIES", "ADD CONSTRAINT", "DROP CONSTRAINT",
    "CHANGE COLUMN", "ADD COLUMNS", "RENAME COLUMN", "DROP COLUMNS", "CLUSTER BY", "ANALYZE", "UPGRADE",
    "MANUAL UPDATE",
)  # fmt: skip


class SparkDeltaInspector:
    """Reads existence, row count, last data change (Delta history), columns and null counts."""

    def __init__(self, spark: Any) -> None:  # noqa: ANN401 - session
        """Keep the session; nothing runs here."""
        self._spark = spark

    def inspect(self, name: str, *, null_columns: Sequence[str] = ()) -> TableStats:
        """Return the statistics of ``name``; an absent table has ``exists=False``."""
        spark = self._spark
        if not spark.catalog.tableExists(name):
            return TableStats(name, exists=False)
        table = quote(name)
        columns = tuple(spark.table(name).columns)
        aggregates = ["count(*) AS n"] + [f"count_if({quote(c)} IS NULL) AS n_{i}" for i, c in enumerate(null_columns)]
        row = spark.sql(f"SELECT {', '.join(aggregates)} FROM {table}").collect()[0]
        nulls = {c: int(row[f"n_{i}"]) for i, c in enumerate(null_columns)} if null_columns else None
        modified, note = None, ""
        try:
            history = f"SELECT operation, unix_timestamp(timestamp) AS epoch, version FROM (DESCRIBE HISTORY {table})"
            for entry in spark.sql(f"{history} ORDER BY epoch DESC, version DESC LIMIT 100").collect():
                if not str(entry["operation"]).upper().startswith(_NON_DATA_PREFIXES):
                    modified = datetime.fromtimestamp(int(entry["epoch"]), UTC)
                    break
            else:
                note = "no data-changing entry in the last 100 history records"
        except Exception as error:  # noqa: BLE001 - views and non-Delta tables have no history
            note = f"history is not available ({type(error).__name__})"
        return TableStats(
            name,
            exists=True,
            row_count=int(row["n"]),
            last_modified=modified,
            null_counts=nulls,
            columns=columns,
            note=note,
        )
