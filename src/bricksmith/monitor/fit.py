"""Fit a reference from rows (pure) or, through ``monitor.spark``, from a DataFrame; both share ``build_reference``."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.bins import fit_bins
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.measure import in_window
from bricksmith.monitor.reference import DEFAULT_CONFIG, AlertRule, Reference

if TYPE_CHECKING:
    from collections.abc import Hashable, Mapping, Sequence


def build_reference(  # noqa: PLR0913 - one keyword per documented reference field
    counts: Mapping[str, Mapping[Hashable, int]],
    columns: Mapping[str, Mapping[str, Any]],
    fit_rows: int,
    *,
    name: str,
    version: int,
    score: str = "",
    label: str = "",
    segment: str = "",
    time: str = "",
    fit_source: str = "",
    fit_window: Mapping[str, str] | None = None,
    metric_config: Mapping[str, Any] | None = None,
    evaluation_window: Mapping[str, Any] | None = None,
    alert_policy: Sequence[AlertRule | Mapping[str, Any]] = (),
) -> Reference:
    """Assemble a reference from per-column ``value -> count`` mappings and bin configurations."""
    if fit_rows <= 0:
        msg = "cannot fit a reference on an empty window"
        raise MonitorError(msg)
    specs = tuple(fit_bins(column, counts[column], config) for column, config in columns.items())
    rules = tuple(r if isinstance(r, AlertRule) else AlertRule.from_dict(dict(r)) for r in alert_policy)
    return Reference(
        name=name,
        version=version,
        columns=specs,
        fit_source=fit_source,
        fit_rows=fit_rows,
        fit_window=dict(fit_window or {}),
        score=score,
        label=label,
        segment=segment,
        time=time,
        metric_config={**DEFAULT_CONFIG, **(metric_config or {})},
        evaluation_window=dict(evaluation_window or {}),
        alert_policy=rules,
    )


def fit_rows(rows: Sequence[Mapping[str, Any]], columns: Mapping[str, Mapping[str, Any]], **meta: Any) -> Reference:  # noqa: ANN401 - reference metadata
    """Fit bins on rows (optionally limited to ``fit_window``) and return the versioned reference."""
    window = meta.get("fit_window")
    scoped = in_window(rows, window)
    counts: dict[str, Counter[Hashable]] = {c: Counter() for c in columns}
    for row in scoped:
        for column in columns:
            counts[column][row.get(column)] += 1
    return build_reference(counts, columns, len(scoped), **meta)
