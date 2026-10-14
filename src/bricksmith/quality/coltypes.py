"""Column type classification shared by comparison planning and checks."""

from __future__ import annotations

import re

NUMERIC = ("tinyint", "smallint", "int", "integer", "bigint", "long", "short", "byte", "float", "double", "real")
_SCALAR = ("string", "boolean", "date", "timestamp", "timestamp_ntz", "char", "varchar")
_UNSUPPORTED = ("map", "binary", "interval", "variant", "object", "void", "null")
_WORD = re.compile(r"[a-z_]+")


def normalize(type_name: str) -> str:
    """Return a lower-case type string without surrounding blanks."""
    return type_name.strip().lower()


def words(type_name: str) -> set[str]:
    """Return every type word inside a possibly nested type string."""
    return set(_WORD.findall(normalize(type_name)))


def is_numeric(type_name: str) -> bool:
    """Tell whether the type is a plain numeric scalar (integers, floats, decimals)."""
    base = normalize(type_name).split("(", 1)[0].strip()
    return base in NUMERIC or base in ("decimal", "numeric", "dec")


def unsupported_reason(type_name: str) -> str | None:
    """Return why values of this type cannot be compared, or ``None`` when they can."""
    found = sorted(words(type_name) & set(_UNSUPPORTED))
    if found:
        return f"type {type_name!r} contains {', '.join(found)}, which has no defined equality"
    return None


def is_temporal(type_name: str) -> bool:
    """Tell whether the type is a date or timestamp."""
    return normalize(type_name).split("(", 1)[0] in ("date", "timestamp", "timestamp_ntz")


def comparable(left: str, right: str) -> bool:
    """Tell whether two supported types may be compared (identical, or both numeric)."""
    if normalize(left) == normalize(right):
        return True
    return is_numeric(left) and is_numeric(right)
