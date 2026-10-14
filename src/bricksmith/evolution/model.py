"""The Spark-free table schema and metadata model, compared between declared and live state."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from bricksmith.evolution.errors import SchemaSpecError
from bricksmith.evolution.types import Field, Struct, field_from_json, from_spark_json

CONSTRAINT_PREFIX = "delta.constraints."
_KEYS = frozenset({"columns", "comment", "checks", "primary_key", "key_name", "properties"})


@dataclass(frozen=True)
class TableSchema:
    """Columns plus metadata. ``None`` for ``comment`` or ``primary_key`` means "not managed".

    ``checks`` and ``properties`` hold the managed entries only when declared; a live schema holds them all.
    """

    columns: tuple[Field, ...]
    comment: str | None = None
    checks: Mapping[str, str] = field(default_factory=dict)
    primary_key: tuple[str, ...] | None = None
    key_name: str | None = None
    properties: Mapping[str, str] = field(default_factory=dict)

    def column(self, name: str) -> Field | None:
        """Return the top-level column called ``name``."""
        return next((c for c in self.columns if c.name == name), None)

    def to_dict(self) -> dict[str, object]:
        """Return the stable serializable form (sorted mappings)."""
        out: dict[str, object] = {
            "checks": dict(sorted(self.checks.items())),
            "columns": [c.to_json() for c in self.columns],
            "properties": dict(sorted(self.properties.items())),
        }
        if self.comment is not None:
            out["comment"] = self.comment
        if self.primary_key is not None:
            out["primary_key"] = list(self.primary_key)
        if self.key_name is not None:
            out["key_name"] = self.key_name
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TableSchema:
        """Validate and build a schema from a mapping (a declaration or :meth:`to_dict` output)."""
        extra = set(data) - _KEYS
        if extra:
            msg = f"unknown schema keys: {sorted(extra)}"
            raise SchemaSpecError(msg)
        columns = tuple(field_from_json(c) for c in data.get("columns", ()))
        names = [c.name for c in columns]
        if not columns or len(set(names)) != len(names):
            msg = "a schema needs at least one column and unique column names"
            raise SchemaSpecError(msg)
        checks, props = data.get("checks", {}), data.get("properties", {})
        if not isinstance(checks, Mapping) or not isinstance(props, Mapping):
            msg = "checks and properties are mappings of text to text"
            raise SchemaSpecError(msg)
        key = data.get("primary_key")
        if key is not None and (not isinstance(key, list | tuple) or not set(key) <= set(names)):
            msg = f"primary_key must list declared columns, got {key!r}"
            raise SchemaSpecError(msg)
        comment = data.get("comment")
        if comment is not None and not isinstance(comment, str):
            msg = "comment must be text"
            raise SchemaSpecError(msg)
        return cls(
            columns,
            comment,
            {str(k): str(v) for k, v in checks.items()},
            tuple(key) if key is not None else None,
            data.get("key_name"),
            {str(k): str(v) for k, v in props.items()},
        )

    def struct(self) -> Struct:
        """Return the columns as a struct."""
        return Struct(self.columns)


def schema_from_spark(
    schema_json: Mapping[str, Any],
    *,
    properties: Mapping[str, str] | None = None,
    comment: str | None = None,
    primary_key: tuple[str, ...] | None = None,
    key_name: str | None = None,
) -> TableSchema:
    """Build the live model from ``StructType.jsonValue()`` and the raw table properties."""
    struct = from_spark_json(schema_json)
    if not isinstance(struct, Struct):
        msg = "a table schema must be a struct"
        raise SchemaSpecError(msg)
    raw = dict(properties or {})
    checks = {k[len(CONSTRAINT_PREFIX) :]: v for k, v in raw.items() if k.startswith(CONSTRAINT_PREFIX)}
    plain = {k: v for k, v in raw.items() if not k.startswith(CONSTRAINT_PREFIX)}
    return TableSchema(struct.fields, comment or None, checks, primary_key, key_name, plain)


def schema_from_columns(columns: Mapping[str, Any], *, base: TableSchema | None = None) -> TableSchema:
    """Build a column-only declaration from ``StructType.jsonValue()``, keeping ``base`` metadata unmanaged."""
    live = schema_from_spark(columns)
    if base is None:
        return live
    return TableSchema(live.columns, base.comment, base.checks, base.primary_key, base.key_name, base.properties)


def normalize_expression(expression: str) -> str:
    """Normalize a SQL boolean expression for comparison: case, spacing, quoting and outer parentheses."""
    text = re.sub(r"\s+", " ", expression.replace("`", "")).strip().lower()
    while text.startswith("(") and text.endswith(")") and _balanced(text[1:-1]):
        text = text[1:-1].strip()
    return text


def _balanced(text: str) -> bool:
    depth = 0
    for char in text:
        depth += char == "("
        depth -= char == ")"
        if depth < 0:
            return False
    return depth == 0


def references(expression: str, name: str) -> bool:
    """Whether ``expression`` mentions the column (or dotted field path) ``name`` as an identifier."""
    text = expression.replace("`", "")
    pattern = r"(?<![\w.])" + re.escape(name) + r"(?![\w])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def substitute(expression: str, old: str, new: str) -> str:
    """Replace identifier ``old`` by ``new`` in ``expression`` (backticks around it are dropped)."""
    text = expression.replace("`", "")
    pattern = r"(?<![\w.])" + re.escape(old) + r"(?![\w])"
    return re.sub(pattern, new, text, flags=re.IGNORECASE)
