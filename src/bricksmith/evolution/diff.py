"""Compare a declared schema with the live one, down to nested fields, checks, keys and properties."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bricksmith.evolution.errors import SchemaSpecError
from bricksmith.evolution.model import TableSchema, normalize_expression
from bricksmith.evolution.types import (
    INCOMPATIBLE,
    SAME,
    Array,
    DataType,
    Field,
    Map,
    Struct,
    compare_types,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

ADD_COLUMN, DROP_COLUMN, RENAME_COLUMN = "add_column", "drop_column", "rename_column"
CHANGE_TYPE, SET_NULLABLE, SET_COMMENT = "change_type", "set_nullable", "set_comment"
ADD_CHECK, CHANGE_CHECK = "add_check", "change_check"
SET_KEY, SET_PROPERTY, SET_TABLE_COMMENT = "set_key", "set_property", "set_table_comment"


@dataclass(frozen=True)
class Change:
    """One difference. ``before``/``after`` are text, booleans or lists; ``type_change`` classifies type changes."""

    kind: str
    path: str
    before: object = None
    after: object = None
    type_change: str = ""
    declared: Field | None = field(default=None, compare=False)
    live: Field | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, object]:
        """Return the stable serializable form."""
        return {
            "after": self.after,
            "before": self.before,
            "kind": self.kind,
            "path": self.path,
            "type_change": self.type_change,
        }


def _rename(columns: tuple[Field, ...], parts: list[str], new: str) -> tuple[Field, ...]:
    out: list[Field] = []
    for col in columns:
        if col.name != parts[0]:
            out.append(col)
        elif len(parts) == 1:
            out.append(Field(new, col.type, col.nullable, col.comment))
        else:
            out.append(Field(col.name, _rename_in_type(col.type, parts[1:], new), col.nullable, col.comment))
    return tuple(out)


def _rename_in_type(dtype: DataType, parts: list[str], new: str) -> DataType:
    if isinstance(dtype, Struct):
        return Struct(_rename(dtype.fields, parts, new))
    if isinstance(dtype, Array) and parts[0] == "element":
        return Array(_rename_in_type(dtype.element, parts[1:], new), dtype.contains_null)
    if isinstance(dtype, Map) and parts[0] == "value":
        return Map(dtype.key, _rename_in_type(dtype.value, parts[1:], new), dtype.value_contains_null)
    return dtype


def _find(columns: tuple[Field, ...], path: str) -> Field | None:
    parts, scope, index = path.split("."), columns, 0
    current: Field | None = None
    while index < len(parts):
        current = next((f for f in scope if f.name == parts[index]), None)
        if current is None:
            return None
        index += 1
        if index == len(parts):
            break
        dtype = current.type
        while index < len(parts) and isinstance(dtype, Array | Map) and parts[index] in {"element", "value"}:
            dtype = dtype.element if isinstance(dtype, Array) else dtype.value
            index += 1
        if not isinstance(dtype, Struct):
            return None
        scope = dtype.fields
    return current


def renamed_live(live: TableSchema, renames: Mapping[str, str]) -> tuple[TableSchema, list[Change]]:
    """Apply declared renames (``old path`` to ``new leaf name``) to ``live``; already applied ones are skipped."""
    columns, changes = live.columns, []
    for old, new in sorted(renames.items()):
        parent = old.rpartition(".")[0]
        new_path = f"{parent}.{new}" if parent else new
        present, target = _find(columns, old), _find(columns, new_path)
        if present is None and target is not None:
            continue  # applied before: re-running is a no-op
        if present is None:
            msg = f"rename source {old!r} does not exist and {new_path!r} is absent"
            raise SchemaSpecError(msg)
        if target is not None:
            msg = f"rename {old!r} to {new!r}: the target already exists"
            raise SchemaSpecError(msg)
        columns = _rename(columns, old.split("."), new)
        changes.append(Change(RENAME_COLUMN, old, old, new_path))
    key = live.primary_key
    if key is not None:
        key = tuple(renames.get(k, k) for k in key)
    return TableSchema(columns, live.comment, live.checks, key, live.key_name, live.properties), changes


def _diff_fields(declared: tuple[Field, ...], live: tuple[Field, ...], prefix: str) -> list[Change]:
    out: list[Change] = []
    live_by = {f.name: f for f in live}
    declared_names = {f.name for f in declared}
    for want in declared:
        path = f"{prefix}{want.name}"
        have = live_by.get(want.name)
        if have is None:
            out.append(Change(ADD_COLUMN, path, None, want.type.ddl(), declared=want))
            continue
        out.extend(_diff_field(want, have, path))
    out.extend(
        Change(DROP_COLUMN, f"{prefix}{f.name}", f.type.ddl(), None, live=f)
        for f in live
        if f.name not in declared_names
    )
    return out


def _diff_field(want: Field, have: Field, path: str) -> list[Change]:
    out: list[Change] = []
    if want.nullable != have.nullable:
        out.append(Change(SET_NULLABLE, path, have.nullable, want.nullable, declared=want, live=have))
    if want.comment is not None and want.comment != (have.comment or ""):
        out.append(Change(SET_COMMENT, path, have.comment, want.comment, declared=want, live=have))
    out.extend(_diff_type(want, have, want.type, have.type, path))
    return out


def _diff_type(want: Field, have: Field, new: DataType, old: DataType, path: str) -> list[Change]:
    if isinstance(new, Struct) and isinstance(old, Struct):
        return _diff_fields(new.fields, old.fields, path + ".")
    if isinstance(new, Array) and isinstance(old, Array):
        out = _diff_type(want, have, new.element, old.element, f"{path}.element")
        if new.contains_null != old.contains_null:
            out.append(Change(SET_NULLABLE, f"{path}.element", old.contains_null, new.contains_null))
        return out
    if isinstance(new, Map) and isinstance(old, Map):
        out = _diff_type(want, have, new.value, old.value, f"{path}.value")
        if new.key != old.key:
            out.append(Change(CHANGE_TYPE, f"{path}.key", old.ddl(), new.ddl(), INCOMPATIBLE, want, have))
        return out
    kind = compare_types(old, new)
    if kind == SAME:
        return []
    return [Change(CHANGE_TYPE, path, old.ddl(), new.ddl(), kind, want, have)]


def diff_schemas(declared: TableSchema, live: TableSchema, renames: Mapping[str, str] | None = None) -> list[Change]:
    """Return the changes that turn ``live`` into ``declared``, in a stable order.

    Renames must be declared; without one, a missing and an extra column are a drop plus an add. Only
    declared checks, keys and properties are managed; undeclared live ones are left alone.
    """
    moved, changes = renamed_live(live, renames or {})
    changes = [*changes, *_diff_fields(declared.columns, moved.columns, "")]
    if declared.comment is not None and declared.comment != (live.comment or ""):
        changes.append(Change(SET_TABLE_COMMENT, "", live.comment, declared.comment))
    for name, expression in sorted(declared.checks.items()):
        have = live.checks.get(name)
        if have is None:
            changes.append(Change(ADD_CHECK, name, None, expression))
        elif normalize_expression(have) != normalize_expression(expression):
            changes.append(Change(CHANGE_CHECK, name, have, expression))
    if declared.primary_key is not None and tuple(declared.primary_key) != tuple(moved.primary_key or ()):
        changes.append(Change(SET_KEY, "primary_key", list(moved.primary_key or ()), list(declared.primary_key)))
    changes.extend(
        Change(SET_PROPERTY, key, live.properties.get(key), value)
        for key, value in sorted(declared.properties.items())
        if live.properties.get(key) != value
    )
    return changes
