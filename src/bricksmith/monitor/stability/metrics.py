"""Stability results from stored profiles: baselines, metrics, traffic lights, trend checks and replayed alerts.

Everything here is pure and works on profile rows only, so results can be recomputed for any baseline without
reading the monitored table again. Definitions:

- ``psi``: population stability index of the period's bin shares against the baseline's (weights instead of
  counts when the declaration names a ``weight``), shares floored at ``epsilon``.
- ``null_rate`` / ``null_rate_delta``: share of the ``null`` bin and its difference from the baseline.
- ``unseen_rate`` / ``unseen_rate_delta``: share outside the fitted bins (new categories, values below or above
  the fitted range) and its difference from the baseline.
- ``mean`` / ``mean_shift``: mean of the non-null values; shift ``(mean - baseline mean) / baseline std``.
- ``row_count``, ``volume_change`` (rows / mean baseline rows per period - 1), ``weight_change`` likewise.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.bins import ABOVE, BELOW, NULL, UNSEEN
from bricksmith.monitor.limits import (
    COLORS,
    GREEN,
    HIGHER,
    INFORMATIONAL,
    NO_RULE,
    RED,
    YELLOW,
    Light,
    Limits,
    classify,
    rank,
)
from bricksmith.monitor.persistence import CLOSED, CRITICAL, OPEN, WARNING, AlertSpan, PersistencePolicy, alert_spans
from bricksmith.monitor.rules import INFORMATIONAL_REASON
from bricksmith.monitor.stability.config import (
    DISTRIBUTION_METRICS,
    FIXED,
    LAPLACE,
    MEAN,
    MEAN_SHIFT,
    NULL_RATE,
    NULL_RATE_DELTA,
    PREVIOUS,
    PSI,
    REVISION_PSI,
    ROW_COUNT,
    SAMPLE_SIZE,
    UNSEEN_RATE,
    UNSEEN_RATE_DELTA,
    VOLUME_CHANGE,
    WEIGHT_CHANGE,
)
from bricksmith.monitor.stability.profile import fit_periods_of, period_date, segment_label
from bricksmith.monitor.stats import chi2_ppf, psi_terms
from bricksmith.monitor.trend import keeps, trend_check

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

    from bricksmith.monitor.reference import Reference
    from bricksmith.monitor.stability.config import StabilityConfig

OUTSIDE = frozenset({UNSEEN, BELOW, ABOVE})
TABLE = ""
PERSISTENCE = "persistence"
TREND = "trend"

#: Significance of the yellow and red sampling-noise floors of a PSI (``psi_limits: sample_size``).
YELLOW_ALPHA = 0.05
RED_ALPHA = 0.01
PSI_METRICS = frozenset({PSI, REVISION_PSI})
LAPLACE_COUNT = 1.0

type Slot = tuple[int, float, float, float]  # count, weight, value sum, value sum of squares
type Key = tuple[str, str, str]  # segment, column, metric


@dataclass
class History:
    """Profile rows indexed for the metrics: bins per (segment, column) and period, rows and weights per period."""

    bins: dict[tuple[str, str], dict[str, dict[str, Slot]]] = field(default_factory=dict)
    rows: dict[str, dict[str, int]] = field(default_factory=dict)
    weights: dict[str, dict[str, float]] = field(default_factory=dict)
    periods: list[str] = field(default_factory=list)


def index_profiles(binning: Reference, profiles: Iterable[Mapping[str, Any]]) -> History:
    """Index the profile rows of one binning version (other versions are ignored)."""
    history = History()
    first = binning.columns[0].column
    found: set[str] = set()
    for row in profiles:
        if int(row["version"]) != binning.version:
            continue
        period, segment, column = str(row["period"]), str(row["segment"]), str(row["column"])
        slot: Slot = (
            int(row["count"] or 0),
            float(row.get("weight") or 0.0),
            float(row.get("value_sum") or 0.0),
            float(row.get("value_sum_sq") or 0.0),
        )
        history.bins.setdefault((segment, column), {}).setdefault(period, {})[str(row["bin"])] = slot
        found.add(period)
        if column == first:
            rows = history.rows.setdefault(segment, {})
            rows[period] = rows.get(period, 0) + slot[0]
            weights = history.weights.setdefault(segment, {})
            weights[period] = weights.get(period, 0.0) + slot[1]
    history.periods = sorted(found)
    return history


def baseline_periods(config: StabilityConfig, binning: Reference, periods: Sequence[str], period: str) -> list[str]:
    """Return the periods ``period`` is compared with, before checking which ones a segment has."""
    rule = config.baseline
    if rule.kind == FIXED:
        if rule.start:
            return [p for p in periods if rule.start <= p <= rule.end]
        return list(fit_periods_of(binning))
    index = periods.index(period)
    if rule.kind == PREVIOUS:
        return [periods[index - rule.lag]] if index - rule.lag >= 0 else []
    return list(periods[max(0, index - rule.periods) : index])


def _sum(slots: Iterable[Mapping[str, Slot]]) -> dict[str, list[float]]:
    total: dict[str, list[float]] = {}
    for bins in slots:
        for bin_id, slot in bins.items():
            acc = total.setdefault(bin_id, [0.0, 0.0, 0.0, 0.0])
            for i in range(4):
                acc[i] += slot[i]
    return total


@dataclass(frozen=True)
class _Stats:
    rows: float
    null_rate: float
    outside_rate: float
    mean: float | None
    std: float | None


def _stats(bins: Mapping[str, Sequence[float]], *, numeric: bool) -> _Stats:
    rows = sum(s[0] for s in bins.values())
    nulls = bins.get(NULL, (0, 0, 0, 0))[0]
    outside = sum(s[0] for b, s in bins.items() if b in OUTSIDE)
    present = rows - nulls
    if not numeric or present <= 0:
        return _Stats(rows, nulls / rows if rows else 0.0, outside / rows if rows else 0.0, None, None)
    total = sum(s[2] for b, s in bins.items() if b != NULL)
    squares = sum(s[3] for b, s in bins.items() if b != NULL)
    mean = total / present
    variance = max(squares / present - mean * mean, 0.0)
    return _Stats(rows, nulls / rows, outside / rows, mean, math.sqrt(variance))


@dataclass
class Computed:
    """Result rows of every period in the window, plus bin shares and contributions for the profile rows."""

    results: list[dict[str, Any]] = field(default_factory=list)
    shares: dict[tuple[str, str, str, str], tuple[float, float | None, float | None]] = field(default_factory=dict)


@dataclass
class _Context:
    config: StabilityConfig
    binning: Reference
    dataset: str
    run_id: str
    computed_at: str
    trend_history: dict[Key, list[float]] = field(default_factory=dict)
    settling: frozenset[str] = frozenset()
    gap: int = 0
    excluded: frozenset[str] = frozenset()
    red_periods: dict[str, set[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Sample:
    """The sizes a distribution metric rests on: current rows, baseline rows and the bins they are spread over."""

    current: float
    baseline: float
    bins: int


def sample_of(current: Mapping[str, Sequence[float]], baseline: Mapping[str, Sequence[float]]) -> Sample:
    """Return the :class:`Sample` of two bin maps (unweighted counts: sampling noise depends on rows)."""
    return Sample(sum(s[0] for s in current.values()), sum(s[0] for s in baseline.values()), len({*current, *baseline}))


def smoothing_of(config: StabilityConfig) -> float:
    """Return the pseudo-count added to every bin of a PSI (``0`` unless ``psi_smoothing: laplace``)."""
    return LAPLACE_COUNT if config.psi_smoothing == LAPLACE else 0.0


@lru_cache(maxsize=256)
def _chi2(bins: int, alpha: float) -> float:
    return chi2_ppf(1 - alpha, bins - 1)


def effective_limits(
    config: StabilityConfig, metric: str, limits: Limits | None, sample: Sample | None
) -> Limits | None:
    """Return ``limits`` raised to the sampling-noise floor of ``sample`` for a PSI under ``psi_limits: sample_size``.

    A PSI between two samples of the same population is about ``chi2(bins - 1) * (1/n + 1/m)``, so a limit below
    that is crossed by noise alone. The floor never lowers a declared limit.
    """
    if (
        limits is None or sample is None or config.psi_limits != SAMPLE_SIZE or metric not in PSI_METRICS
        or limits.direction != HIGHER or sample.bins < 2 or sample.current <= 0 or sample.baseline <= 0  # noqa: PLR2004
    ):  # fmt: skip
        return limits
    scale = 1 / sample.current + 1 / sample.baseline
    return replace(
        limits,
        green=max(limits.green, _chi2(sample.bins, YELLOW_ALPHA) * scale),
        red=max(limits.red, _chi2(sample.bins, RED_ALPHA) * scale),
    )


def _result(  # noqa: PLR0913 - one keyword per result field
    ctx: _Context,
    *,
    period: str,
    segment: str,
    column: str,
    metric: str,
    value: float | None,
    light: Light,
    current: float | None = None,
    baseline: float | None = None,
    rows: int = 0,
    baseline_rows: float | None = None,
    in_baseline: bool = False,
    sample: Sample | None = None,
) -> dict[str, Any]:
    found = ctx.config.resolve(
        dataset=ctx.dataset, column=column, segment=segment, metric=metric, period_date=period_date(period)
    )
    limits = None if found.informational else ctx.config.limits_of(found, metric, column)
    limits = effective_limits(ctx.config, metric, limits, sample)
    if found.informational and light.status in (*COLORS, NO_RULE):
        light = Light(INFORMATIONAL, INFORMATIONAL_REASON)
    row: dict[str, Any] = {
        "dataset": ctx.dataset, "version": ctx.binning.version, "period": period, "period_date": period_date(period),
        "segment": segment, "segment_label": segment_label(segment), "column": column, "metric": metric,
        "value": _finite(value), "current": _finite(current), "baseline": _finite(baseline), "status": light.status,
        "status_rank": light.rank, "reason": light.reason, "green_limit": limits.green if limits else None,
        "red_limit": limits.red if limits else None, "direction": limits.direction if limits else "",
        "rows": rows, "baseline_rows": _finite(baseline_rows), "in_baseline": in_baseline,
        "baseline_kind": ctx.config.baseline.kind, "baseline_label": ctx.config.baseline.label,
        "trend_status": "", "trend_expected": None, "trend_deviation": None,
        "trend_sigma": None, "trend_lower": None, "trend_upper": None,
        "baseline_gap": ctx.gap, "settling": period in ctx.settling,
        "run_id": ctx.run_id, "computed_at": ctx.computed_at, "rule_id": found.rule_id,
        "rule_version": found.rule_version,
    }  # fmt: skip
    _trend(ctx, row, value, light, (limits.direction if limits else "") or ctx.config.trend_direction(metric))
    return row


def _key_of(row: Mapping[str, Any]) -> Key:
    return (str(row["segment"]), str(row["column"]), str(row["metric"]))


def _finite(value: float | None) -> float | None:
    return value if value is None or math.isfinite(value) else None


def _trend(ctx: _Context, row: dict[str, Any], value: float | None, light: Light, direction: str) -> None:
    policy = ctx.config.trend
    if policy is None or row["metric"] not in ctx.config.trend_metrics or not direction:
        return
    if light.status not in (*COLORS, NO_RULE) or value is None or not math.isfinite(value) or row["settling"]:
        return
    key = (row["segment"], row["column"], row["metric"])
    history = ctx.trend_history.setdefault(key, [])
    found = trend_check(history, value, policy, direction)
    if row["period"] not in ctx.excluded and keeps(policy, light.status, found.status):
        history.append(value)
    row["trend_status"] = found.status
    row["trend_expected"] = _finite(found.expected)
    row["trend_deviation"] = _finite(found.deviation)
    row["trend_sigma"] = _finite(found.sigma)
    row["trend_lower"] = _finite(found.lower)
    row["trend_upper"] = _finite(found.upper)


def _judge(  # noqa: PLR0913 - the metric and its suppression inputs
    ctx: _Context,
    metric: str,
    column: str,
    value: float | None,
    *,
    where: tuple[str, str],
    rows: int,
    has_baseline: bool,
    reason: str = "",
    sample: Sample | None = None,
) -> Light:
    period, segment = where
    found = ctx.config.resolve(
        dataset=ctx.dataset, column=column, segment=segment, metric=metric, period_date=period_date(period)
    )
    limits = effective_limits(ctx.config, metric, ctx.config.limits_of(found, metric, column), sample)
    needs_volume = metric in DISTRIBUTION_METRICS
    min_count = ctx.config.min_rows
    if needs_volume and sample is not None:
        min_count = max(min_count, math.ceil(ctx.config.min_rows_per_bin * sample.bins))
    if not has_baseline and value is None:
        value = 0.0  # nothing to compare with: let ``no_baseline`` (or too few rows) explain the gap
    return classify(
        value,
        limits,
        count=rows if needs_volume else None,
        min_count=min_count,
        has_baseline=has_baseline,
        reason=reason,
    )


def compute(  # noqa: PLR0913 - one keyword per result field
    binning: Reference,
    profiles: Iterable[Mapping[str, Any]],
    config: StabilityConfig,
    *,
    dataset: str,
    window: Sequence[str],
    run_id: str = "",
    computed_at: str = "",
    emptied: Collection[str] = (),
    settling: Collection[str] = (),
    gap: Callable[[Sequence[str], str], int] | None = None,
    trend_seed: Mapping[Key, Sequence[float]] | None = None,
    excluded: Collection[str] = (),
    red_seed: Mapping[str, Collection[str]] | None = None,
) -> Computed:
    """Compute the result rows of every period of ``window`` and ``emptied`` (in order) from the stored profiles.

    An ``emptied`` period is reported like any other, but its zero-count profile is never a baseline period for
    another period: the ``previous`` and ``rolling`` rules skip it and take the next eligible period.

    A ``settling`` period is judged and shown (``settling`` true) like any other, but it is never a baseline
    period, never enters a trend history and is not counted by the gate. ``gap(baseline periods, period)`` gives
    the ``baseline_gap`` of a result (calendar periods without rows between the earliest baseline period and it).

    ``trend_seed`` carries, per ``(segment, column, metric)``, the stored values of the periods just before the
    window, so a period at the start of a rewritten window is judged with the history it had the first time.

    ``excluded`` lists periods kept out of every trend history and every ``previous``/``rolling`` baseline (the one
    input a human mark of an anomalous period plugs into). With ``baseline.exclude = red`` a period whose
    ``volume_change`` or ``psi`` was red in a segment is skipped for that segment too; ``red_seed`` carries, per
    segment, the red periods already stored before the window.
    """
    history = index_profiles(binning, profiles)
    ctx = _Context(
        config, binning, dataset, run_id, computed_at, settling=frozenset(settling), excluded=frozenset(excluded)
    )
    for segment_name, reds in (red_seed or {}).items():
        ctx.red_periods[segment_name] = set(reds)
    for key, values in (trend_seed or {}).items():
        ctx.trend_history[key] = list(values)
    out = Computed()
    numeric = {s.column: s.kind == "numeric" for s in binning.columns}
    in_window = [p for p in history.periods if p in {*window, *emptied}]
    skipped = {*emptied, *settling}
    eligible = [p for p in history.periods if p not in skipped]
    for period in in_window:
        ordered = sorted([*eligible, period]) if period in skipped else eligible
        wanted = [p for p in baseline_periods(config, binning, ordered, period) if p not in settling]
        ctx.gap = gap(wanted, period) if gap and wanted else 0
        for segment in sorted(history.rows):
            if period not in history.rows[segment]:
                continue
            skip = _skipped(ctx, segment)
            wanted = baseline_periods(config, binning, [p for p in ordered if p == period or p not in skip], period)
            first = len(out.results)
            present = [p for p in wanted if p in history.rows[segment]]
            enough = len(present) >= (config.baseline.min_periods if config.baseline.kind == "rolling" else 1)
            usable = present if enough else []
            out.results += _table_results(ctx, history, segment, period, usable, wanted)
            for spec in binning.columns:
                out.results += _column_results(
                    ctx, out, history, (segment, spec.column), period, usable,
                    numeric=numeric[spec.column], in_baseline=period in wanted,
                )  # fmt: skip
            _mark_red(ctx, segment, period, out.results[first:])
    return out


def _skipped(ctx: _Context, segment: str) -> set[str]:
    """Return the periods a ``previous`` or ``rolling`` baseline of ``segment`` does not take."""
    skip = set(ctx.excluded)
    if ctx.config.baseline.exclude == "red":
        skip |= ctx.red_periods.get(segment, set())
    return skip


def _mark_red(ctx: _Context, segment: str, period: str, results: Iterable[Mapping[str, Any]]) -> None:
    """Remember ``period`` as red for ``segment`` when its ``volume_change`` or ``psi`` was red."""
    if any(r["status"] == RED and r["metric"] in (VOLUME_CHANGE, PSI) for r in results):
        ctx.red_periods.setdefault(segment, set()).add(period)


def _table_results(  # noqa: PLR0913, PLR0917 - the period's full context
    ctx: _Context, history: History, segment: str, period: str, usable: list[str], wanted: list[str]
) -> list[dict[str, Any]]:
    rows = history.rows[segment][period]
    common = {"period": period, "segment": segment, "column": TABLE, "rows": rows, "in_baseline": period in wanted}
    out = [_result(ctx, metric=ROW_COUNT, value=float(rows), current=float(rows), light=Light("no_rule"), **common)]
    base_rows = sum(history.rows[segment][p] for p in usable) / len(usable) if usable else None
    change = rows / base_rows - 1 if base_rows else None
    light = _judge(ctx, VOLUME_CHANGE, TABLE, change, where=(period, segment), rows=rows, has_baseline=bool(usable))
    out.append(
        _result(
            ctx, metric=VOLUME_CHANGE, value=change, light=light, current=float(rows), baseline=base_rows,
            baseline_rows=base_rows, **common,
        )
    )  # fmt: skip
    if ctx.config.weight:
        weight = history.weights[segment][period]
        base = sum(history.weights[segment][p] for p in usable) / len(usable) if usable else None
        wchange = weight / base - 1 if base else None
        light = _judge(
            ctx, WEIGHT_CHANGE, TABLE, wchange, where=(period, segment), rows=rows, has_baseline=bool(usable)
        )
        out.append(
            _result(
                ctx, metric=WEIGHT_CHANGE, value=wchange, light=light, current=weight, baseline=base,
                baseline_rows=base_rows, **common,
            )
        )  # fmt: skip
    return out


def _column_results(  # noqa: PLR0913, PLR0917 - the period's full context
    ctx: _Context,
    out: Computed,
    history: History,
    key: tuple[str, str],
    period: str,
    usable: list[str],
    *,
    numeric: bool,
    in_baseline: bool,
) -> list[dict[str, Any]]:
    segment, column = key
    where = (period, segment)
    series = history.bins.get(key, {})
    current_bins = series.get(period, {})
    rows = history.rows[segment][period]
    base_bins = _sum(series[p] for p in usable if p in series)
    has_base = bool(base_bins) and sum(s[0] for s in base_bins.values()) > 0
    base_rows = sum(history.rows[segment][p] for p in usable) / len(usable) if usable else None
    common = {
        "period": period, "segment": segment, "column": column, "rows": rows, "baseline_rows": base_rows,
        "in_baseline": in_baseline,
    }  # fmt: skip
    cur = _stats(current_bins, numeric=numeric)
    base = _stats(base_bins, numeric=numeric) if has_base else None
    psi_value = _psi(ctx, out, (period, segment, column), current_bins, base_bins if has_base else {})
    sample = sample_of(current_bins, base_bins if has_base else {})
    common["sample"] = sample
    results = [
        _result(ctx, metric=PSI, value=psi_value, light=_judge(
            ctx, PSI, column, psi_value, where=where, rows=rows, has_baseline=has_base, sample=sample), **common),
        _result(ctx, metric=NULL_RATE, value=cur.null_rate, current=cur.null_rate,
                baseline=base.null_rate if base else None, light=_judge(
            ctx, NULL_RATE, column, cur.null_rate, where=where, rows=rows, has_baseline=True), **common),
        _delta(ctx, NULL_RATE_DELTA, cur.null_rate, base.null_rate if base else None, rows, has_base, common),
        _result(ctx, metric=UNSEEN_RATE, value=cur.outside_rate, current=cur.outside_rate,
                baseline=base.outside_rate if base else None, light=_judge(
            ctx, UNSEEN_RATE, column, cur.outside_rate, where=where, rows=rows, has_baseline=True), **common),
        _delta(ctx, UNSEEN_RATE_DELTA, cur.outside_rate, base.outside_rate if base else None, rows, has_base, common),
    ]  # fmt: skip
    if numeric:
        results.append(
            _result(ctx, metric=MEAN, value=cur.mean, current=cur.mean, baseline=base.mean if base else None,
                    light=_judge(ctx, MEAN, column, cur.mean, where=where, rows=rows, has_baseline=True), **common)
        )  # fmt: skip
        shift, reason = _shift(cur, base)
        light = _judge(
            ctx, MEAN_SHIFT, column, shift, where=where, rows=rows, has_baseline=has_base, reason=reason, sample=sample
        )
        results.append(
            _result(ctx, metric=MEAN_SHIFT, value=shift, current=cur.mean, baseline=base.mean if base else None,
                    light=light, **common)
        )  # fmt: skip
    return results


def _delta(  # noqa: PLR0913, PLR0917 - the metric and its context
    ctx: _Context,
    metric: str,
    current: float,
    baseline: float | None,
    rows: int,
    has_base: bool,  # noqa: FBT001 - internal helper
    common: dict[str, Any],
) -> dict[str, Any]:
    value = current - baseline if baseline is not None else None
    where = (common["period"], common["segment"])
    light = _judge(
        ctx, metric, common["column"], value, where=where, rows=rows, has_baseline=has_base,
        sample=common.get("sample"),
    )  # fmt: skip
    return _result(ctx, metric=metric, value=value, current=current, baseline=baseline, light=light, **common)


def _shift(current: _Stats, base: _Stats | None) -> tuple[float | None, str]:
    if base is None or base.mean is None or current.mean is None:
        return None, "no values"
    if not base.std:
        return (0.0, "") if current.mean == base.mean else (None, "baseline has no variance")
    return (current.mean - base.mean) / base.std, ""


def _psi(
    ctx: _Context,
    out: Computed,
    key: tuple[str, str, str],
    current: Mapping[str, Slot],
    baseline: Mapping[str, Sequence[float]],
) -> float | None:
    basis = 1 if ctx.config.weight else 0
    smoothing = smoothing_of(ctx.config)
    cur = {b: float(s[basis]) for b, s in current.items()}
    total = sum(cur.values())
    base = {b: float(s[basis]) for b, s in baseline.items()}
    if not base or total <= 0 or sum(base.values()) <= 0:
        for b, value in cur.items():
            out.shares[(*key, b)] = (value / total if total > 0 else 0.0, None, None)
        return None
    terms = psi_terms(base, cur, ctx.config.epsilon, smoothing)  # type: ignore[arg-type] - weights are floats
    for b, value in cur.items():
        term = terms.get(b)
        out.shares[(*key, b)] = (
            value / total,
            term["reference"] if term else None,
            term["contribution"] if term else None,
        )
    return sum(t["contribution"] for t in terms.values())


#: The building blocks the revision metrics reuse, so both judge and write results the same way.
Context = _Context
result_row = _result
judge = _judge
column_stats = _stats
mean_shift = _shift


def alert_id(dataset: str, version: int, key: Key, kind: str, opened: str) -> str:
    """Return the stable identity of an alert (replays of the same history produce the same id)."""
    text = "|".join((dataset, str(version), *key, kind, opened))
    return hashlib.sha256(text.encode()).hexdigest()[:20]


def _stored_by_key(
    stored: Iterable[Mapping[str, Any]], version: int
) -> tuple[dict[tuple[str, ...], Mapping[str, Any]], dict[tuple[str, ...], list[str]]]:
    """Return the stored open alert and the stored close periods of ``version``, per (segment, column, metric, kind)."""
    carried: dict[tuple[str, ...], Mapping[str, Any]] = {}
    closes: dict[tuple[str, ...], list[str]] = {}
    for r in stored:
        if int(r.get("version") or 0) != version:
            continue
        key = (str(r["segment"]), str(r["column"]), str(r["metric"]), str(r["kind"]))
        if r.get("state") == OPEN:
            carried[key] = r
        elif r.get("state") == CLOSED and r.get("closed_period"):
            closes.setdefault(key, []).append(str(r["closed_period"]))
    return carried, closes


def _since_close(closes: Sequence[str], periods: Sequence[str], first: str) -> int | None:
    """Return how many known ``periods`` lie between the last close before ``first`` and ``first``, or ``None``."""
    before = [p for p in closes if p < first]
    if not before:
        return None
    last = max(before)
    return sum(1 for p in periods if last < p < first)


def _resume(
    stored_open: Mapping[str, Any] | None,
    series: list[tuple[str, str]],
    policy: PersistencePolicy,
    first_period: str,
    *,
    keep_inside: bool,
) -> tuple[Mapping[str, Any] | None, list[tuple[str, str]]]:
    """Return the stored open alert to carry (or ``None``) and the series to replay it over.

    An alert opened inside the window is left to the replay when the replay reproduces it, or when the judgment
    changed (``keep_inside`` false); otherwise it is carried and the series starts at its opening period.
    """
    if stored_open is None or str(stored_open["opened_period"]) <= first_period:
        return stored_open, series
    opened = str(stored_open["opened_period"])
    if not keep_inside or any(s.opened == opened for s in alert_spans(series, policy)):
        return None, series
    return stored_open, [(p, status) for p, status in series if p >= opened]


def _series(
    results: Iterable[Mapping[str, Any]],
) -> tuple[dict[Key, list[tuple[str, str]]], dict[Key, list[tuple[str, str]]], dict[tuple[Key, str], float | None]]:
    """Group results into the status series, the trend series and the values, by series key and period.

    A series without limits (``direction`` empty) is judged by its trend alone: it never opens persistence alerts.
    """
    rows = list(results)
    statuses: dict[Key, list[tuple[str, str]]] = {}
    trends: dict[Key, list[tuple[str, str]]] = {}
    values: dict[tuple[Key, str], float | None] = {}
    trend_only = {_key_of(r) for r in rows if r["direction"] == "" and r["trend_status"]}
    for row in rows:
        if row.get("settling"):
            continue
        key = _key_of(row)
        if row["direction"] == "" and key not in trend_only:
            continue
        if row["direction"] != "":
            statuses.setdefault(key, []).append((row["period"], row["status"]))
        trends.setdefault(key, []).append((row["period"], row["trend_status"] or "skipped"))
        values[(key, row["period"])] = row["value"]
    return statuses, trends, values


def alerts(  # noqa: PLR0913 - one keyword per alert field
    results: Iterable[Mapping[str, Any]],
    config: StabilityConfig,
    *,
    dataset: str,
    version: int,
    run_id: str = "",
    updated_at: str = "",
    stored: Iterable[Mapping[str, Any]] = (),
    periods: Sequence[str] = (),
    keep_inside: bool = True,
) -> list[dict[str, Any]]:
    """Replay the status and trend series of every judged metric into alert rows (persistence and trend kinds).

    An alert still ``open`` in ``stored`` whose opening period is at or before the window start is carried: the
    replay resumes it before the first status, so it keeps its id, opening period, escalation and recurrence,
    and closes after ``close_after`` greens even when the window alone would not open it. The last ``closed``
    alert of the same key that closed before the window counts for recurrence: the distance is the number of
    ``periods`` (the known periods of the data) between its close and the window.

    A stored open alert that opened inside the window, when the window alone no longer reproduces it (its red run
    began before the window), is carried too and replayed from its opening period; ``keep_inside=False`` (the
    judgment changed) lets the replay re-judge it instead.
    """
    carried, closes = _stored_by_key(stored, version)
    statuses, trends, values = _series(results)
    persistence = config.persistence
    trend_policy = PersistencePolicy(
        yellow=0, red=1, close_after=persistence.close_after, immediate_red=True, recurrence=persistence.recurrence
    )
    out: list[dict[str, Any]] = []
    for key in sorted(trends):
        for kind, series, policy in (
            (PERSISTENCE, statuses.get(key, []), persistence),
            (TREND, trends[key], trend_policy),
        ):
            if not series:
                continue
            first_period = min((p for p, _ in series), default="")
            stored_open, replayed = _resume(
                carried.get((*key, kind)), series, policy, first_period, keep_inside=keep_inside
            )
            since_close = _since_close(closes.get((*key, kind), ()), periods, first_period)
            seed = None
            if stored_open is not None:
                seed = AlertSpan(
                    str(stored_open.get("severity") or WARNING),
                    str(stored_open["opened_period"]),
                    last_period=str(stored_open.get("last_period") or ""),
                    last_status=str(stored_open.get("last_status") or ""),
                )
            for number, span in enumerate(alert_spans(replayed, policy, carry=seed, since_close=since_close)):
                old = stored_open if number == 0 else None
                flagged = len(span.flagged)
                if old is not None:
                    span.opened = str(old["opened_period"])
                    span.escalated = str(old.get("escalated_period") or "") or span.escalated
                    span.recurrence = bool(old.get("recurrence")) or span.recurrence
                    if str(old.get("severity")) == CRITICAL:
                        span.severity = CRITICAL
                    seen = str(old.get("last_period") or "")
                    flagged = int(old.get("flagged_periods") or 0) + sum(1 for p in span.flagged if p > seen)
                value = values.get((key, span.last_period))
                segment, column, metric = key
                subject = f"{metric} of {column}" if column else metric
                verb = "departs from its trend" if kind == TREND else "is unstable"
                message = (
                    f"{subject} [{segment_label(segment)}] {verb}: {span.severity} since {span.opened}, "
                    f"last {span.last_status} in {span.last_period}"
                    + (f" (value {value:.4g})" if value is not None else "")
                )
                out.append(
                    {
                        "dataset": dataset, "alert_id": alert_id(dataset, version, key, kind, span.opened),
                        "version": version, "kind": kind, "metric": metric, "column": column, "segment": segment,
                        "segment_label": segment_label(segment), "severity": span.severity,
                        "severity_rank": 2 if span.severity == CRITICAL else 1, "state": span.state,
                        "opened_period": span.opened, "escalated_period": span.escalated,
                        "closed_period": span.closed, "last_period": span.last_period,
                        "last_status": span.last_status, "last_value": value, "flagged_periods": flagged,
                        "recurrence": span.recurrence, "message": message, "run_id": run_id,
                        "updated_at": updated_at,
                    }
                )  # fmt: skip
    return out


def counts_in(results: Iterable[Mapping[str, Any]], period: str) -> dict[str, int]:
    """Return how many results of ``period`` are red and yellow (settling results are not counted)."""
    found = {RED: 0, YELLOW: 0, GREEN: 0}
    for row in results:
        if row["period"] == period and row["status"] in found and not row.get("settling"):
            found[row["status"]] += 1
    return found


__all__ = [
    "PERSISTENCE",
    "TABLE",
    "TREND",
    "Computed",
    "Context",
    "History",
    "alert_id",
    "alerts",
    "baseline_periods",
    "column_stats",
    "compute",
    "counts_in",
    "index_profiles",
    "judge",
    "mean_shift",
    "rank",
    "result_row",
]
