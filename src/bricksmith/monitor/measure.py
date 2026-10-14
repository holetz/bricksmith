"""Metrics from a ``Summary`` (the engine-independent reduction of one window) and the pure list engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.monitor import stats
from bricksmith.monitor.bins import ABOVE, BELOW, NULL, UNSEEN, is_null

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.monitor.reference import Reference

OK = "ok"
UNAVAILABLE = "unavailable"
NULL_SEGMENT = "(null)"


@dataclass(frozen=True)
class Measurement:
    """One metric value, or the reason there is none. ``count`` is the number of rows the metric used."""

    metric: str
    subject: str
    segment: str
    value: float | None
    status: str = OK
    reason: str = ""
    count: int = 0
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> tuple[str, str, str]:
        """Return the identity ``(metric, subject, segment)``."""
        return (self.metric, self.subject, self.segment)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "count": self.count,
            "detail": dict(self.detail),
            "metric": self.metric,
            "reason": self.reason,
            "segment": self.segment,
            "status": self.status,
            "subject": self.subject,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Measurement:
        """Rebuild a measurement from ``to_dict`` output."""
        value = data.get("value")
        return cls(
            metric=str(data["metric"]),
            subject=str(data.get("subject", "")),
            segment=str(data.get("segment", "")),
            value=None if value is None else float(value),
            status=str(data.get("status", OK)),
            reason=str(data.get("reason", "")),
            count=int(data.get("count", 0)),
            detail=dict(data.get("detail", {})),
        )


@dataclass
class Summary:
    """Counts and sums of one window (and segment) that every metric derives from."""

    n: int = 0
    bins: dict[str, dict[str, int]] = field(default_factory=dict)
    periods: dict[str, dict[str, dict[str, int]]] = field(default_factory=dict)
    groups: dict[float, tuple[int, int]] = field(default_factory=dict)
    labeled: int = 0
    invalid_labels: int = 0
    missing_scores: int = 0
    invalid_scores: int = 0
    calibration: dict[int, tuple[int, float, float]] = field(default_factory=dict)
    squared_error: float = 0.0


def period_key(value: object, kind: str) -> str | None:
    """Return the period of a time value (``day`` or ``month`` prefix of its ISO text) or ``None`` when null."""
    if is_null(value):
        return None
    return str(value)[: 10 if kind == "day" else 7]


def summarize(reference: Reference, rows: Sequence[Mapping[str, Any]]) -> Summary:
    """Reduce already windowed rows to a ``Summary`` using the reference's bins (pure Python)."""
    summary = Summary(n=len(rows))
    period_kind = reference.metric_config.get("stability_period", "") if reference.time else ""
    for spec in reference.columns:
        summary.bins[spec.column] = dict.fromkeys(spec.bin_ids(), 0)
        if period_kind:
            summary.periods[spec.column] = {}
    bins = reference.metric_config["calibration_bins"]
    for row in rows:
        period = period_key(row.get(reference.time), period_kind) if period_kind else None
        for spec in reference.columns:
            bin_id = spec.assign(row.get(spec.column))
            summary.bins[spec.column][bin_id] += 1
            if period is not None:
                slot = summary.periods[spec.column].setdefault(period, dict.fromkeys(spec.bin_ids(), 0))
                slot[bin_id] += 1
        if not reference.label or is_null(row.get(reference.label)):
            continue
        summary.labeled += 1
        label = float(row[reference.label])
        if label not in (0.0, 1.0):
            summary.invalid_labels += 1
            continue
        score = row.get(reference.score) if reference.score else None
        if is_null(score):
            summary.missing_scores += 1
            continue
        score = float(score)  # type: ignore[arg-type]
        pos, neg = summary.groups.get(score, (0, 0))
        summary.groups[score] = (pos + int(label == 1.0), neg + int(label == 0.0))
        if 0.0 <= score <= 1.0:
            idx = stats.calibration_bin(score, bins)
            n, sum_s, sum_y = summary.calibration.get(idx, (0, 0.0, 0.0))
            summary.calibration[idx] = (n + 1, sum_s + score, sum_y + label)
            summary.squared_error += (score - label) ** 2
        else:
            summary.invalid_scores += 1
    return summary


def _unavailable(metric: str, subject: str, segment: str, reason: str, count: int = 0) -> Measurement:
    return Measurement(metric, subject, segment, None, UNAVAILABLE, reason, count)


def _distribution(reference: Reference, summary: Summary, segment: str) -> list[Measurement]:
    out: list[Measurement] = []
    epsilon = float(reference.metric_config["epsilon"])
    for spec in reference.columns:
        name = "psi" if spec.column == reference.score else "csi"
        current = summary.bins[spec.column]
        if summary.n == 0:
            out += [_unavailable(m, spec.column, segment, "empty_window") for m in (name, "coverage", "null_rate")]
        else:
            if sum(spec.counts.values()) == 0:
                out.append(_unavailable(name, spec.column, segment, "empty_reference", summary.n))
            else:
                terms = stats.psi_terms(spec.counts, current, epsilon)
                value = sum(t["contribution"] for t in terms.values())
                out.append(Measurement(name, spec.column, segment, value, count=summary.n, detail={"bins": terms}))
            outside = sum(current.get(b, 0) for b in (NULL, BELOW, ABOVE, UNSEEN))
            rates = {b: current.get(b, 0) / summary.n for b in (NULL, BELOW, ABOVE, UNSEEN)}
            out.append(
                Measurement("coverage", spec.column, segment, 1 - outside / summary.n, count=summary.n, detail=rates)
            )
            out.append(Measurement("null_rate", spec.column, segment, rates[NULL], count=summary.n))
        if reference.metric_config.get("stability_period"):
            out.append(_stability(reference, summary, spec.column, segment))
    return out


def _stability(reference: Reference, summary: Summary, column: str, segment: str) -> Measurement:
    spec = reference.column(column)
    periods = {p: c for p, c in summary.periods.get(column, {}).items() if sum(c.values()) > 0}
    if len(periods) < 2 or sum(spec.counts.values()) == 0:  # noqa: PLR2004 - two periods are the minimum
        return _unavailable("stability", column, segment, "insufficient_periods", summary.n)
    values = {
        p: stats.psi(spec.counts, c, float(reference.metric_config["epsilon"])) for p, c in sorted(periods.items())
    }
    return Measurement("stability", column, segment, max(values.values()), count=summary.n, detail={"periods": values})


def _label_reason(summary: Summary) -> str:
    if summary.n == 0:
        return "empty_window"
    if summary.invalid_labels:
        return "invalid_labels"
    if summary.labeled == 0:
        return "missing_labels"
    if not summary.groups:
        return "missing_scores"
    return ""


def _model(reference: Reference, summary: Summary, segment: str) -> list[Measurement]:
    subject = reference.score
    used = sum(p + n for p, n in summary.groups.values())
    reason = _label_reason(summary)
    names = ("auc", "gini", "ks")
    out: list[Measurement] = []
    if reason:
        out += [_unavailable(m, subject, segment, reason, used) for m in (*names, "brier", "ece")]
        return out
    try:
        auc = stats.auc_groups(summary.groups)
        ks = stats.ks_groups(summary.groups)
        detail = {
            "negatives": sum(n for _, n in summary.groups.values()),
            "positives": sum(p for p, _ in summary.groups.values()),
        }
        out += [
            Measurement("auc", subject, segment, auc, count=used, detail=detail),
            Measurement("gini", subject, segment, 2 * auc - 1, count=used, detail=detail),
            Measurement("ks", subject, segment, ks, count=used, detail=detail),
        ]
    except ZeroDivisionError:
        out += [_unavailable(m, subject, segment, "single_class", used) for m in names]
    if summary.invalid_scores or not summary.calibration:
        out += [_unavailable(m, subject, segment, "score_out_of_range", used) for m in ("brier", "ece")]
        return out
    total = sum(n for n, _, _ in summary.calibration.values())
    table = {
        str(i): {"count": n, "mean_score": s / n, "observed_rate": y / n}
        for i, (n, s, y) in sorted(summary.calibration.items())
    }
    ece = sum(n / total * abs(y / n - s / n) for n, s, y in summary.calibration.values())
    out += [
        Measurement("brier", subject, segment, summary.squared_error / total, count=total),
        Measurement("ece", subject, segment, ece, count=total, detail={"bins": table}),
    ]
    return out


def measure_summary(reference: Reference, summary: Summary, segment: str = "") -> list[Measurement]:
    """Derive every measurement the reference defines from one summary (shared by both engines)."""
    out = _distribution(reference, summary, segment)
    out.append(Measurement("row_count", "", segment, float(summary.n), count=summary.n))
    if reference.fit_rows > 0:
        out.append(Measurement("volume_ratio", "", segment, summary.n / reference.fit_rows, count=summary.n))
    if reference.score and reference.label:
        out += _model(reference, summary, segment)
    return out


def in_window(rows: Sequence[Mapping[str, Any]], window: Mapping[str, str] | None) -> list[Mapping[str, Any]]:
    """Keep rows whose window column text lies in ``[start, end)``."""
    if not window:
        return list(rows)
    column, start, end = window["column"], window["start"], window["end"]
    return [r for r in rows if not is_null(r.get(column)) and start <= str(r[column]) < end]


def segment_name(value: object) -> str:
    """Return the segment label of a value (``(null)`` for null)."""
    return NULL_SEGMENT if is_null(value) else str(value)


def measure_rows(
    reference: Reference,
    rows: Sequence[Mapping[str, Any]],
    window: Mapping[str, str] | None = None,
    *,
    segments: bool = True,
) -> list[Measurement]:
    """Compare rows with the reference (pure engine). Whole population first, then each segment value."""
    scoped = in_window(rows, window)
    out = measure_summary(reference, summarize(reference, scoped))
    if segments and reference.segment:
        for name in sorted({segment_name(r.get(reference.segment)) for r in scoped}):
            subset = [r for r in scoped if segment_name(r.get(reference.segment)) == name]
            out += measure_summary(reference, summarize(reference, subset), name)
    return out
