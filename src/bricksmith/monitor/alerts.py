"""Evaluate alert rules over measurements and emit ``bricksmith.ops.AlertEvent`` for statistical breaches."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.measure import OK, UNAVAILABLE, Measurement
from bricksmith.ops.notify import AlertEvent

if TYPE_CHECKING:
    from bricksmith.monitor.reference import AlertRule, Reference

ALERT = "alert"
INSUFFICIENT_COUNT = "insufficient_count"
INSUFFICIENT_HISTORY = "insufficient_history"
ERROR_CLASS = "statistical_alert"
KIND = "monitor_threshold"
History = Mapping[tuple[str, str, str], Sequence[float]]


@dataclass(frozen=True)
class Evaluation:
    """The outcome of one rule on one measurement; only ``alert`` raises an event, every state is visible."""

    rule: str
    metric: str
    subject: str
    segment: str
    state: str
    value: float | None
    threshold: float
    severity: str
    op: str = ">"
    reason: str = ""
    count: int = 0
    history: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "count": self.count,
            "history": self.history,
            "metric": self.metric,
            "op": self.op,
            "reason": self.reason,
            "rule": self.rule,
            "segment": self.segment,
            "severity": self.severity,
            "state": self.state,
            "subject": self.subject,
            "threshold": self.threshold,
            "value": self.value,
        }


def _matches(rule: AlertRule, m: Measurement) -> bool:
    return m.metric == rule.metric and rule.subject in ("*", m.subject) and rule.segment in ("*", m.segment)


def evaluate(
    measurements: Sequence[Measurement], rules: Sequence[AlertRule], history: History | None = None
) -> list[Evaluation]:
    """Evaluate each rule on each matching measurement.

    State order: ``unavailable`` (the metric's reason), ``insufficient_count``, ``insufficient_history``
    (fewer prior values in ``history`` than ``min_history``), then ``alert`` or ``ok``.
    """
    results: list[Evaluation] = []
    for rule in rules:
        for m in measurements:
            if not _matches(rule, m):
                continue
            prior = len((history or {}).get(m.key, ()))
            common = {
                "rule": rule.name, "metric": m.metric, "subject": m.subject, "segment": m.segment,
                "value": m.value, "threshold": rule.threshold, "severity": rule.severity, "op": rule.op,
                "count": m.count, "history": prior,
            }  # fmt: skip
            if m.status == UNAVAILABLE or m.value is None:
                results.append(Evaluation(state=UNAVAILABLE, reason=m.reason or "unavailable", **common))
            elif m.count < rule.min_count:
                results.append(
                    Evaluation(state=INSUFFICIENT_COUNT, reason=f"{m.count} < min_count {rule.min_count}", **common)
                )
            elif prior < rule.min_history:
                results.append(
                    Evaluation(state=INSUFFICIENT_HISTORY, reason=f"{prior} < min_history {rule.min_history}", **common)
                )
            else:
                results.append(Evaluation(state=ALERT if rule.breached(m.value) else OK, **common))
    return results


def alert_events(
    reference: Reference, evaluations: Sequence[Evaluation], window_end: str = "", run_id: str = ""
) -> list[AlertEvent]:
    """Return one ``AlertEvent`` (class ``statistical_alert``) per evaluation in the ``alert`` state."""
    events: list[AlertEvent] = []
    for e in evaluations:
        if e.state != ALERT:
            continue
        where = f"{e.metric}:{e.subject}" + (f":{e.segment}" if e.segment else "")
        events.append(
            AlertEvent(
                kind=KIND,
                subject=f"{reference.name}:{where}",
                message=f"{where} = {e.value:.6g} breaches rule {e.rule} ({e.op} {e.threshold:g})",
                severity=e.severity,
                error_class=ERROR_CLASS,
                target=reference.name,
                run_id=run_id,
                plan_fingerprint=reference.fingerprint,
                occurred_at=window_end,
                details={
                    "count": e.count,
                    "rule": e.rule,
                    "segment": e.segment,
                    "value": e.value,
                    "version": reference.version,
                },
            )
        )
    return events
