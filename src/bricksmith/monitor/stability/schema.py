"""Schema drift: what columns a table had when its binning was fitted, and what changed since.

A schema snapshot is a tuple of ``(column, type, nullable)`` sorted by column, stored as JSON text on the binnings
and runs tables. Two schemas differ by added, dropped and retyped columns (nullability is recorded, not compared).
The drift becomes one table-level ``schema_change`` result per run; a monitored column absent from the table becomes
a ``missing_column`` result, and the profiling binning leaves it out so neither engine touches it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.bins import is_null
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.infer import python_type
from bricksmith.monitor.stability.config import MISSING_COLUMN, SCHEMA_CHANGE
from bricksmith.monitor.stability.metrics import TABLE, Context, judge, result_row

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from bricksmith.monitor.reference import Reference

type Schema = tuple[tuple[str, str, bool], ...]
BASELINE_KIND = "schema"


def rows_schema(rows: Sequence[Mapping[str, Any]]) -> Schema:
    """Return the schema of a list of rows: every key seen, its type name and whether any value is null."""
    names: dict[str, None] = {}
    for row in rows:
        names.update(dict.fromkeys(row))
    return tuple(
        (name, python_type(r.get(name) for r in rows), any(is_null(r.get(name)) for r in rows))
        for name in sorted(names)
    )


def schema_text(schema: Schema) -> str:
    """Return the stored text of a schema."""
    return json.dumps([list(item) for item in schema], separators=(",", ":"))


def parse_schema(text: object) -> Schema | None:
    """Return the schema stored as ``text``, or ``None`` when it is absent or unreadable."""
    if not isinstance(text, str) or not text:
        return None
    try:
        items = json.loads(text)
        return tuple((str(c), str(t), bool(n)) for c, t, n in items)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class Drift:
    """What changed between a reference schema and the current one."""

    added: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    retyped: tuple[tuple[str, str, str], ...] = ()

    @property
    def count(self) -> int:
        """Return how many columns changed."""
        return len(self.added) + len(self.dropped) + len(self.retyped)

    def reason(self) -> str:
        """Return the readable list of changes (``""`` when there is none)."""
        parts = []
        if self.added:
            parts.append(f"added: {', '.join(self.added)}")
        if self.dropped:
            parts.append(f"dropped: {', '.join(self.dropped)}")
        if self.retyped:
            parts.append("retyped: " + ", ".join(f"{c} ({old} -> {new})" for c, old, new in self.retyped))
        return "; ".join(parts)


def diff(reference: Schema, current: Schema, ignore: Collection[str] = ()) -> Drift:
    """Return the drift of ``current`` from ``reference``, leaving out the ``ignore`` columns."""
    before = {c: t for c, t, _ in reference if c not in ignore}
    after = {c: t for c, t, _ in current if c not in ignore}
    return Drift(
        added=tuple(sorted(set(after) - set(before))),
        dropped=tuple(sorted(set(before) - set(after))),
        retyped=tuple((c, before[c], after[c]) for c in sorted(set(before) & set(after)) if before[c] != after[c]),
    )


def missing_columns(binning: Reference, current: Schema) -> list[str]:
    """Return the monitored columns of ``binning`` that the table no longer has."""
    present = {c for c, _, _ in current}
    return [spec.column for spec in binning.columns if spec.column not in present]


def live_binning(binning: Reference, missing: Collection[str]) -> Reference:
    """Return ``binning`` without the ``missing`` columns: what the profile of this run covers."""
    if not missing:
        return binning
    kept = tuple(spec for spec in binning.columns if spec.column not in missing)
    if not kept:
        msg = f"stability: every monitored column is missing from the table: {', '.join(sorted(missing))}"
        raise MonitorError(msg)
    return replace(binning, columns=kept)


def schema_results(  # noqa: PLR0913 - one keyword per result input
    ctx: Context,
    period: str,
    *,
    drift: Drift | None,
    label: str,
    note: str,
    missing: Iterable[str],
    returned: Iterable[str],
) -> list[dict[str, Any]]:
    """Return the schema results of ``period``: ``schema_change`` (when ``drift`` is known) and ``missing_column``.

    ``label`` names the reference schema, ``note`` is appended to the reason of a drift. A ``returned`` column is
    reported at zero so that its open ``missing_column`` alert closes.
    """
    where = (period, "")
    out: list[dict[str, Any]] = []
    if drift is not None:
        value = float(drift.count)
        light = judge(ctx, SCHEMA_CHANGE, TABLE, value, where=where, rows=0, has_baseline=True)
        row = result_row(ctx, period=period, segment="", column=TABLE, metric=SCHEMA_CHANGE, value=value, light=light,
                         current=value, baseline=0.0)  # fmt: skip
        if drift.count:
            row["reason"] = f"{drift.reason()}{('; ' + note) if note else ''}"
        row["baseline_kind"], row["baseline_label"] = BASELINE_KIND, label
        out.append(row)
    for column, value in [*((c, 1.0) for c in sorted(missing)), *((c, 0.0) for c in sorted(returned))]:
        light = judge(ctx, MISSING_COLUMN, column, value, where=where, rows=0, has_baseline=True)
        row = result_row(ctx, period=period, segment="", column=column, metric=MISSING_COLUMN, value=value, light=light,
                         current=value, baseline=0.0)  # fmt: skip
        if value:
            row["reason"] = f"column {column} is not in the table"
        row["baseline_kind"], row["baseline_label"] = BASELINE_KIND, label
        out.append(row)
    return out


__all__ = [
    "BASELINE_KIND",
    "Drift",
    "Schema",
    "diff",
    "live_binning",
    "missing_columns",
    "parse_schema",
    "rows_schema",
    "schema_results",
    "schema_text",
]
