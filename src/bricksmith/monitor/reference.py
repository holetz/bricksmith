"""The versioned, serializable monitoring reference and its alert policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from bricksmith.monitor.bins import BinSpec
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stats import EPSILON

OPS = (">", ">=", "<", "<=")
SEVERITIES = ("info", "warning", "critical")
DEFAULT_CONFIG: dict[str, Any] = {"calibration_bins": 10, "epsilon": EPSILON, "stability_period": ""}
PERIODS = ("", "day", "month")


@dataclass(frozen=True)
class AlertRule:
    """Threshold rule over one metric; ``subject``/``segment`` ``"*"`` match any (default segment: whole population)."""

    name: str
    metric: str
    op: str
    threshold: float
    severity: str = "warning"
    subject: str = "*"
    segment: str = ""
    min_count: int = 0
    min_history: int = 0

    def __post_init__(self) -> None:
        """Validate the operator and severity."""
        if self.op not in OPS or self.severity not in SEVERITIES or not self.name or not self.metric:
            msg = f"rule {self.name!r}: op must be one of {', '.join(OPS)}, severity one of {', '.join(SEVERITIES)}"
            raise MonitorError(msg)
        if self.min_count < 0 or self.min_history < 0:
            msg = f"rule {self.name!r}: min_count and min_history cannot be negative"
            raise MonitorError(msg)

    def breached(self, value: float) -> bool:
        """Return whether ``value`` crosses the threshold."""
        return {">": value > self.threshold, ">=": value >= self.threshold, "<": value < self.threshold}.get(
            self.op, value <= self.threshold
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "metric": self.metric,
            "min_count": self.min_count,
            "min_history": self.min_history,
            "name": self.name,
            "op": self.op,
            "segment": self.segment,
            "severity": self.severity,
            "subject": self.subject,
            "threshold": self.threshold,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlertRule:
        """Build a rule from a mapping, rejecting unknown keys."""
        unknown = set(data) - set(cls.__dataclass_fields__)
        if unknown:
            msg = f"unknown alert rule keys: {', '.join(sorted(unknown))}"
            raise MonitorError(msg)
        return cls(**{**data, "threshold": float(data["threshold"])})


@dataclass(frozen=True)
class Reference:
    """What a window is compared with: fitted bins, column roles, metric config, windows and alert policy."""

    name: str
    version: int
    columns: tuple[BinSpec, ...]
    fit_source: str = ""
    fit_rows: int = 0
    fit_window: dict[str, str] = field(default_factory=dict)
    score: str = ""
    label: str = ""
    segment: str = ""
    time: str = ""
    metric_config: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_CONFIG))
    evaluation_window: dict[str, Any] = field(default_factory=dict)
    alert_policy: tuple[AlertRule, ...] = ()

    def __post_init__(self) -> None:
        """Validate identity, roles and the metric configuration."""
        names = [c.column for c in self.columns]
        if not self.name or self.version < 1 or len(set(names)) != len(names):
            msg = "a reference needs a name, a version >= 1 and distinct columns"
            raise MonitorError(msg)
        if self.score and self.score not in names:
            msg = f"score column {self.score!r} has no fitted bins"
            raise MonitorError(msg)
        if self.metric_config.get("stability_period", "") not in PERIODS or self.metric_config["calibration_bins"] < 1:
            msg = "metric_config: stability_period must be '', 'day' or 'month' and calibration_bins >= 1"
            raise MonitorError(msg)

    def column(self, name: str) -> BinSpec:
        """Return the bin spec of ``name``."""
        for spec in self.columns:
            if spec.column == name:
                return spec
        msg = f"reference {self.name!r} has no column {name!r}"
        raise MonitorError(msg)

    def body(self) -> dict[str, Any]:
        """Return the canonical content the fingerprint covers."""
        return {
            "alert_policy": [r.to_dict() for r in self.alert_policy],
            "columns": [c.to_dict() for c in self.columns],
            "evaluation_window": dict(self.evaluation_window),
            "fit_rows": self.fit_rows,
            "fit_source": self.fit_source,
            "fit_window": dict(self.fit_window),
            "label": self.label,
            "metric_config": dict(self.metric_config),
            "name": self.name,
            "score": self.score,
            "segment": self.segment,
            "time": self.time,
            "version": self.version,
        }

    @property
    def fingerprint(self) -> str:
        """Return the SHA-256 of the canonical content."""
        return hashlib.sha256(json.dumps(self.body(), sort_keys=True).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form (content plus fingerprint)."""
        return {**self.body(), "fingerprint": self.fingerprint}

    def to_json(self) -> str:
        """Return stable JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Reference:
        """Rebuild a reference; a fingerprint that does not match the content is an error."""
        ref = cls(
            name=str(data["name"]),
            version=int(data["version"]),
            columns=tuple(BinSpec.from_dict(c) for c in data["columns"]),
            fit_source=str(data.get("fit_source", "")),
            fit_rows=int(data.get("fit_rows", 0)),
            fit_window=dict(data.get("fit_window", {})),
            score=str(data.get("score", "")),
            label=str(data.get("label", "")),
            segment=str(data.get("segment", "")),
            time=str(data.get("time", "")),
            metric_config={**DEFAULT_CONFIG, **data.get("metric_config", {})},
            evaluation_window=dict(data.get("evaluation_window", {})),
            alert_policy=tuple(AlertRule.from_dict(r) for r in data.get("alert_policy", ())),
        )
        if data.get("fingerprint") and data["fingerprint"] != ref.fingerprint:
            msg = f"reference {ref.name!r} v{ref.version}: fingerprint does not match its content"
            raise MonitorError(msg)
        return ref

    @classmethod
    def from_json(cls, text: str) -> Reference:
        """Rebuild a reference from ``to_json`` output."""
        return cls.from_dict(json.loads(text))


def resolve_window(reference: Reference, as_of: str | None = None) -> dict[str, str]:
    """Return ``{column, start, end}`` from the evaluation window: explicit bounds, or ``days`` back from ``as_of``."""
    spec = reference.evaluation_window
    column = str(spec.get("column", reference.time))
    if "start" in spec and "end" in spec:
        return {"column": column, "end": str(spec["end"]), "start": str(spec["start"])}
    if "days" in spec and as_of and column:
        end = date.fromisoformat(as_of)
        return {
            "column": column,
            "end": end.isoformat(),
            "start": (end - timedelta(days=int(spec["days"]))).isoformat(),
        }
    return {}
