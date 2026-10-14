"""Outside-project adapter: monitor DataFrames (or lists of rows) with no project, tasks or tables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.alerts import ALERT, Evaluation, History, alert_events, evaluate
from bricksmith.monitor.fit import fit_rows
from bricksmith.monitor.measure import Measurement, measure_rows

if TYPE_CHECKING:
    from bricksmith.monitor.reference import Reference
    from bricksmith.ops.notify import AlertEvent


def is_rows(data: object) -> bool:
    """Return whether ``data`` is a list of mappings (pure engine) rather than a Spark DataFrame."""
    return isinstance(data, Sequence) and not isinstance(data, str)


def fit_reference(data: Any, columns: Mapping[str, Mapping[str, Any]], **meta: Any) -> Reference:  # noqa: ANN401 - rows or DataFrame, reference metadata
    """Fit a versioned reference on rows (pure) or a Spark DataFrame; ``meta`` are ``build_reference`` keywords."""
    if is_rows(data):
        return fit_rows(data, columns, **meta)
    from bricksmith.monitor.spark import fit_spark  # noqa: PLC0415 - heavy integration, loaded where used

    return fit_spark(data, columns, **meta)


def measure(
    reference: Reference,
    data: Any,  # noqa: ANN401 - rows or DataFrame
    window: Mapping[str, str] | None = None,
    *,
    segments: bool = True,
) -> list[Measurement]:
    """Compare rows or a DataFrame with a reference; the reference is never refit."""
    if is_rows(data):
        return measure_rows(reference, data, window, segments=segments)
    from bricksmith.monitor.spark import measure_spark  # noqa: PLC0415

    return measure_spark(reference, data, window, segments=segments)


@dataclass(frozen=True)
class MonitorReport:
    """Measurements, rule evaluations (every state) and the alert events of one comparison."""

    reference: Reference
    measurements: tuple[Measurement, ...]
    evaluations: tuple[Evaluation, ...]
    alerts: tuple[AlertEvent, ...]

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "alerts": [a.to_dict() for a in self.alerts],
            "evaluations": [e.to_dict() for e in self.evaluations],
            "measurements": [m.to_dict() for m in self.measurements],
            "reference": {
                "fingerprint": self.reference.fingerprint,
                "name": self.reference.name,
                "version": self.reference.version,
            },
        }

    def to_text(self) -> str:
        """Return a compact text rendering; unavailable metrics show their reason."""
        lines = [f"reference {self.reference.name} v{self.reference.version}"]
        for m in self.measurements:
            seg = f" [{m.segment}]" if m.segment else ""
            shown = f"{m.value:.6g}" if m.value is not None else f"unavailable ({m.reason})"
            lines.append(f"  {m.metric:<12} {m.subject:<16}{seg} {shown}")
        lines += [
            f"  {e.state:<22} {e.rule}: {e.metric} {e.subject} {e.reason}".rstrip()
            for e in self.evaluations
            if e.state != "ok"
        ]
        lines += [f"ALERT {a.severity}: {a.message}" for a in self.alerts]
        return "\n".join(lines)


def monitor_frames(  # noqa: PLR0913 - one keyword per documented input
    current: Any,  # noqa: ANN401 - rows or DataFrame
    *,
    reference: Reference | None = None,
    baseline: Any = None,  # noqa: ANN401
    columns: Mapping[str, Mapping[str, Any]] | None = None,
    window: Mapping[str, str] | None = None,
    history: History | None = None,
    segments: bool = True,
    **meta: Any,  # noqa: ANN401 - reference metadata when fitting from ``baseline``
) -> MonitorReport:
    """Fit (from ``baseline``) or reuse a reference, measure ``current`` and evaluate its alert policy."""
    if reference is None:
        if baseline is None or columns is None:
            from bricksmith.monitor.errors import MonitorError  # noqa: PLC0415

            msg = "pass a reference, or baseline data with columns to fit one"
            raise MonitorError(msg)
        reference = fit_reference(baseline, columns, **{"name": "adhoc", "version": 1, **meta})
    measurements = measure(reference, current, window, segments=segments)
    evaluations = evaluate(measurements, reference.alert_policy, history)
    fired = [e for e in evaluations if e.state == ALERT]
    events = alert_events(reference, fired, (window or {}).get("end", ""))
    return MonitorReport(reference, tuple(measurements), tuple(evaluations), tuple(events))
