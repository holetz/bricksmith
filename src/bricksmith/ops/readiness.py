"""Freshness, completeness and input readiness: pure functions over table metadata from an injectable inspector."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from bricksmith.ops.events import MISSING_INPUT, PLATFORM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

READY = "ready"
STALE = "stale"
INCOMPLETE = "incomplete"
MISSING = "missing"
UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TableStats:
    """What an inspector knows about one table; ``None`` means not determinable (never guessed)."""

    name: str
    exists: bool = True
    row_count: int | None = None
    last_modified: datetime | None = None
    null_counts: Mapping[str, int] | None = None
    columns: tuple[str, ...] = ()
    note: str = ""


class TableInspector(Protocol):
    """Reads table metadata; ``null_columns`` are the columns whose null counts are needed."""

    def inspect(self, name: str, *, null_columns: Sequence[str] = ()) -> TableStats:
        """Return the statistics of ``name`` (``exists=False`` when it is not there)."""
        ...


@dataclass(frozen=True)
class InputSpec:
    """Readiness requirements of one input table (a physical name)."""

    table: str
    max_age_seconds: float | None = None
    min_rows: int | None = None
    required_columns: tuple[str, ...] = ()
    max_null_fraction: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> InputSpec:
        """Build a spec from a TOML/JSON table."""
        return cls(
            table=str(data["table"]),
            max_age_seconds=None if data.get("max_age_seconds") is None else float(data["max_age_seconds"]),
            min_rows=None if data.get("min_rows") is None else int(data["min_rows"]),
            required_columns=tuple(data.get("required_columns", ())),
            max_null_fraction={str(k): float(v) for k, v in dict(data.get("max_null_fraction", {})).items()},
        )


@dataclass(frozen=True)
class Finding:
    """One evaluated requirement of one input."""

    table: str
    check: str
    status: str
    message: str
    observed: object = None
    expected: object = None

    @property
    def error_class(self) -> str | None:
        """The telemetry class of a non-ready finding: missing/stale/incomplete inputs, or unavailable metadata."""
        if self.status == READY:
            return None
        return PLATFORM if self.status == UNAVAILABLE else MISSING_INPUT

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "check": self.check,
            "error_class": self.error_class,
            "expected": self.expected,
            "message": self.message,
            "observed": self.observed,
            "status": self.status,
            "table": self.table,
        }


@dataclass(frozen=True)
class ReadinessReport:
    """All findings; the inputs are ready only when every finding is ``ready``."""

    findings: tuple[Finding, ...]

    @property
    def ready(self) -> bool:
        """Tell whether every requirement holds."""
        return all(f.status == READY for f in self.findings)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"findings": [f.to_dict() for f in self.findings], "ready": self.ready}

    def to_text(self) -> str:
        """Return one line per finding."""
        lines = [f"{f.status:11} {f.table}  {f.check}: {f.message}" for f in self.findings]
        lines.append("ready" if self.ready else "NOT ready")
        return "\n".join(lines)

    def to_json(self) -> str:
        """Return sorted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


def evaluate_freshness(stats: TableStats, max_age_seconds: float, now: datetime) -> Finding:
    """Compare the last data change to ``now``; an unknown change time is ``unavailable``, not stale."""
    if not stats.exists:
        return Finding(stats.name, "freshness", MISSING, "table does not exist", expected=max_age_seconds)
    if stats.last_modified is None:
        return Finding(
            stats.name, "freshness", UNAVAILABLE, stats.note or "last modification time is not available",
            expected=max_age_seconds,
        )  # fmt: skip
    age = (now - stats.last_modified).total_seconds()
    ok = age <= max_age_seconds
    return Finding(
        stats.name, "freshness", READY if ok else STALE,
        f"age {age:.0f}s {'within' if ok else 'exceeds'} {max_age_seconds:.0f}s",
        observed=round(age, 3), expected=max_age_seconds,
    )  # fmt: skip


def evaluate_completeness(stats: TableStats, spec: InputSpec) -> tuple[Finding, ...]:
    """Evaluate row count, required columns and null fractions."""
    name = stats.name
    if not stats.exists:
        return (Finding(name, "completeness", MISSING, "table does not exist"),)
    out: list[Finding] = []
    if spec.min_rows is not None:
        if stats.row_count is None:
            out.append(Finding(name, "min_rows", UNAVAILABLE, "row count is not available", expected=spec.min_rows))
        else:
            ok = stats.row_count >= spec.min_rows
            out.append(
                Finding(name, "min_rows", READY if ok else INCOMPLETE, f"{stats.row_count} rows, need {spec.min_rows}",
                        stats.row_count, spec.min_rows)
            )  # fmt: skip
    if spec.required_columns:
        missing = [c for c in spec.required_columns if c not in stats.columns]
        out.append(
            Finding(name, "required_columns", INCOMPLETE if missing else READY,
                    f"missing columns {missing}" if missing else "all required columns present",
                    observed=list(stats.columns), expected=list(spec.required_columns))
        )  # fmt: skip
    for column, limit in sorted(spec.max_null_fraction.items()):
        nulls = None if stats.null_counts is None else stats.null_counts.get(column)
        if nulls is None or not stats.row_count:
            out.append(
                Finding(name, f"null_fraction[{column}]", UNAVAILABLE, "null fraction is not available", expected=limit)
            )
            continue
        fraction = nulls / stats.row_count
        ok = fraction <= limit
        out.append(
            Finding(name, f"null_fraction[{column}]", READY if ok else INCOMPLETE,
                    f"{fraction:.4f} of {column} null, limit {limit}", round(fraction, 6), limit)
        )  # fmt: skip
    return tuple(out)


def evaluate_readiness(specs: Sequence[InputSpec], inspector: TableInspector, now: datetime) -> ReadinessReport:
    """Inspect every input once and evaluate freshness and completeness; an inspector error is ``unavailable``."""
    findings: list[Finding] = []
    for spec in specs:
        try:
            stats = inspector.inspect(spec.table, null_columns=tuple(sorted(spec.max_null_fraction)))
        except Exception as error:  # noqa: BLE001 - the inspector is platform code
            findings.append(
                Finding(spec.table, "inspect", UNAVAILABLE, f"metadata could not be read: {type(error).__name__}")
            )
            continue
        if spec.max_age_seconds is not None:
            findings.append(evaluate_freshness(stats, spec.max_age_seconds, now))
        findings.extend(evaluate_completeness(stats, spec))
        if not findings or findings[-1].table != spec.table:
            findings.append(Finding(spec.table, "exists", READY if stats.exists else MISSING,
                                    "table exists" if stats.exists else "table does not exist"))  # fmt: skip
    return ReadinessReport(tuple(findings))
