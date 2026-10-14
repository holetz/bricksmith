"""Pure functions that turn registry metadata into comments, properties and coverage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from bricksmith.registry.model import UNCLASSIFIED

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from bricksmith.registry.model import Registry

OWNED_PREFIX = "bricksmith."


@dataclass(frozen=True)
class TableMetadata:
    """Comment and property values ready for a table write or catalog documentation."""

    table_comment: str
    column_comments: dict[str, str]
    properties: dict[str, str]


@dataclass(frozen=True)
class SensitivityCoverage:
    """Columns of a table set by state; entries are ``table.column``."""

    classified: tuple[str, ...]
    unclassified: tuple[str, ...]
    unknown: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        """Return the JSON form."""
        return {
            "classified": list(self.classified),
            "unclassified": list(self.unclassified),
            "unknown": list(self.unknown),
        }


def desired_properties(registry: Registry, table: str) -> dict[str, str]:
    """Properties owned by bricksmith for ``table``: declared grain and per-column sensitivity."""
    spec = registry.tables[table]
    props: dict[str, str] = {}
    if spec.grain:
        props[f"{OWNED_PREFIX}grain"] = ",".join(spec.grain)
    for col in sorted(spec.columns):
        resolved = registry.resolve(table, col)
        if resolved is not None:
            props[f"{OWNED_PREFIX}sensitivity.{col}"] = resolved.sensitivity
    return props


def merge_comments(existing: Mapping[str, str], desired: Mapping[str, str]) -> dict[str, str]:
    """Registry descriptions win; comments the registry has no opinion about are kept."""
    return {**existing, **{k: v for k, v in desired.items() if v}}


def merge_properties(existing: Mapping[str, str], desired: Mapping[str, str]) -> dict[str, str]:
    """Keep every existing property except stale ``bricksmith.*`` ones; desired values win."""
    kept = {k: v for k, v in existing.items() if not k.startswith(OWNED_PREFIX) or k in desired}
    return {**kept, **desired}


def table_metadata(
    registry: Registry,
    table: str,
    existing_comments: Mapping[str, str] | None = None,
    existing_properties: Mapping[str, str] | None = None,
) -> TableMetadata:
    """Build the merged table comment, column comments and properties for ``table``."""
    spec = registry.tables[table]
    desired: dict[str, str] = {}
    for col in spec.columns:
        resolved = registry.resolve(table, col)
        if resolved is not None:
            desired[col] = resolved.description
    return TableMetadata(
        table_comment=spec.description,
        column_comments=merge_comments(existing_comments or {}, desired),
        properties=merge_properties(existing_properties or {}, desired_properties(registry, table)),
    )


def sensitivity_coverage(registry: Registry, tables: Iterable[str] | None = None) -> SensitivityCoverage:
    """Report classified, unclassified and unknown (undefined) columns."""
    classified: list[str] = []
    unclassified: list[str] = []
    unknown: list[str] = []
    for table in sorted(tables if tables is not None else registry.tables):
        for col in sorted(registry.tables[table].columns):
            resolved = registry.resolve(table, col)
            bucket = (
                unknown if resolved is None else unclassified if resolved.sensitivity == UNCLASSIFIED else classified
            )
            bucket.append(f"{table}.{col}")
    return SensitivityCoverage(tuple(classified), tuple(unclassified), tuple(unknown))
