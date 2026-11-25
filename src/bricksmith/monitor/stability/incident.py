"""Stability incidents: the alerts of one run grouped per dataset and period, ranked by cause, plus the noise budget.

Everything here is pure: it reads the alert, result and profile rows a check already holds and returns plain
values. An incident is derived at each check, never stored; the per-alert rows stay the source of truth.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.limits import BOTH, HIGHER, LOWER
from bricksmith.monitor.persistence import CRITICAL, OPEN
from bricksmith.monitor.stability.config import CRITICALITY_WEIGHTS

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from bricksmith.monitor.stability.config import NoiseBudget, StabilityConfig
    from bricksmith.ops.notify import AlertEvent

TREND = "trend"
FALSE_POSITIVE = "false_positive"
CLOSED_ACTION = "closed"
SQRT2 = math.sqrt(2.0)

Key = tuple[str, str, str]


def _key(row: Mapping[str, Any]) -> Key:
    return (str(row["segment"]), str(row["column"]), str(row["metric"]))


def trend_p_value(deviation: float, direction: str = BOTH) -> float:
    """Return the p-value of a standardized trend deviation under a normal approximation.

    ``higher`` and ``lower`` are one-sided (only a deviation that way counts), anything else is two-sided.
    """
    if math.isnan(deviation):
        return 1.0
    z = {HIGHER: deviation, LOWER: -deviation}.get(direction)
    if z is None:
        return math.erfc(abs(deviation) / SQRT2)
    return 0.5 * math.erfc(z / SQRT2)


def benjamini_hochberg(p_values: Mapping[Key, float], level: float) -> set[Key]:
    """Return the keys rejected by the Benjamini-Hochberg step-up procedure at false-discovery ``level``."""
    ordered = sorted(p_values.items(), key=lambda kv: (kv[1], kv[0]))
    total = len(ordered)
    cutoff = 0
    for rank, (_, p) in enumerate(ordered, start=1):
        if p <= level * rank / total:
            cutoff = rank
    return {key for key, _ in ordered[:cutoff]}


def _results_at(results: Iterable[Mapping[str, Any]], period: str) -> dict[Key, Mapping[str, Any]]:
    return {_key(r): r for r in results if str(r["period"]) == period}


def alert_score(row: Mapping[str, Any], results: Mapping[Key, Mapping[str, Any]], config: StabilityConfig) -> float:
    """Return how far the alert's series is beyond its red threshold: the value over the red limit, or the trend.

    A series with a red limit scores ``|value| / red_limit``; one judged only by its trend scores
    ``|deviation| / trend.red``. A missing or infinite magnitude scores zero or the largest finite score.
    """
    found = results.get(_key(row))
    if found is None:
        return 0.0
    red = found.get("red_limit")
    value = found.get("value")
    deviation = found.get("trend_deviation")
    if row.get("kind") != TREND and red and value is not None:
        score = abs(float(value)) / abs(float(red))
    elif deviation is not None and config.trend is not None:
        score = abs(float(deviation)) / config.trend.red
    else:
        score = 0.0
    return score if math.isfinite(score) else 1e9


@dataclass(frozen=True)
class Cause:
    """One alert of the incident with the magnitude it was ranked by."""

    alert_id: str
    segment: str
    column: str
    metric: str
    kind: str
    severity: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "alert_id": self.alert_id, "column": self.column, "kind": self.kind, "metric": self.metric,
            "score": round(self.score, 6), "segment": self.segment, "severity": self.severity,
        }  # fmt: skip


@dataclass(frozen=True)
class Incident:
    """The alerts open after one run of a dataset, grouped and ranked.

    ``causes`` are ordered by severity then score; ``segments`` and ``columns`` sum the scores of their alerts;
    ``bins`` rank the stored contributions of the latest period of the alerted columns and segments.
    """

    incident_id: str
    dataset: str
    period: str
    severity: str
    causes: tuple[Cause, ...]
    segments: tuple[tuple[str, float], ...] = ()
    columns: tuple[tuple[str, float], ...] = ()
    bins: tuple[dict[str, Any], ...] = ()
    suppressed: int = 0
    false_positive_rates: dict[str, dict[str, Any]] = field(default_factory=dict)
    severity_score: float = 0.0
    upstream: str = ""

    def to_dict(self, top: int | None = None) -> dict[str, Any]:
        """Return the serializable form, limited to the ``top`` entries of each ranking when given."""
        cut = slice(None, top)
        extra: dict[str, Any] = {"upstream": self.upstream} if self.upstream else {}
        return {
            **extra,
            "severity_score": self.severity_score,
            "alerts": len(self.causes),
            "bins": list(self.bins[cut]),
            "causes": [c.to_dict() for c in self.causes[cut]],
            "columns": [{"column": c, "score": round(s, 6)} for c, s in self.columns[cut]],
            "dataset": self.dataset,
            "false_positive_rates": self.false_positive_rates,
            "incident_id": self.incident_id,
            "period": self.period,
            "segments": [{"segment": g, "score": round(s, 6)} for g, s in self.segments[cut]],
            "severity": self.severity,
            "suppressed": self.suppressed,
        }


@dataclass(frozen=True)
class Upstream:
    """The red incident a task of the same run left for one period, and the dataset that started it.

    ``dataset`` is the task's own dataset; ``origin`` is the dataset where the problem began: the same one when the
    task had no upstream incident, else the origin the task inherited.
    """

    dataset: str
    period: str
    origin: str


def upstream_origin(records: Iterable[Upstream], period: str) -> str:
    """Return the origin dataset of the upstream incidents of ``period`` (the first by name), or ``""`` when none."""
    return min((r.origin for r in records if r.period == period), default="")


def severity_score(causes: Sequence[Cause], open_alerts: Sequence[Mapping[str, Any]], criticality: str) -> float:
    """Return the ranking score of an incident: exceedance * persistence * criticality weight * correlated columns.

    Exceedance is the sum of the cause scores, persistence the largest number of flagged periods of an open alert
    (at least 1) and the correlated columns are the distinct alerted columns (at least 1).
    """
    persistence = max((int(a.get("flagged_periods") or 0) for a in open_alerts), default=0) or 1
    columns = len({c.column for c in causes if c.column}) or 1
    weight = CRITICALITY_WEIGHTS.get(criticality, CRITICALITY_WEIGHTS["medium"])
    return round(sum(c.score for c in causes) * persistence * weight * columns, 6)


def _ranked(scores: Mapping[str, float]) -> tuple[tuple[str, float], ...]:
    return tuple(sorted(((k, v) for k, v in scores.items() if k), key=lambda kv: (-kv[1], kv[0])))


def _bins(profiles: Iterable[Mapping[str, Any]], causes: Sequence[Cause], period: str) -> tuple[dict[str, Any], ...]:
    wanted = {(c.segment, c.column) for c in causes if c.column}
    found = [
        {
            "bin": str(r["bin"]), "bin_label": str(r.get("bin_label") or r["bin"]), "column": str(r["column"]),
            "contribution": round(float(r["contribution"]), 6), "segment": str(r["segment"]),
        }
        for r in profiles
        if str(r["period"]) == period
        and (str(r["segment"]), str(r["column"])) in wanted
        and r.get("contribution") is not None
        and math.isfinite(float(r["contribution"]))
    ]  # fmt: skip
    return tuple(sorted(found, key=lambda b: (-abs(b["contribution"]), b["segment"], b["column"], b["bin"])))


def build_incident(  # noqa: PLR0913 - the rows one check holds and the run identity
    open_alerts: Sequence[Mapping[str, Any]],
    results: Iterable[Mapping[str, Any]],
    profiles: Iterable[Mapping[str, Any]],
    config: StabilityConfig,
    *,
    dataset: str,
    period: str,
    suppressed: int = 0,
    false_positive_rates: dict[str, dict[str, Any]] | None = None,
    upstream: str = "",
) -> Incident | None:
    """Group the open alerts of ``dataset`` into one incident of ``period``; ``None`` when none is open."""
    if not open_alerts:
        return None
    at = _results_at(results, period)
    causes = sorted(
        (
            Cause(
                str(a["alert_id"]), str(a["segment"]), str(a["column"]), str(a["metric"]), str(a["kind"]),
                str(a["severity"]), alert_score(a, at, config),
            )
            for a in open_alerts
        ),
        key=lambda c: (c.severity != CRITICAL, -c.score, c.alert_id),
    )  # fmt: skip
    by_segment: dict[str, float] = {}
    by_column: dict[str, float] = {}
    for cause in causes:
        by_segment[cause.segment] = by_segment.get(cause.segment, 0.0) + cause.score
        by_column[cause.column] = by_column.get(cause.column, 0.0) + cause.score
    identity = hashlib.sha256(f"{dataset}\x1f{period}".encode()).hexdigest()[:16]
    return Incident(
        f"stability:{dataset}:{period}:{identity}", dataset, period,
        CRITICAL if any(c.severity == CRITICAL for c in causes) else "warning",
        tuple(causes), _ranked(by_segment), _ranked(by_column), _bins(profiles, causes, period),
        suppressed, false_positive_rates or {}, severity_score(causes, open_alerts, config.criticality), upstream,
    )  # fmt: skip


def apply_noise(  # noqa: PLR0913 - the rows of one check and the budget
    alert_rows: Sequence[dict[str, Any]],
    stored_ids: Collection[str],
    results: Iterable[Mapping[str, Any]],
    config: StabilityConfig,
    budget: NoiseBudget,
    *,
    latest: str,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(alert rows kept, how many new alerts the budget suppressed)``.

    Only alerts that are open and not stored yet are candidates; stored alerts and non-open rows pass through. With
    ``fdr``, a new warning-severity trend alert survives when Benjamini-Hochberg over the trend p-values of the
    latest period rejects its series; critical alerts are never filtered by it. ``max_alerts_per_run`` then keeps the
    candidates with the highest severity and score.
    """
    at = _results_at(results, latest)
    candidates = [r for r in alert_rows if r["state"] == OPEN and str(r["alert_id"]) not in stored_ids]
    dropped: set[str] = set()
    if budget.fdr is not None:
        family = {
            key: trend_p_value(float(r["trend_deviation"]), str(r.get("direction") or BOTH))
            for key, r in at.items()
            if r.get("trend_deviation") is not None
        }
        discoveries = benjamini_hochberg(family, budget.fdr) if family else set()
        dropped |= {
            str(r["alert_id"])
            for r in candidates
            if r["kind"] == TREND and r["severity"] != CRITICAL and _key(r) not in discoveries
        }
    kept = [r for r in candidates if str(r["alert_id"]) not in dropped]
    if budget.max_alerts_per_run is not None and len(kept) > budget.max_alerts_per_run:
        ranked = sorted(
            kept, key=lambda r: (r["severity"] != CRITICAL, -alert_score(r, at, config), str(r["alert_id"]))
        )
        dropped |= {str(r["alert_id"]) for r in ranked[budget.max_alerts_per_run :]}
    return [r for r in alert_rows if str(r["alert_id"]) not in dropped], len(dropped)


def false_positive_rates(
    actions: Iterable[Mapping[str, Any]],
    alert_rows: Iterable[Mapping[str, Any]],
    results: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return ``rule_id -> {closed, false_positive, rate}`` from the typed resolutions of closed alerts.

    The latest ``closed`` action of each alert counts once; its rule is the ``rule_id`` of the alert's series in
    the results. Series that no rule judged are left out.
    """
    rules: dict[Key, str] = {}
    for r in results:
        if r.get("rule_id"):
            rules[_key(r)] = str(r["rule_id"])
    by_alert = {str(a["alert_id"]): _key(a) for a in alert_rows}
    latest: dict[str, Mapping[str, Any]] = {}
    for action in sorted(actions, key=lambda a: str(a.get("created_at") or "")):
        latest[str(action["alert_id"])] = action
    counts: dict[str, list[int]] = {}
    for alert_id, action in latest.items():
        rule = rules.get(by_alert.get(alert_id, ("", "", "")))
        if action.get("action") != CLOSED_ACTION or rule is None:
            continue
        tally = counts.setdefault(rule, [0, 0])
        tally[0] += 1
        tally[1] += action.get("resolution") == FALSE_POSITIVE
    return {
        rule: {"closed": closed, "false_positive": fp, "rate": round(fp / closed, 4)}
        for rule, (closed, fp) in sorted(counts.items())
    }


def digest_event(  # noqa: PLR0913 - one keyword per event field
    events: Sequence[AlertEvent],
    incident: Incident | None,
    *,
    dataset: str,
    target: str,
    run_id: str,
    occurred_at: str,
    top: int,
    meta: Mapping[str, str] | None = None,
) -> AlertEvent:
    """Collapse the per-alert ``events`` of one run into one message listing the incident and its top causes.

    The per-alert dedup keys ride in ``details.alert_keys`` and the digest key hashes them, so a run that yields
    the same alert events yields the same digest key and a run with none yields no digest at all.
    """
    from bricksmith.ops.events import STATISTICAL_ALERT  # noqa: PLC0415 - keeps the engine import light
    from bricksmith.ops.notify import AlertEvent  # noqa: PLC0415

    keys = sorted(e.dedup_key for e in events)
    severity = "critical" if any(e.severity == "critical" for e in events) else "warning"
    causes = [c.to_dict() for c in incident.causes[:top]] if incident else []
    columns = [c for c, _ in incident.columns[:top]] if incident else []
    segments = [s for s, _ in incident.segments[:top]] if incident else []
    lead = ", ".join(f"{c['metric']} of {c['column']}" if c["column"] else str(c["metric"]) for c in causes)
    period = incident.period if incident else ""
    message = f"{len(events)} stability alert(s) of {dataset} {period}" + (f"; top causes: {lead}" if lead else "")
    digest = hashlib.sha256("\x1f".join(keys).encode()).hexdigest()[:16]
    return AlertEvent(
        STATISTICAL_ALERT,
        dataset,
        message,
        severity,
        STATISTICAL_ALERT,
        target,
        run_id,
        occurred_at=occurred_at,
        key=f"stability:{dataset}:digest:{digest}",
        details={
            "alert_keys": keys,
            "causes": causes,
            "columns": columns,
            "incident_id": incident.incident_id if incident else "",
            "segments": segments,
            "transitions": sorted({str(e.details.get("transition")) for e in events}),
            **(meta or {}),
        },
    )


__all__ = [
    "Cause",
    "Incident",
    "Upstream",
    "alert_score",
    "apply_noise",
    "benjamini_hochberg",
    "build_incident",
    "digest_event",
    "false_positive_rates",
    "severity_score",
    "trend_p_value",
    "upstream_origin",
]
