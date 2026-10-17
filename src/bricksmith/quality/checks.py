"""Data checks: grain, join cardinality, nullability, accepted values, completeness and freshness.

A check is a small serializable declaration. It runs as an ordinary task (``require_check`` inside a task of
kind ``check``) or as a write condition (``condition_sql`` for the storage ``input_checks``/``target_checks``),
and always yields a reportable :class:`CheckResult` of ``passed``, ``failed`` or ``unavailable`` with a reason.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.quality.errors import CheckFailedError, QualityError
from bricksmith.quality.report import check_sample_limit, jsonable
from bricksmith.storage.sql import INPUT, key_match, literal, quote

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Mapping, Sequence

PASSED, FAILED, UNAVAILABLE = "passed", "failed", "unavailable"
KINDS = ("grain", "cardinality", "nullability", "accepted_values", "completeness", "freshness")
EXPECTATIONS = ("one_to_one", "one_to_many", "many_to_one", "many_to_many")


def _finite_non_negative(value: object) -> bool:
    """Return whether value is a real number (not a bool) that is finite and at least zero."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _number(value: object, field_name: str) -> float:
    """Convert a mapping value to float, raising :class:`QualityError` for anything that is not a real number."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        msg = f"{field_name} must be a number, got {value!r}"
        raise QualityError(msg)
    try:
        return float(value)
    except (ValueError, OverflowError):
        msg = f"{field_name} must be a number, got {value!r}"
        raise QualityError(msg) from None


@dataclass(frozen=True)
class Check:
    """A declared check. Use the constructors (:meth:`grain`, :meth:`freshness`, ...) rather than the fields."""

    kind: str
    name: str
    columns: tuple[str, ...] = ()
    values: tuple[object, ...] = ()
    expect: str = "one_to_one"
    require_match: bool = False
    allow_null: bool = False
    min_ratio: float = 1.0
    max_age_seconds: float = 0.0

    def __post_init__(self) -> None:
        """Validate the declaration."""
        if self.kind not in KINDS:
            msg = f"unknown check kind {self.kind!r}; expected one of {', '.join(KINDS)}"
            raise QualityError(msg)
        if not self.columns:
            msg = f"check {self.name!r} needs at least one column"
            raise QualityError(msg)
        if self.kind == "accepted_values" and (len(self.columns) != 1 or not self.values):
            msg = "accepted_values needs exactly one column and at least one value"
            raise QualityError(msg)
        if self.kind == "freshness" and (len(self.columns) != 1 or not _finite_non_negative(self.max_age_seconds)):
            msg = "freshness needs exactly one column and a finite, non-negative max_age_seconds"
            raise QualityError(msg)
        for flag in ("require_match", "allow_null"):
            if not isinstance(getattr(self, flag), bool):
                msg = f"{flag} must be a boolean, got {getattr(self, flag)!r}"
                raise QualityError(msg)
        if self.expect not in EXPECTATIONS:
            msg = f"unknown cardinality {self.expect!r}; expected one of {', '.join(EXPECTATIONS)}"
            raise QualityError(msg)
        if not 0 <= self.min_ratio <= 1:
            msg = "min_ratio must be between 0 and 1"
            raise QualityError(msg)

    @classmethod
    def grain(cls, *columns: str, name: str = "") -> Check:
        """Rows are unique on ``columns`` and no key is null."""
        return cls("grain", name or f"grain({','.join(columns)})", columns)

    @classmethod
    def cardinality(cls, *keys: str, expect: str = "one_to_one", require_match: bool = False, name: str = "") -> Check:
        """Require that joining the data to another relation on ``keys`` has the ``expect`` shape.

        ``one_to_many`` means unique keys on the data side, ``many_to_one`` on the other side;
        ``require_match`` also demands that every data key exists in the other relation.
        """
        return cls(
            "cardinality", name or f"{expect}({','.join(keys)})", keys, expect=expect, require_match=require_match
        )

    @classmethod
    def not_null(cls, *columns: str, name: str = "") -> Check:
        """No null in any of ``columns``."""
        return cls("nullability", name or f"not_null({','.join(columns)})", columns)

    @classmethod
    def accepted_values(
        cls, column: str, values: Sequence[object], *, allow_null: bool = False, name: str = ""
    ) -> Check:
        """Every value of ``column`` is in ``values``; a null passes only with ``allow_null``."""
        return cls(
            "accepted_values", name or f"accepted_values({column})", (column,), tuple(values), allow_null=allow_null
        )

    @classmethod
    def completeness(cls, *columns: str, min_ratio: float = 1.0, name: str = "") -> Check:
        """Require every column's non-null share to reach ``min_ratio``; an empty dataset is unavailable."""
        return cls("completeness", name or f"completeness({','.join(columns)})", columns, min_ratio=min_ratio)

    @classmethod
    def freshness(cls, column: str, *, max_age_seconds: float, name: str = "") -> Check:
        """Require the newest value of ``column`` (date/timestamp) to be at most ``max_age_seconds`` old."""
        return cls("freshness", name or f"freshness({column})", (column,), max_age_seconds=max_age_seconds)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "allow_null": self.allow_null,
            "columns": list(self.columns),
            "expect": self.expect,
            "kind": self.kind,
            "max_age_seconds": self.max_age_seconds,
            "min_ratio": self.min_ratio,
            "name": self.name,
            "require_match": self.require_match,
            "values": [jsonable(v) for v in self.values],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Check:
        """Build a check from a mapping; only ``kind`` and ``columns`` are required."""
        known = {
            "kind",
            "name",
            "columns",
            "values",
            "expect",
            "require_match",
            "allow_null",
            "min_ratio",
            "max_age_seconds",
        }
        extra = sorted(set(data) - known)
        if extra:
            msg = f"unknown check field(s): {', '.join(extra)}"
            raise QualityError(msg)
        if "kind" not in data:
            msg = "a check needs kind="
            raise QualityError(msg)
        columns = data.get("columns", ())
        columns = (columns,) if isinstance(columns, str) else tuple(columns)
        kind = str(data["kind"])
        return cls(
            kind,
            str(data.get("name") or f"{kind}({','.join(columns)})"),
            columns,
            tuple(data.get("values", ())),
            str(data.get("expect", "one_to_one")),
            data.get("require_match", False),
            data.get("allow_null", False),
            float(data.get("min_ratio", 1.0)),
            _number(data.get("max_age_seconds", 0.0), "max_age_seconds"),
        )


@dataclass(frozen=True)
class CheckResult:
    """The reportable outcome of one check."""

    name: str
    kind: str
    status: str
    reason: str = ""
    observed: Mapping[str, object] = field(default_factory=dict)
    samples: tuple[object, ...] = ()

    @property
    def passed(self) -> bool:
        """Tell whether the check passed."""
        return self.status == PASSED

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "kind": self.kind,
            "name": self.name,
            "observed": {k: jsonable(v) for k, v in sorted(self.observed.items())},
            "reason": self.reason,
            "samples": [jsonable(s) for s in self.samples],
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CheckResult:
        """Rebuild a result from :meth:`to_dict` output."""
        return cls(
            data["name"], data["kind"], data["status"], data["reason"], dict(data["observed"]), tuple(data["samples"])
        )


def _unavailable(check: Check, reason: str) -> CheckResult:
    return CheckResult(check.name, check.kind, UNAVAILABLE, reason)


def _verdict(
    check: Check, problems: list[str], observed: Mapping[str, object], samples: Sequence[object] = ()
) -> CheckResult:
    return CheckResult(
        check.name, check.kind, FAILED if problems else PASSED, "; ".join(problems), observed, tuple(samples)
    )


def _is_frame(data: object) -> bool:
    return hasattr(data, "groupBy") and hasattr(data, "columns")


def _columns_of(data: Any) -> set[str]:  # noqa: ANN401
    if _is_frame(data):
        return set(data.columns)
    return {c for row in data for c in row}


def run_check(
    check: Check,
    data: Any,  # noqa: ANN401 - Spark DataFrame or list of dict rows
    other: Any = None,  # noqa: ANN401
    *,
    sample_limit: int | None = None,
    now: dt.datetime | None = None,
) -> CheckResult:
    """Evaluate ``check`` on a Spark DataFrame or a list of dict rows; ``other`` serves cardinality only.

    ``now`` (timezone-aware, default current UTC time) fixes the reference instant of freshness. Samples are
    returned only for an explicit ``sample_limit``.
    """
    limit = check_sample_limit(sample_limit)
    if now is not None and now.tzinfo is None:
        msg = "now must be timezone-aware"
        raise QualityError(msg)
    if check.kind == "cardinality" and other is None:
        return _unavailable(check, "cardinality needs the other relation")
    for label, source in (("data", data), ("other", other if check.kind == "cardinality" else None)):
        if source is None:
            continue
        missing = sorted(set(check.columns) - _columns_of(source)) if (_is_frame(source) or source) else []
        if missing:
            return _unavailable(check, f"{label} has no column(s): {', '.join(missing)}")
    if _is_frame(data):
        from bricksmith.quality import checks_spark  # noqa: PLC0415 - Spark helpers load lazily

        return checks_spark.run(check, data, other, limit, now)
    from bricksmith.quality import checks_rows  # noqa: PLC0415

    return checks_rows.run(check, list(data), None if other is None else list(other), limit, now)


def require_check(check: Check, data: Any, other: Any = None, **options: Any) -> CheckResult:  # noqa: ANN401 - see run_check
    """Run ``check`` inside a task of kind ``check``: raise unless it passed (``allow_unavailable`` tolerates it)."""
    allow = bool(options.pop("allow_unavailable", False))
    result = run_check(check, data, other, **options)
    if result.status == FAILED or (result.status == UNAVAILABLE and not allow):
        msg = f"check {result.name!r} {result.status}: {result.reason}"
        raise CheckFailedError(msg)
    return result


def condition_sql(check: Check, relation: str = INPUT, other: str | None = None) -> str:
    """Render ``check`` as a SQL boolean for the storage ``input_checks`` / ``target_checks`` hooks.

    Those hooks require the expression to hold for every row. Row-level checks (nullability, accepted values)
    are plain predicates; dataset-level checks are scalar subqueries over ``relation`` (the input placeholder
    by default, or a quoted target name for ``target_checks``) and are constant across rows. An empty
    relation passes row-level checks vacuously and trivially satisfies the hook; a condition cannot report
    ``unavailable``, so completeness and freshness of missing data evaluate to false there.
    """
    cols = check.columns
    if check.kind == "nullability":
        return " AND ".join(f"{quote(c)} IS NOT NULL" for c in cols)
    if check.kind == "accepted_values":
        column = quote(cols[0])
        listed = f"{column} IN ({', '.join(literal(v) for v in check.values)})"
        matched = f"coalesce({listed}, false)"
        return f"({matched} OR {column} IS NULL)" if check.allow_null else matched
    if check.kind == "grain":
        return f"({_dup_groups(relation, cols)}) = 0"
    if check.kind == "completeness":
        parts = [f"(SELECT count({quote(c)}) / count(*) FROM {relation}) >= {check.min_ratio!r}" for c in cols]
        return " AND ".join(parts)
    if check.kind == "freshness":
        column = quote(cols[0])
        return (
            f"(SELECT unix_timestamp(CAST(max({column}) AS TIMESTAMP)) FROM {relation}) "
            f">= unix_timestamp(current_timestamp()) - {check.max_age_seconds!r}"
        )
    if other is None:
        msg = "a cardinality condition needs the other relation"
        raise QualityError(msg)
    terms: list[str] = []
    if check.expect in ("one_to_one", "one_to_many"):
        terms.append(_dup_groups(relation, cols))
    if check.expect in ("one_to_one", "many_to_one"):
        terms.append(_dup_groups(other, cols))
    if check.require_match:
        join = key_match(cols, "l", "r")
        terms.append(f"SELECT count(*) FROM {relation} AS l LEFT ANTI JOIN {other} AS r ON {join}")
    return " AND ".join(f"({t}) = 0" for t in terms) if terms else "true"


def _dup_groups(relation: str, columns: Sequence[str]) -> str:
    cols = ", ".join(quote(c) for c in columns)
    nulls = " OR ".join(f"{quote(c)} IS NULL" for c in columns)
    return f"SELECT count(*) FROM (SELECT 1 FROM {relation} GROUP BY {cols} HAVING count(*) > 1 OR {nulls})"


def evaluate_checks(
    checks: Sequence[Check],
    data: Any,  # noqa: ANN401 - Spark DataFrame or list of dict rows
    other: Any = None,  # noqa: ANN401 - second relation of the same kind
    **options: Any,  # noqa: ANN401
) -> tuple[CheckResult, ...]:
    """Run several checks against the same data and return their results in order."""
    return tuple(run_check(c, data, other, **options) for c in checks)


def load_checks(text: str, *, fmt: str = "json") -> tuple[Check, ...]:
    """Parse checks from JSON (a list, or ``{"checks": [...]}``) or TOML (``[[check]]`` tables)."""
    if fmt == "toml":
        import tomllib  # noqa: PLC0415

        raw: Any = tomllib.loads(text).get("check", [])
    else:
        raw = json.loads(text)
        raw = raw.get("checks", []) if isinstance(raw, dict) else raw
    if not isinstance(raw, list):
        msg = "checks must be a list"
        raise QualityError(msg)
    return tuple(Check.from_dict(item) for item in raw)
