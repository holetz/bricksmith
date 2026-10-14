"""The column registry: versioned project data read from TOML."""

from __future__ import annotations

import dataclasses
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bricksmith.registry.errors import fail

FORMAT_VERSION = 1
UNCLASSIFIED = "unclassified"
_COLUMN_FIELDS = frozenset({"type", "description", "not_null", "checks", "sensitivity"})


@dataclass(frozen=True)
class ColumnSpec:
    """A resolved column definition."""

    name: str
    type: str = ""
    description: str = ""
    not_null: bool = False
    checks: tuple[str, ...] = ()
    sensitivity: str = UNCLASSIFIED


@dataclass(frozen=True)
class TableSpec:
    """A table in the registry: description, declared grain and per-column overrides."""

    name: str
    description: str = ""
    grain: tuple[str, ...] = ()
    columns: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Registry:
    """Base column definitions plus the tables that use them."""

    columns: dict[str, ColumnSpec] = field(default_factory=dict)
    tables: dict[str, TableSpec] = field(default_factory=dict)
    duplicates: tuple[str, ...] = ()

    def resolve(self, table: str, column: str) -> ColumnSpec | None:
        """Return the column as seen by ``table`` (base plus override), or None if undefined."""
        base = self.columns.get(column)
        override = self.tables[table].columns.get(column, {}) if table in self.tables else {}
        if base is None:
            if "type" not in override:
                return None
            base = ColumnSpec(name=column)
        return dataclasses.replace(base, **override)


def _check_keys(data: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        fail(f"{where}: unknown keys {unknown}")


def _fields(data: dict[str, Any], where: str) -> dict[str, Any]:
    """Validate the shape of column fields and return the ones that are present."""
    _check_keys(data, _COLUMN_FIELDS, where)
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key == "not_null":
            ok = isinstance(value, bool)
        elif key == "checks":
            ok = isinstance(value, list) and all(isinstance(item, str) for item in value)
            value = tuple(value) if ok else value  # noqa: PLW2901 - normalise to an immutable value
        else:
            ok = isinstance(value, str)
        if not ok:
            fail(f"{where}: invalid value for '{key}'")
        out[key] = value
    return out


def parse_registry(data: dict[str, Any], source: str = "registry") -> Registry:
    """Build a ``Registry`` from parsed TOML data, rejecting unknown keys and versions."""
    _check_keys(data, frozenset({"version", "columns", "tables"}), source)
    if data.get("version") != FORMAT_VERSION:
        fail(f"{source}: unsupported or missing version (expected {FORMAT_VERSION})")
    columns: dict[str, ColumnSpec] = {}
    duplicates: list[str] = []
    for entry in data.get("columns", []):
        if not isinstance(entry.get("name"), str):
            fail(f"{source}: every [[columns]] entry needs a string 'name'")
        name = entry["name"]
        values = _fields({k: v for k, v in entry.items() if k != "name"}, f"{source}: column {name}")
        if name in columns:
            duplicates.append(name)
        columns[name] = ColumnSpec(name=name, **values)
    tables: dict[str, TableSpec] = {}
    for table, entry in data.get("tables", {}).items():
        if len(table.split(".")) != 3:  # noqa: PLR2004 - catalog.schema.table
            fail(f"{source}: table '{table}' must be 'catalog.schema.table'")
        _check_keys(entry, frozenset({"description", "grain", "columns"}), f"{source}: table {table}")
        grain = entry.get("grain", [])
        if not isinstance(grain, list) or not all(isinstance(item, str) for item in grain):
            fail(f"{source}: table {table}: 'grain' must be a list of strings")
        overrides = {
            col: _fields(values, f"{source}: table {table}, column {col}")
            for col, values in entry.get("columns", {}).items()
        }
        tables[table] = TableSpec(table, str(entry.get("description", "")), tuple(grain), overrides)
    return Registry(columns, tables, tuple(sorted(set(duplicates))))


def load_registry(path: Path | str) -> Registry:
    """Read a registry TOML file."""
    target = Path(path)
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        fail(f"{target}: cannot read registry ({error})")
    return parse_registry(data, str(target))
