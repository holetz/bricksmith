"""Revision reliability: did a period change since its first publication.

Pure, on profile rows. The first publication of a period is the profile the monitor stored the first time it saw
the period. Comparing the period's current profile with it gives, per segment: ``revision_row_change`` (rows now /
rows at publication - 1, whole table), ``revision_psi`` (bin shares over the same fitted bins) and
``revision_mean_shift`` (mean change in standard deviations of the first publication, numeric columns). The
results are ordinary stability results, so limits, rules, ``min_rows``, persistence alerts and the gate apply.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.monitor.stability.config import REVISION_MEAN_SHIFT, REVISION_PSI, REVISION_ROW_CHANGE
from bricksmith.monitor.stability.metrics import (
    TABLE,
    Context,
    column_stats,
    index_profiles,
    judge,
    mean_shift,
    result_row,
)
from bricksmith.monitor.stats import psi_terms

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from bricksmith.monitor.reference import Reference
    from bricksmith.monitor.stability.config import StabilityConfig
    from bricksmith.monitor.stability.metrics import History

BASELINE_KIND = "revision"
BASELINE_LABEL = "first publication"
SNAPSHOT_FIELDS = (
    "dataset", "version", "period", "period_date", "segment", "segment_label", "column", "bin", "bin_order",
    "bin_label", "count", "weight", "value_sum", "value_sum_sq",
)  # fmt: skip


def snapshot_rows(
    profiles: Iterable[Mapping[str, Any]], periods: Iterable[str], *, run_id: str, published_at: str
) -> list[dict[str, Any]]:
    """Return the profile rows of ``periods`` as first-publication rows (the ``revisions`` table)."""
    wanted = set(periods)
    return [
        {**{c: r.get(c) for c in SNAPSHOT_FIELDS}, "run_id": run_id, "published_at": published_at}
        for r in profiles
        if r["period"] in wanted
    ]


def _psi(
    config: StabilityConfig, first: Mapping[str, Sequence[float]], now: Mapping[str, Sequence[float]]
) -> float | None:
    basis = 1 if config.weight else 0
    base = {b: float(s[basis]) for b, s in first.items()}
    cur = {b: float(s[basis]) for b, s in now.items()}
    if sum(base.values()) <= 0 or sum(cur.values()) <= 0:
        return None
    terms = psi_terms(base, cur, config.epsilon)  # type: ignore[arg-type] - weights are floats
    return sum(t["contribution"] for t in terms.values())


def _table_result(
    ctx: Context, common: dict[str, Any], rows_now: int, rows_first: int, where: tuple[str, str]
) -> dict[str, Any]:
    change = rows_now / rows_first - 1 if rows_first else None
    light = judge(ctx, REVISION_ROW_CHANGE, TABLE, change, where=where, rows=rows_now, has_baseline=rows_first > 0)
    return result_row(
        ctx, metric=REVISION_ROW_CHANGE, value=change, light=light, current=float(rows_now), baseline=float(rows_first),
        **common,
    )  # fmt: skip


def _column_results(  # noqa: PLR0913 - the period's full context
    ctx: Context,
    common: dict[str, Any],
    where: tuple[str, str],
    column: str,
    *,
    numeric: bool,
    history: tuple[History, History],
) -> list[dict[str, Any]]:
    now, first = history
    segment, period = where[1], where[0]
    cur_bins, base_bins = now.bins[(segment, column)][period], first.bins[(segment, column)][period]
    rows = common["rows"]
    value = _psi(ctx.config, base_bins, cur_bins)
    light = judge(ctx, REVISION_PSI, column, value, where=where, rows=rows, has_baseline=value is not None)
    out = [
        result_row(
            ctx, metric=REVISION_PSI, value=value, light=light, current=None, baseline=None,
            **{**common, "column": column},
        )
    ]  # fmt: skip
    if numeric:
        cur, base = column_stats(cur_bins, numeric=True), column_stats(base_bins, numeric=True)
        shift, reason = mean_shift(cur, base)
        light = judge(
            ctx,
            REVISION_MEAN_SHIFT,
            column,
            shift,
            where=where,
            rows=rows,
            has_baseline=base.mean is not None,
            reason=reason,
        )
        out.append(
            result_row(
                ctx, metric=REVISION_MEAN_SHIFT, value=shift, light=light, current=cur.mean, baseline=base.mean,
                **{**common, "column": column},
            )
        )  # fmt: skip
    return out


def revision_results(  # noqa: PLR0913 - one keyword per result field
    binning: Reference,
    current: Iterable[Mapping[str, Any]],
    snapshot: Iterable[Mapping[str, Any]],
    config: StabilityConfig,
    *,
    dataset: str,
    window: Sequence[str],
    run_id: str = "",
    computed_at: str = "",
) -> list[dict[str, Any]]:
    """Return the revision results of every period of ``window`` that has a first-publication ``snapshot``."""
    now, first = index_profiles(binning, current), index_profiles(binning, snapshot)
    ctx = Context(config, binning, dataset, run_id, computed_at)
    numeric = {s.column: s.kind == "numeric" for s in binning.columns}
    out: list[dict[str, Any]] = []
    for period in (p for p in window if p in set(first.periods)):
        for segment in sorted(now.rows):
            if period not in now.rows[segment] or period not in first.rows.get(segment, {}):
                continue
            rows_now, rows_first = now.rows[segment][period], first.rows[segment][period]
            where = (period, segment)
            common = {"period": period, "segment": segment, "column": TABLE, "rows": rows_now}
            results = [_table_result(ctx, common, rows_now, rows_first, where)]
            for spec in binning.columns:
                key = (segment, spec.column)
                if period in now.bins.get(key, {}) and period in first.bins.get(key, {}):
                    results += _column_results(
                        ctx, common, where, spec.column, numeric=numeric[spec.column], history=(now, first)
                    )
            for row in results:
                row["baseline_kind"], row["baseline_label"] = BASELINE_KIND, BASELINE_LABEL
            out += results
    return out


__all__ = ["BASELINE_KIND", "BASELINE_LABEL", "revision_results", "snapshot_rows"]
