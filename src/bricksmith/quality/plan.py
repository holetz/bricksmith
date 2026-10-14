"""Pure comparison planning: two schemas plus declared keys give a serializable plan."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.quality.coltypes import comparable, is_numeric, unsupported_reason
from bricksmith.quality.errors import QualityError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

PLAN_VERSION = 1
ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Tolerance:
    """A numeric tolerance. Two values match when ``|l - r| <= max(absolute, relative * max(|l|, |r|))``.

    The boundary is inclusive: a difference exactly equal to the allowed one matches, anything larger does not.
    Both limits default to zero, which means exact equality. NaN equals NaN and equal infinities match.
    """

    absolute: float = 0.0
    relative: float = 0.0

    def __post_init__(self) -> None:
        """Reject negative, NaN or infinite limits."""
        for label, value in (("absolute", self.absolute), ("relative", self.relative)):
            if not math.isfinite(value) or value < 0:
                msg = f"{label} tolerance must be a finite number >= 0, got {value!r}"
                raise QualityError(msg)

    @property
    def exact(self) -> bool:
        """Tell whether the tolerance is zero."""
        return self.absolute == 0 and self.relative == 0

    def to_dict(self) -> dict[str, float]:
        """Return the serializable form."""
        return {"absolute": self.absolute, "relative": self.relative}


@dataclass(frozen=True)
class Diagnostic:
    """A planning finding. ``error`` blocks execution; ``warning`` records an exclusion or a difference."""

    code: str
    severity: str
    message: str
    column: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"code": self.code, "column": self.column, "message": self.message, "severity": self.severity}


@dataclass(frozen=True)
class ColumnPlan:
    """One compared column."""

    name: str
    left_type: str
    right_type: str
    tolerance: Tolerance = field(default_factory=Tolerance)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "left_type": self.left_type,
            "name": self.name,
            "right_type": self.right_type,
            "tolerance": self.tolerance.to_dict(),
        }


@dataclass(frozen=True)
class ComparisonPlan:
    """What a comparison will do: keys, compared columns, tolerances and every exclusion with its reason."""

    keys: tuple[str, ...]
    columns: tuple[ColumnPlan, ...]
    diagnostics: tuple[Diagnostic, ...]
    left: str = "left"
    right: str = "right"

    @property
    def executable(self) -> bool:
        """Tell whether the plan has no blocking diagnostic."""
        return not any(d.severity == ERROR for d in self.diagnostics)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "columns": [c.to_dict() for c in self.columns],
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "executable": self.executable,
            "keys": list(self.keys),
            "left": self.left,
            "right": self.right,
            "version": PLAN_VERSION,
        }

    def to_json(self) -> str:
        """Return stable, sorted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ComparisonPlan:
        """Rebuild a plan from :meth:`to_dict` output."""
        if data.get("version") != PLAN_VERSION:
            msg = f"unsupported comparison plan version {data.get('version')!r}; expected {PLAN_VERSION}"
            raise QualityError(msg)
        return cls(
            keys=tuple(data["keys"]),
            columns=tuple(
                ColumnPlan(c["name"], c["left_type"], c["right_type"], Tolerance(**c["tolerance"]))
                for c in data["columns"]
            ),
            diagnostics=tuple(
                Diagnostic(d["code"], d["severity"], d["message"], d["column"]) for d in data["diagnostics"]
            ),
            left=data["left"],
            right=data["right"],
        )


def _find(schema: Mapping[str, str], name: str) -> str | None:
    return schema.get(name)


def _key_diagnostics(
    keys: Sequence[str], left: Mapping[str, str], right: Mapping[str, str], out: list[Diagnostic]
) -> None:
    if not keys:
        out.append(Diagnostic("no_keys", ERROR, "at least one key column is required"))
    if len(set(keys)) != len(keys):
        out.append(Diagnostic("duplicate_key_column", ERROR, "a key column is listed twice"))
    for key in keys:
        lt, rt = _find(left, key), _find(right, key)
        for side, found in (("left", lt), ("right", rt)):
            if found is None:
                out.append(Diagnostic("key_missing", ERROR, f"key {key!r} is not a column of the {side} schema", key))
        if lt is None or rt is None:
            continue
        for side, found in (("left", lt), ("right", rt)):
            reason = unsupported_reason(found)
            if reason:
                out.append(Diagnostic("key_unsupported_type", ERROR, f"key {key!r} ({side}): {reason}", key))
        if not comparable(lt, rt):
            out.append(Diagnostic("key_type_mismatch", ERROR, f"key {key!r} has types {lt!r} and {rt!r}", key))


def _tolerance_for(
    name: str, tolerances: Mapping[str, Tolerance], default: Tolerance | None, *, numeric: bool, out: list[Diagnostic]
) -> Tolerance:
    wanted = tolerances.get(name) or default
    if wanted is None or wanted.exact:
        return Tolerance()
    if not numeric:
        if name in tolerances:
            out.append(
                Diagnostic("tolerance_ignored", ERROR, f"a tolerance was set for non-numeric column {name!r}", name)
            )
        return Tolerance()
    return wanted


def plan_comparison(  # noqa: PLR0913 - one keyword per documented planning input
    left_schema: Mapping[str, str],
    right_schema: Mapping[str, str],
    keys: Sequence[str],
    *,
    columns: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
    tolerances: Mapping[str, Tolerance] | None = None,
    default_tolerance: Tolerance | None = None,
    left: str = "left",
    right: str = "right",
) -> ComparisonPlan:
    """Plan a comparison of two datasets whose schemas map column names to type strings.

    ``columns`` selects value columns explicitly; ``None`` selects every non-key column present on both sides
    minus ``exclude``. A column that cannot be compared (map, binary, ... or incompatible types) is never
    skipped silently: it becomes an ``unsupported_type`` / ``type_mismatch`` diagnostic, an error when the
    column was selected explicitly and a warning when it was selected implicitly. Columns present on one side
    only become ``column_only_left`` / ``column_only_right`` warnings (errors when selected explicitly).
    """
    diagnostics: list[Diagnostic] = []
    _key_diagnostics(keys, left_schema, right_schema, diagnostics)
    explicit = columns is not None
    if explicit:
        selected = list(dict.fromkeys(columns or ()))
        diagnostics.extend(
            Diagnostic("key_selected_as_value", ERROR, f"{name!r} is a key", name) for name in selected if name in keys
        )
        selected = [c for c in selected if c not in keys]
    else:
        selected = sorted(set(left_schema) | set(right_schema))
        selected = [c for c in selected if c not in keys and c not in exclude]
    severity = ERROR if explicit else WARNING
    tolerances = tolerances or {}
    compared: list[ColumnPlan] = []
    for name in selected:
        lt, rt = left_schema.get(name), right_schema.get(name)
        if lt is None or rt is None:
            side = "either" if lt is None and rt is None else "left" if lt is None else "right"
            code = "column_missing" if side == "either" else "column_only_right" if lt is None else "column_only_left"
            diagnostics.append(Diagnostic(code, severity, f"column {name!r} is missing from the {side} schema", name))
            continue
        reason = unsupported_reason(lt) or unsupported_reason(rt)
        if reason:
            diagnostics.append(
                Diagnostic("unsupported_type", severity, f"column {name!r} is not compared: {reason}", name)
            )
            continue
        if not comparable(lt, rt):
            msg = f"column {name!r} has incompatible types {lt!r} and {rt!r}"
            diagnostics.append(Diagnostic("type_mismatch", severity, msg, name))
            continue
        numeric = is_numeric(lt) and is_numeric(rt)
        compared.append(
            ColumnPlan(
                name, lt, rt, _tolerance_for(name, tolerances, default_tolerance, numeric=numeric, out=diagnostics)
            )
        )
    for name in tolerances:
        if name not in selected and not any(d.column == name for d in diagnostics):
            diagnostics.append(
                Diagnostic("tolerance_unused", ERROR, f"a tolerance names unselected column {name!r}", name)
            )
    ordered = tuple(sorted(diagnostics, key=lambda d: (d.severity != ERROR, d.code, d.column or "")))
    return ComparisonPlan(tuple(keys), tuple(compared), ordered, left, right)


def schema_of(frame: Any) -> dict[str, str]:  # noqa: ANN401 - Spark DataFrame
    """Return ``{column: type string}`` of a Spark DataFrame."""
    return {field.name: field.dataType.simpleString() for field in frame.schema.fields}


def schema_of_rows(rows: Sequence[Mapping[str, object]], types: Mapping[str, str]) -> dict[str, str]:
    """Return the declared schema for rows; every row column must be declared."""
    for row in rows:
        missing = sorted(set(row) - set(types))
        if missing:
            msg = f"columns without a declared type: {', '.join(missing)}"
            raise QualityError(msg)
    return dict(types)
