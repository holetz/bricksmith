"""Comparison report types shared by the Spark and the pure-Python implementations."""

from __future__ import annotations

import datetime as dt
import decimal
import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.quality.errors import QualityError
from bricksmith.quality.plan import ComparisonPlan

if TYPE_CHECKING:
    from collections.abc import Mapping

REPORT_VERSION = 1
MAX_SAMPLE_LIMIT = 1000
IDENTICAL = "identical"
DIFFERENT = "different"
INVALID = "invalid"


def jsonable(value: object) -> object:  # noqa: PLR0911 - one branch per cell type
    """Convert a cell to a stable JSON value (decimals, dates and non-finite floats become strings)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, decimal.Decimal | dt.date | dt.datetime):
        return value.isoformat() if isinstance(value, dt.date | dt.datetime) else str(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if hasattr(value, "asDict"):
        return jsonable(value.asDict(recursive=True))  # type: ignore[attr-defined]  # Spark Row
    return str(value)


def check_sample_limit(limit: int | None) -> int | None:
    """Validate an explicit sample limit; ``None`` means no samples at all."""
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_SAMPLE_LIMIT:
        msg = f"sample_limit must be an integer from 1 to {MAX_SAMPLE_LIMIT}, got {limit!r}"
        raise QualityError(msg)
    return limit


def format_deviation(value: decimal.Decimal | None) -> str | None:
    """Render a deviation as a plain decimal string without trailing zeros; ``None`` stays ``None``."""
    if value is None:
        return None
    return format(value.normalize(), "f")


def sample_order(deviation: str | None, key: tuple[object, ...]) -> tuple[bool, decimal.Decimal, tuple[object, ...]]:
    """Order severity samples: largest deviation first, ineligible last, then the key ascending."""
    if deviation is None:
        return (True, decimal.Decimal(0), key)
    return (False, -decimal.Decimal(deviation), key)


@dataclass(frozen=True)
class SideStats:
    """Row and key statistics of one side."""

    rows: int
    null_key_rows: int
    duplicate_key_groups: int
    duplicate_key_rows: int

    @property
    def keys_valid(self) -> bool:
        """Tell whether every key is non-null and unique."""
        return self.null_key_rows == 0 and self.duplicate_key_groups == 0

    def to_dict(self) -> dict[str, int]:
        """Return the serializable form."""
        return {
            "duplicate_key_groups": self.duplicate_key_groups,
            "duplicate_key_rows": self.duplicate_key_rows,
            "null_key_rows": self.null_key_rows,
            "rows": self.rows,
        }


@dataclass(frozen=True)
class ColumnResult:
    """Mismatch count of one compared column among rows present on both sides."""

    name: str
    mismatches: int
    max_abs_deviation: str | None = None

    def to_dict(self, *, severity: bool = False) -> dict[str, object]:
        """Return the serializable form; the deviation appears only for a severity report."""
        data: dict[str, object] = {"mismatches": self.mismatches, "name": self.name}
        if severity:
            data["max_abs_deviation"] = self.max_abs_deviation
        return data


@dataclass(frozen=True)
class ComparisonReport:
    """The bounded outcome of a comparison; ``samples`` is empty unless a limit was requested."""

    plan: ComparisonPlan
    left: SideStats | None
    right: SideStats | None
    reasons: tuple[str, ...] = ()
    missing_in_right: int = 0
    extra_in_right: int = 0
    matched_keys: int = 0
    rows_with_differences: int = 0
    columns: tuple[ColumnResult, ...] = ()
    samples: Mapping[str, list[object]] = field(default_factory=dict)
    sample_limit: int | None = None
    numeric_severity: bool = False

    @property
    def status(self) -> str:
        """``invalid`` when the comparison cannot be trusted, else ``identical`` or ``different``."""
        if self.reasons:
            return INVALID
        different = self.missing_in_right or self.extra_in_right or self.rows_with_differences
        return DIFFERENT if different else IDENTICAL

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        data: dict[str, object] = {
            "columns": [c.to_dict(severity=self.numeric_severity) for c in self.columns],
            "extra_in_right": self.extra_in_right,
            "left": self.left.to_dict() if self.left else None,
            "matched_keys": self.matched_keys,
            "missing_in_right": self.missing_in_right,
            "plan": self.plan.to_dict(),
            "reasons": list(self.reasons),
            "right": self.right.to_dict() if self.right else None,
            "rows_with_differences": self.rows_with_differences,
            "sample_limit": self.sample_limit,
            "samples": {k: [jsonable(v) for v in vs] for k, vs in sorted(self.samples.items())},
            "status": self.status,
            "version": REPORT_VERSION,
        }
        if self.numeric_severity:
            data["numeric_severity"] = True
        return data

    def to_json(self) -> str:
        """Return stable, sorted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ComparisonReport:
        """Rebuild a report from :meth:`to_dict` output."""
        if data.get("version") != REPORT_VERSION:
            msg = f"unsupported comparison report version {data.get('version')!r}; expected {REPORT_VERSION}"
            raise QualityError(msg)
        return cls(
            plan=ComparisonPlan.from_dict(data["plan"]),
            left=SideStats(**data["left"]) if data["left"] else None,
            right=SideStats(**data["right"]) if data["right"] else None,
            reasons=tuple(data["reasons"]),
            missing_in_right=data["missing_in_right"],
            extra_in_right=data["extra_in_right"],
            matched_keys=data["matched_keys"],
            rows_with_differences=data["rows_with_differences"],
            columns=tuple(
                ColumnResult(c["name"], c["mismatches"], c.get("max_abs_deviation")) for c in data["columns"]
            ),
            samples={k: list(v) for k, v in data["samples"].items()},
            sample_limit=data["sample_limit"],
            numeric_severity=bool(data.get("numeric_severity", False)),
        )

    def to_text(self) -> str:
        """Return a short human-readable summary."""
        lines = [f"comparison {self.plan.left} vs {self.plan.right}: {self.status}"]
        lines += [f"  invalid: {reason}" for reason in self.reasons]
        if self.left and self.right:
            lines.append(f"  rows: {self.left.rows} vs {self.right.rows}; matched keys: {self.matched_keys}")
            lines.append(f"  missing in right: {self.missing_in_right}; extra in right: {self.extra_in_right}")
            lines.append(f"  rows with differences: {self.rows_with_differences}")
        for c in self.columns:
            if c.mismatches:
                extra = f"; max abs deviation {c.max_abs_deviation}" if c.max_abs_deviation is not None else ""
                lines.append(f"  column {c.name}: {c.mismatches} mismatch(es){extra}")
        lines += [f"  {d.severity} {d.code}: {d.message}" for d in self.plan.diagnostics]
        return "\n".join(lines)


def invalid_reasons(left: SideStats, right: SideStats, plan: ComparisonPlan) -> tuple[str, ...]:
    """List why duplicate, null or blocked-plan findings make the comparison invalid."""
    reasons = list(blocked_reasons(plan))
    for label, side in ((plan.left, left), (plan.right, right)):
        if side.duplicate_key_groups:
            reasons.append(
                f"{label}: {side.duplicate_key_groups} duplicate key group(s) over {side.duplicate_key_rows} row(s)"
            )
        if side.null_key_rows:
            reasons.append(f"{label}: {side.null_key_rows} row(s) with a null key")
    return tuple(reasons)


def blocked_reasons(plan: ComparisonPlan) -> tuple[str, ...]:
    """Return the blocking plan diagnostics as invalidity reasons."""
    return tuple(f"plan: {d.message}" for d in plan.diagnostics if d.severity == "error")
