"""Small, pure SQL rendering helpers: identifiers, literals and predicates."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

INPUT = "{input}"


def quote(name: str) -> str:
    """Backtick-quote a possibly dotted name part by part."""
    return ".".join("`" + part.replace("`", "``") + "`" for part in name.split("."))


def literal(value: object) -> str:
    """Render a Python value as a SQL literal (strings, booleans, numbers, dates and timestamps)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    iso = getattr(value, "isoformat", None)
    text = str(iso() if callable(iso) else value)
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def equal(column: str, value: object) -> str:
    """Return ``column = value`` or ``column IS NULL``."""
    return f"{quote(column)} IS NULL" if value is None else f"{quote(column)} = {literal(value)}"


def partition_predicate(values: Sequence[Mapping[str, object]]) -> str:
    """Return an ``OR`` of ``AND`` equalities, one group per partition, in a stable order."""
    groups = sorted("(" + " AND ".join(equal(c, v) for c, v in sorted(row.items())) + ")" for row in values)
    return " OR ".join(groups)


def column_list(columns: Sequence[str], prefix: str = "") -> str:
    """Return ``prefix`col``, ... for ``columns``."""
    return ", ".join(f"{prefix}{quote(c)}" for c in columns)


def key_match(keys: Sequence[str], left: str, right: str) -> str:
    """Return the equality of every key between two aliases."""
    return " AND ".join(f"{left}.{quote(k)} = {right}.{quote(k)}" for k in keys)
