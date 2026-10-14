"""Apply registry comments and ``bricksmith.*`` properties to a written table, keeping unrelated metadata."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.registry.metadata import table_metadata
from bricksmith.storage.plan import MUTATE, Step
from bricksmith.storage.sql import literal, quote

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.registry.model import Registry


def plan_registry_metadata(  # noqa: PLR0913, PLR0917 - the full existing state
    registry: Registry,
    registry_table: str,
    physical: str,
    columns: Sequence[str],
    existing_comment: str,
    existing_columns: Mapping[str, str],
    existing_properties: Mapping[str, str],
) -> list[Step]:
    """Return only the statements that change something; unknown tables and columns yield nothing."""
    if registry_table not in registry.tables:
        return []
    merged = table_metadata(registry, registry_table, existing_columns, existing_properties)
    t = quote(physical)
    steps: list[Step] = []
    if merged.table_comment and merged.table_comment != existing_comment:
        steps.append(Step(MUTATE, "table comment", f"COMMENT ON TABLE {t} IS {literal(merged.table_comment)}"))
    for column in columns:
        wanted = merged.column_comments.get(column)
        if wanted and wanted != existing_columns.get(column):
            sql = f"ALTER TABLE {t} ALTER COLUMN {quote(column)} COMMENT {literal(wanted)}"
            steps.append(Step(MUTATE, f"comment on {column}", sql))
    stale = sorted(k for k in existing_properties if k not in merged.properties)
    changed = {k: v for k, v in merged.properties.items() if existing_properties.get(k) != v}
    if changed:
        body = ", ".join(f"{literal(k)} = {literal(v)}" for k, v in sorted(changed.items()))
        steps.append(Step(MUTATE, "registry properties", f"ALTER TABLE {t} SET TBLPROPERTIES ({body})"))
    if stale:
        body = ", ".join(literal(k) for k in stale)
        steps.append(
            Step(MUTATE, "remove stale registry properties", f"ALTER TABLE {t} UNSET TBLPROPERTIES IF EXISTS ({body})")
        )
    return steps


def apply_registry_metadata(spark: Any, registry: Registry, registry_table: str, physical: str) -> list[Step]:  # noqa: ANN401 - Spark session
    """Read the table's current metadata, then run the changed statements; returns the steps applied."""
    frame = spark.table(physical)
    columns = list(frame.columns)
    existing_columns = {f.name: f.metadata.get("comment", "") for f in frame.schema.fields if f.metadata.get("comment")}
    rows = spark.sql(f"DESCRIBE TABLE EXTENDED {quote(physical)}").collect()
    comment = next((r["data_type"] for r in rows if r["col_name"] == "Comment"), "")
    props = {r["key"]: r["value"] for r in spark.sql(f"SHOW TBLPROPERTIES {quote(physical)}").collect()}
    steps = plan_registry_metadata(registry, registry_table, physical, columns, comment, existing_columns, props)
    for step in steps:
        spark.sql(step.sql)
    return steps
