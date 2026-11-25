"""Period profiles: bin counts, weights and per-bin moments per period, segment and column (pure engine).

A profile row is the smallest additive unit of the stability history: shares, PSI, null and unseen rates,
means and standard deviations of any group of periods are sums of profile rows. The Spark engine in
:mod:`bricksmith.monitor.stability.spark` produces identical rows.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.bins import ABOVE, BELOW, NULL, UNSEEN, ZERO, BinSpec, category_text, fit_bins, is_null
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.infer import Inference, infer_columns, python_type
from bricksmith.monitor.reference import Reference

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from bricksmith.monitor.stability.config import StabilityConfig

ALL_SEGMENTS = ""
ALL_LABEL = "(all)"
NULL_TEXT = "(null)"
OTHER_LABEL = "(other)"
_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_COMPACT = re.compile(r"^(\d{4})(\d{2})(\d{2})?$")


def period_text(value: object) -> str | None:
    """Return the text of a period value (``str``, booleans as ``true``/``false``) or ``None`` when null."""
    return None if is_null(value) else category_text(value)


def period_date(text: str) -> str | None:
    """Return the ISO date a period text starts on (``YYYY-MM-DD``, ``YYYY-MM``, ``YYYYMM``, ``YYYYMMDD``)."""
    found = _DAY.match(text) or _MONTH.match(text) or _COMPACT.match(text)
    if not found:
        return None
    groups = found.groups()
    year, month = int(groups[0]), int(groups[1])
    day = int(groups[2]) if len(groups) > 2 and groups[2] else 1  # noqa: PLR2004 - year, month, day
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def segment_text(value: object) -> str:
    """Return the text of a segment value (``(null)`` for null)."""
    return NULL_TEXT if is_null(value) else category_text(value)


def segment_key(columns: Sequence[str], values: Sequence[object]) -> str:
    """Return the canonical segment key ``col=value,col2=value2`` (``""`` for the whole population)."""
    return ",".join(f"{c}={segment_text(v)}" for c, v in zip(columns, values, strict=True))


def segment_label(key: str) -> str:
    """Return the readable label of a segment key (``(all)`` for the whole population)."""
    return key or ALL_LABEL


def _number(value: float) -> str:
    return f"{value:g}"


def bin_labels(spec: BinSpec) -> dict[str, tuple[int, str]]:
    """Return ``bin id -> (order, readable label)`` in the spec's stable order."""
    labels: dict[str, tuple[int, str]] = {}
    for order, bin_id in enumerate(spec.bin_ids()):
        if bin_id == NULL:
            text = "null"
        elif bin_id == UNSEEN:
            text = OTHER_LABEL
        elif bin_id == ZERO:
            text = "= 0"
        elif bin_id == BELOW:
            text = f"< {_number(spec.edges[0])}"
        elif bin_id == ABOVE:
            text = f"> {_number(spec.edges[-1])}"
        elif bin_id.startswith("cat:"):
            text = bin_id[4:]
        else:
            i = int(bin_id.split("_")[1])
            left = "[" if i == 0 else "("
            text = f"{left}{_number(spec.edges[i])}, {_number(spec.edges[i + 1])}]"
        labels[bin_id] = (order, text)
    return labels


@dataclass
class ProfileInfo:
    """What a profiling pass saw beyond the rows: null periods and truncated segment values."""

    rows: int = 0
    null_period_rows: int = 0
    truncated: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {"null_period_rows": self.null_period_rows, "rows": self.rows, "truncated": dict(self.truncated)}


def segment_sets(config: StabilityConfig) -> tuple[tuple[str, ...], ...]:
    """Return the segment column sets to profile; the whole population ``()`` comes first."""
    return ((), *config.segments)


def _columns_of(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        seen.update(dict.fromkeys(row))
    return list(seen)


def resolve_columns(
    config: StabilityConfig, types: Mapping[str, str], distinct: Mapping[str, int]
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Return ``(bin configuration per monitored column, reason per skipped column)``.

    Explicit columns must exist in ``types``; ``auto`` infers them, skipping excluded and unsupported ones.
    """
    for needed in (config.period, config.weight, *(c for s in config.segments for c in s)):
        if needed and needed not in types:
            msg = f"stability: column {needed!r} is not in the monitored table"
            raise MonitorError(msg)
    if config.columns is not None:
        missing = sorted(set(config.columns) - set(types))
        if missing:
            msg = f"stability: monitored columns not in the table: {', '.join(missing)}"
            raise MonitorError(msg)
        inferred = infer_columns(
            {c: types[c] for c in config.columns}, distinct, exclude=config.excluded(), options=config.inference
        )
        chosen = {c: cfg or inferred.columns.get(c, {}) for c, cfg in config.columns.items()}
        empty = sorted(c for c, cfg in chosen.items() if not cfg)
        if empty:
            reasons = ", ".join(f"{c} ({inferred.skipped.get(c, 'unsupported')})" for c in empty)
            msg = f"stability: cannot infer bins for {reasons}; give an explicit bin configuration"
            raise MonitorError(msg)
        return chosen, {}
    found: Inference = infer_columns(types, distinct, exclude=config.excluded(), options=config.inference)
    if not found.columns:
        msg = "stability: no column can be monitored (all were excluded or unsupported)"
        raise MonitorError(msg)
    return found.columns, found.skipped


def fit_rows_binning(  # noqa: PLR0913 - one keyword per reference field
    rows: Sequence[Mapping[str, Any]],
    config: StabilityConfig,
    fit_periods: Sequence[str],
    *,
    dataset: str,
    version: int,
    source: str = "",
) -> tuple[Reference, dict[str, str]]:
    """Fit a frozen binning (a versioned :class:`Reference`) on the rows of ``fit_periods``."""
    wanted = set(fit_periods)
    scoped = [r for r in rows if period_text(r.get(config.period)) in wanted]
    if not scoped:
        msg = f"stability: no rows in the fit periods {sorted(wanted)}"
        raise MonitorError(msg)
    names = _columns_of(rows)
    types = {c: python_type(r.get(c) for r in rows) for c in names}
    distinct = {c: len({category_text(r[c]) for r in scoped if not is_null(r.get(c))}) for c in names}
    columns, skipped = resolve_columns(config, types, distinct)
    specs = tuple(fit_bins(c, Counter(r.get(c) for r in scoped), columns[c]) for c in sorted(columns))
    return _reference(config, specs, len(scoped), fit_periods, dataset=dataset, version=version, source=source), skipped


def _reference(  # noqa: PLR0913 - the reference fields
    config: StabilityConfig,
    specs: tuple[BinSpec, ...],
    fit_rows: int,
    fit_periods: Sequence[str],
    *,
    dataset: str,
    version: int,
    source: str,
) -> Reference:
    ordered = sorted(fit_periods)
    return Reference(
        name=dataset,
        version=version,
        columns=specs,
        fit_source=source,
        fit_rows=fit_rows,
        fit_window={
            "column": config.period,
            "end": ordered[-1],
            "periods": ",".join(ordered),
            "start": ordered[0],
        },
        time=config.period,
    )


def periods_of_rows(rows: Iterable[Mapping[str, Any]], period: str) -> list[str]:
    """Return the sorted distinct period texts of ``rows`` (null periods are left out)."""
    found = {period_text(r.get(period)) for r in rows}
    return sorted(p for p in found if p is not None)


def fit_periods_of(binning: Reference) -> tuple[str, ...]:
    """Return the periods a stability binning was fitted on."""
    text = binning.fit_window.get("periods", "")
    return tuple(p for p in text.split(",") if p)


def _numeric_value(spec: BinSpec, value: object, bin_id: str) -> float | None:
    if spec.kind != "numeric" or bin_id == NULL:
        return None
    return float(value)  # type: ignore[arg-type]


def _weight(row: Mapping[str, Any], weight: str) -> float:
    value = row.get(weight)
    return 0.0 if is_null(value) else float(value)  # pyright: ignore[reportArgumentType] - not null here


def kept_segments(
    ranked: Mapping[str, int], limit: int, carried: Mapping[str, str], *, label: str, info: ProfileInfo
) -> set[str]:
    """Return the kept keys of one segment set: the top ``limit`` of ``ranked`` plus the carried ones it holds.

    ``ranked`` counts the rows of every key over the periods the run loads (ties go to the smaller key);
    ``carried`` names the segments that already have stored history, which stay kept even outside the top.
    """
    ordered = sorted(ranked.items(), key=lambda item: (-item[1], item[0]))
    keep = {key for key, _ in ordered[:limit]} | {key for key in ranked if key in carried}
    if len(ordered) > len(keep):
        info.truncated[label] = len(ordered) - len(keep)
    return keep


def first_periods(found: Mapping[str, str], carried: Mapping[str, str]) -> dict[str, str]:
    """Return the earliest period each kept or carried segment had rows in (loaded data or stored history)."""
    first = dict(carried)
    for key, period in found.items():
        first[key] = min(period, first.get(key, period))
    return first


def _rank(
    rows: Iterable[tuple[str, Mapping[str, Any]]], columns: tuple[str, ...]
) -> tuple[Counter[str], dict[str, str]]:
    """Return the row count and the earliest period of every segment key of ``columns``."""
    counts: Counter[str] = Counter()
    found: dict[str, str] = {}
    for period, row in rows:
        key = segment_key(columns, [row.get(c) for c in columns])
        counts[key] += 1
        found[key] = min(period, found.get(key, period))
    return counts, found


def _scope(
    rows: Iterable[Mapping[str, Any]], period_column: str, wanted: set[str], ranking: set[str], info: ProfileInfo
) -> tuple[list[tuple[str, Mapping[str, Any]]], list[tuple[str, Mapping[str, Any]]]]:
    """Return ``(rows of the profiled periods, rows of the ranking periods)``, each with its period text."""
    scoped: list[tuple[str, Mapping[str, Any]]] = []
    ranked: list[tuple[str, Mapping[str, Any]]] = []
    for row in rows:
        period = period_text(row.get(period_column))
        if period is None:
            info.null_period_rows += 1
            continue
        if period in wanted:
            scoped.append((period, row))
        if period in ranking:
            ranked.append((period, row))
    info.rows = len(scoped)
    return scoped, ranked


def profile_rows(  # noqa: PLR0913 - one keyword per profile field
    binning: Reference,
    rows: Iterable[Mapping[str, Any]],
    config: StabilityConfig,
    periods: Iterable[str],
    *,
    dataset: str,
    run_id: str = "",
    rank_periods: Iterable[str] | None = None,
    carried: Mapping[str, str] | None = None,
) -> tuple[list[dict[str, Any]], ProfileInfo]:
    """Profile ``periods`` of ``rows`` with the frozen ``binning`` (pure engine).

    Segments are ranked over ``rank_periods`` (the periods the run loads; ``periods`` by default) and
    ``carried`` (segment key -> earliest stored period with rows) stay kept; see :func:`kept_segments`.
    """
    wanted = set(periods)
    carried = carried or {}
    info = ProfileInfo()
    scoped, ranked_rows = _scope(rows, config.period, wanted, wanted | set(rank_periods or ()), info)
    totals: dict[tuple[str, str, str, str], list[float]] = {}
    first: dict[str, str] = {}
    for columns in segment_sets(config):
        keep: set[str] | None = None
        if columns:
            counts, found = _rank(ranked_rows, columns)
            keep = kept_segments(counts, config.max_segments, carried, label=",".join(columns), info=info)
            first |= first_periods({k: found[k] for k in keep}, {k: v for k, v in carried.items() if k in keep})
        for period, row in scoped:
            key = segment_key(columns, [row.get(c) for c in columns])
            if keep is not None and key not in keep:
                continue
            weight = _weight(row, config.weight) if config.weight else 0.0
            for spec in binning.columns:
                value = row.get(spec.column)
                bin_id = spec.assign(value)
                slot = totals.setdefault((period, key, spec.column, bin_id), [0, 0.0, 0.0, 0.0])
                slot[0] += 1
                slot[1] += weight
                x = _numeric_value(spec, value, bin_id)
                if x is not None:
                    slot[2] += x
                    slot[3] += x * x
    return complete_rows(binning, totals, config, dataset=dataset, run_id=run_id, first=first, periods=wanted), info


def complete_rows(  # noqa: PLR0913 - one keyword per profile field
    binning: Reference,
    totals: Mapping[tuple[str, str, str, str], Sequence[float]],
    config: StabilityConfig,
    *,
    dataset: str,
    run_id: str,
    first: Mapping[str, str] | None = None,
    periods: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Turn ``(period, segment, column, bin) -> [count, weight, sum, sum_sq]`` into rows; every bin is listed.

    A segment of ``first`` (segment key -> earliest period with rows) absent from a profiled period of ``periods``
    after that first period gets its rows at zero, so a vanished segment is judged with no rows; before its first
    period nothing is listed and it keeps ``no_baseline``.
    """
    found = {(p, s) for p, s, _, _ in totals}
    found |= {(p, s) for p in periods for s, since in (first or {}).items() if since < p}
    groups = sorted(found)
    out: list[dict[str, Any]] = []
    for period, segment in groups:
        for spec in binning.columns:
            for bin_id, (order, label) in bin_labels(spec).items():
                count, weight, total, squares = totals.get((period, segment, spec.column, bin_id), (0, 0.0, 0.0, 0.0))
                numeric = spec.kind == "numeric"
                out.append(
                    {
                        "dataset": dataset, "version": binning.version, "period": period,
                        "period_date": period_date(period), "segment": segment, "segment_label": segment_label(segment),
                        "column": spec.column, "bin": bin_id, "bin_order": order, "bin_label": label,
                        "count": int(count), "weight": float(weight) if config.weight else None,
                        "value_sum": float(total) if numeric else None,
                        "value_sum_sq": float(squares) if numeric else None, "run_id": run_id,
                    }
                )  # fmt: skip
    return out


__all__ = [
    "ALL_LABEL",
    "ALL_SEGMENTS",
    "ProfileInfo",
    "bin_labels",
    "complete_rows",
    "first_periods",
    "fit_periods_of",
    "fit_rows_binning",
    "kept_segments",
    "period_date",
    "period_text",
    "periods_of_rows",
    "profile_rows",
    "resolve_columns",
    "segment_key",
    "segment_label",
    "segment_sets",
]
