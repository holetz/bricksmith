"""Human marks on stability periods: accepted, excluded from the fit, a new regime, or invalid.

Periods are re-judged on every run, so what people decide about them lives in its own append-only table, like the
alert actions of :mod:`bricksmith.monitor.actions`. A person writes one row per decision (:func:`mark_row`); the
latest row of each selector ``(period, segment, column, metric)`` wins, and :func:`unmarked` cancels one. A run folds
the marks in force at its date into the judgment (:class:`MarkSet`) and hashes them into the judgment fingerprint
(:func:`marks_digest`), so a new mark re-judges the window deterministically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, NoReturn

from bricksmith.monitor.actions import order_key, utc_instant, utc_text
from bricksmith.monitor.errors import MonitorError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

ACCEPTED = "accepted"
EXCLUDED = "excluded"
REGIME_CHANGE = "regime_change"
INVALID = "invalid"
UNMARKED = "unmarked"
#: The marks a person sets; ``unmarked`` is the row that cancels one.
MARKS = (ACCEPTED, EXCLUDED, REGIME_CHANGE, INVALID)
#: The marks that hold for a whole period: baselines and the trend restart are period-wide, so no selector narrows them.
PERIOD_WIDE = (EXCLUDED, REGIME_CHANGE, INVALID)
SELECTORS = ("period", "segment", "column", "metric")
COLUMNS = (
    "mark_id", "dataset", "period", "segment", "column", "metric", "mark", "reason", "author", "valid_until",
    "created_at",
)  # fmt: skip

type Selector = tuple[str, str, str, str]


def _refuse(message: str) -> NoReturn:
    msg = f"stability mark: {message}"
    raise MonitorError(msg)


def mark_row(  # noqa: PLR0913 - one keyword per column a person writes
    *,
    dataset: str,
    period: str,
    mark: str,
    reason: str,
    author: str,
    created_at: str,
    segment: str = "",
    column: str = "",
    metric: str = "",
    valid_until: str = "",
) -> dict[str, Any]:
    """Return the validated row of a mark (``mark`` is one of :data:`MARKS` or :data:`UNMARKED`).

    ``reason`` and ``author`` are required; ``excluded``, ``regime_change`` and ``invalid`` hold for the whole period
    and refuse a selector. ``created_at`` must be ISO-8601 and is stored in UTC. ``mark_id`` hashes the content, so
    the same decision at the same instant is one row.
    """
    import hashlib  # noqa: PLC0415 - tiny

    if mark not in (*MARKS, UNMARKED):
        _refuse(f"mark must be one of {', '.join(MARKS)}; got {mark!r}")
    if not dataset.strip() or not period.strip():
        _refuse("dataset and period are required")
    if not reason.strip() or not author.strip():
        _refuse("reason and author are required")
    if mark in PERIOD_WIDE and (segment or column or metric):
        _refuse(f"{mark} holds for the whole period and takes no segment, column or metric")
    moment = utc_instant(created_at)
    if moment is None:
        _refuse(f"created_at must be an ISO-8601 timestamp, got {created_at!r}")
    try:
        until = date.fromisoformat(valid_until).isoformat() if valid_until else None
    except ValueError:
        _refuse(f"valid_until must be an ISO date (YYYY-MM-DD), got {valid_until!r}")
    created = utc_text(moment)
    identity = f"{dataset}|{period}|{segment}|{column}|{metric}|{mark}|{author}|{created}"
    return {
        "mark_id": hashlib.sha256(identity.encode()).hexdigest()[:16], "dataset": dataset.strip(),
        "period": period.strip(), "segment": segment, "column": column, "metric": metric, "mark": mark,
        "reason": reason.strip(), "author": author.strip(), "valid_until": until, "created_at": created,
    }  # fmt: skip


def selector_of(row: Mapping[str, Any]) -> Selector:
    """Return the selector ``(period, segment, column, metric)`` a mark row decides."""
    return tuple(str(row.get(c) or "") for c in SELECTORS)  # type: ignore[return-value] - four items


def latest_marks(rows: Iterable[Mapping[str, Any]], dataset: str | None = None) -> dict[Selector, dict[str, Any]]:
    """Return the latest row per selector, optionally of one dataset, ordered by UTC ``created_at`` then ``mark_id``."""
    latest: dict[Selector, dict[str, Any]] = {}
    for row in rows:
        if dataset is not None and row.get("dataset") != dataset:
            continue
        key = selector_of(row)
        held = latest.get(key)
        if held is None or order_key(row, "mark_id") > order_key(held, "mark_id"):
            latest[key] = dict(row)
    return latest


def holds(row: Mapping[str, Any], as_of: str) -> bool:
    """Tell whether a mark row is in force on ``as_of``: not an ``unmarked`` row, and not past its ``valid_until``."""
    until = row.get("valid_until")
    return row.get("mark") in MARKS and (until is None or str(until)[:10] >= as_of[:10])


def effective_marks(
    rows: Iterable[Mapping[str, Any]], as_of: str, dataset: str | None = None, *, since: str = ""
) -> list[dict[str, Any]]:
    """Return the marks in force on ``as_of``, sorted by selector; ``since`` drops marks of earlier periods."""
    found = latest_marks(rows, dataset)
    return [r for key, r in sorted(found.items()) if key[0] >= since and holds(r, as_of)]


def marks_digest(effective: Iterable[Mapping[str, Any]]) -> list[list[str]]:
    """Return what of the effective marks enters the judgment fingerprint (empty without marks)."""
    return sorted([*selector_of(r), str(r["mark"]), str(r["mark_id"])] for r in effective)


@dataclass(frozen=True)
class MarkSet:
    """The marks in force, indexed for the judgment: period-wide sets and the most specific mark of a result."""

    marks: tuple[dict[str, Any], ...] = ()
    excluded: frozenset[str] = field(init=False, default=frozenset())
    regimes: frozenset[str] = field(init=False, default=frozenset())
    invalid: dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        """Index the period-wide marks."""
        wide = [m for m in self.marks if m["mark"] in PERIOD_WIDE]
        object.__setattr__(self, "excluded", frozenset(m["period"] for m in wide if m["mark"] in (EXCLUDED, INVALID)))
        object.__setattr__(self, "regimes", frozenset(m["period"] for m in wide if m["mark"] == REGIME_CHANGE))
        object.__setattr__(self, "invalid", {m["period"]: str(m["reason"]) for m in wide if m["mark"] == INVALID})

    def __bool__(self) -> bool:
        """Tell whether any mark is in force."""
        return bool(self.marks)

    def of(self, row: Mapping[str, Any]) -> dict[str, Any] | None:
        """Return the most specific mark (then the latest) that covers a result row, or ``None``."""
        best: tuple[int, tuple[int, Any, str]] | None = None
        found: dict[str, Any] | None = None
        for mark in self.marks:
            if mark["period"] != row["period"]:
                continue
            if any(mark[c] and mark[c] != row.get(c) for c in SELECTORS[1:]):
                continue
            rank = (sum(1 for c in SELECTORS[1:] if mark[c]), order_key(mark, "mark_id"))
            if best is None or rank > best:
                best, found = rank, mark
        return found

    def accepted(self, row: Mapping[str, Any]) -> bool:
        """Tell whether a result row is covered by an ``accepted`` mark."""
        found = self.of(row)
        return found is not None and found["mark"] == ACCEPTED


__all__ = [
    "ACCEPTED",
    "COLUMNS",
    "EXCLUDED",
    "INVALID",
    "MARKS",
    "PERIOD_WIDE",
    "REGIME_CHANGE",
    "UNMARKED",
    "MarkSet",
    "effective_marks",
    "holds",
    "latest_marks",
    "mark_row",
    "marks_digest",
    "selector_of",
]
