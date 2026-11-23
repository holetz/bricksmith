"""The ``model_monitoring`` declaration of a task: which model, which prediction columns, references and metrics.

A declaration names the prediction table roles (score, label, grade, segment, sample), the development and
out-of-time references, the metric families, the limits that color each result and the gate the run applies.
Unknown keys and invalid values are errors, so a typo fails the plan instead of silently watching nothing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import BOTH, HIGHER, Limits

KEY = "model_monitoring"
DEVELOPMENT = "development"
OUT_OF_TIME = "out_of_time"
REFERENCES = (DEVELOPMENT, OUT_OF_TIME)
SCORE_PSI = "score_psi"
FEATURE_CSI = "feature_csi"
GRADE_PSI = "grade_psi"
DISCRIMINATION = "discrimination"
CALIBRATION = "calibration"
VOLUMETRY = "volumetry"
COMPLETENESS = "completeness"
FAMILIES = (SCORE_PSI, FEATURE_CSI, GRADE_PSI, DISCRIMINATION, CALIBRATION, VOLUMETRY, COMPLETENESS)
#: Families that need a label; their data period is the monitoring period moved back by ``label_lag``.
LABEL_FAMILIES = frozenset({DISCRIMINATION, CALIBRATION})
#: The metrics each family writes (column ``""`` for whole-population metrics).
FAMILY_METRICS: dict[str, tuple[str, ...]] = {
    SCORE_PSI: ("psi",),
    FEATURE_CSI: ("csi",),
    GRADE_PSI: ("grade_psi",),
    DISCRIMINATION: ("ks", "auc", "gini"),
    CALIBRATION: ("brier", "ece", "calibration_ratio"),
    VOLUMETRY: ("row_count", "volume_ratio"),
    COMPLETENESS: ("null_rate",),
}
METRICS = tuple(m for f in FAMILIES for m in FAMILY_METRICS[f])
DEFAULT_LIMITS: dict[str, Limits | None] = {
    "psi": Limits(0.10, 0.25, HIGHER),
    "csi": Limits(0.10, 0.25, HIGHER),
    "grade_psi": Limits(0.10, 0.25, HIGHER),
    "volume_ratio": Limits(0.20, 0.50, BOTH, center=1.0),
}
GATE_CONDITIONS = ("red", "yellow")
KEYS = frozenset({
    "output", "model", "model_version", "model_version_column", "period", "score", "label", "grade", "segment",
    "sample", "references", "features", "feature_binning", "critical_fields", "metrics", "label_lag", "limits",
    "gate", "min_rows", "max_periods", "score_bins", "calibration_bins", "scope", "prefix", "epsilon",
})  # fmt: skip


def _fail(message: str) -> MonitorError:
    return MonitorError(f"model_monitoring: {message}")


@dataclass(frozen=True)
class Sample:
    """Which rows make a reference: a value of the sample column, or an inclusive period range (text order)."""

    name: str
    value: str = ""
    start: str = ""
    end: str = ""

    def __post_init__(self) -> None:
        """Require exactly one of a sample value or a complete, ordered period range."""
        ranged = bool(self.start) or bool(self.end)
        if bool(self.value) == ranged or (ranged and not (self.start and self.end and self.start <= self.end)):
            msg = f"reference {self.name!r} needs either sample: <value> or start <= end"
            raise _fail(msg)

    @property
    def label(self) -> str:
        """Return a short description (``sample=dev`` or ``2025-01..2025-06``)."""
        return f"sample={self.value}" if self.value else f"{self.start}..{self.end}"

    def to_dict(self) -> dict[str, str]:
        """Return the serializable form."""
        return {"end": self.end, "name": self.name, "start": self.start, "value": self.value}


@dataclass(frozen=True)
class Gate:
    """What the run does: ``fail_on`` colors in the latest monitoring period, and the policy for engine errors."""

    fail_on: tuple[str, ...] = ()
    on_error: str = "warn"

    def __post_init__(self) -> None:
        """Validate the conditions and the error policy."""
        if set(self.fail_on) - set(GATE_CONDITIONS) or self.on_error not in ("warn", "fail"):
            msg = f"gate fail_on takes {', '.join(GATE_CONDITIONS)} and on_error warn or fail"
            raise _fail(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {"fail_on": list(self.fail_on), "on_error": self.on_error}


@dataclass(frozen=True)
class ModelMonitorConfig:
    """A validated ``model_monitoring`` declaration for one prediction output of a task."""

    model: str
    period: str
    score: str
    development: Sample
    output: str = ""
    model_version: str = ""
    model_version_column: str = ""
    label: str = ""
    grade: str = ""
    segment: str = ""
    sample: str = ""
    out_of_time: Sample | None = None
    features: dict[str, dict[str, Any]] = field(default_factory=dict)
    stability_dataset: str = ""
    stability_prefix: str = "stability_"
    critical_fields: tuple[str, ...] = ()
    metrics: tuple[str, ...] = FAMILIES
    label_lag: int = 0
    limits: dict[str, Limits | None] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    gate: Gate = field(default_factory=Gate)
    min_rows: int = 30
    max_periods: int = 24
    score_bins: int = 10
    calibration_bins: int = 10
    scope: str = ""
    prefix: str = "model_monitoring_"
    epsilon: float = 1e-4

    @property
    def references(self) -> tuple[Sample, ...]:
        """Return the declared references, development first."""
        return (self.development,) if self.out_of_time is None else (self.development, self.out_of_time)

    def families(self) -> tuple[str, ...]:
        """Return the families that apply: label families need a label, and features, grade or fields their roles."""
        needs = {
            FEATURE_CSI: bool(self.features),
            GRADE_PSI: bool(self.grade),
            DISCRIMINATION: bool(self.label),
            CALIBRATION: bool(self.label),
            COMPLETENESS: bool(self.critical_fields),
        }
        return tuple(f for f in self.metrics if needs.get(f, True))

    def limits_for(self, metric: str) -> Limits | None:
        """Return the limits of ``metric`` (``None``: no rule)."""
        return self.limits.get(metric)

    def binning_fingerprint(self) -> str:
        """Hash what decides the bins; limits, gate and lags never trigger a refit."""
        content = {
            "development": self.development.to_dict(),
            "features": self.features,
            "grade": self.grade,
            "period": self.period,
            "sample": self.sample,
            "score": self.score,
            "score_bins": self.score_bins,
            "stability": [self.stability_dataset, self.stability_prefix] if self.stability_dataset else [],
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "calibration_bins": self.calibration_bins,
            "critical_fields": list(self.critical_fields),
            "development": self.development.to_dict(),
            "epsilon": self.epsilon,
            "features": self.features,
            "gate": self.gate.to_dict(),
            "grade": self.grade,
            "label": self.label,
            "label_lag": self.label_lag,
            "limits": {m: lim.to_dict() if lim else None for m, lim in sorted(self.limits.items())},
            "max_periods": self.max_periods,
            "metrics": list(self.metrics),
            "min_rows": self.min_rows,
            "model": self.model,
            "model_version": self.model_version,
            "model_version_column": self.model_version_column,
            "out_of_time": self.out_of_time.to_dict() if self.out_of_time else None,
            "output": self.output,
            "period": self.period,
            "prefix": self.prefix,
            "sample": self.sample,
            "scope": self.scope,
            "score": self.score,
            "score_bins": self.score_bins,
            "segment": self.segment,
            "stability_dataset": self.stability_dataset,
            "stability_prefix": self.stability_prefix,
        }


def _name(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{what} must be a non-empty name, got {value!r}"
        raise _fail(msg)
    return value


def _optional(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    return "" if value in (None, "") else _name(value, key)


def _int(data: dict[str, Any], key: str, default: int, minimum: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        msg = f"{key} must be an integer >= {minimum}, got {value!r}"
        raise _fail(msg)
    return value


def _sample(name: str, value: object, sample_column: str) -> Sample:
    if not isinstance(value, dict) or set(value) - {"sample", "start", "end"}:
        msg = f"reference {name!r} must be a mapping with sample, or start and end; got {value!r}"
        raise _fail(msg)
    if "sample" in value and not sample_column:
        msg = f"reference {name!r} selects by sample value, so declare the sample column"
        raise _fail(msg)
    return Sample(name, str(value.get("sample", "")), str(value.get("start", "")), str(value.get("end", "")))


def _references(value: object, sample_column: str) -> tuple[Sample, Sample | None]:
    if not isinstance(value, dict) or DEVELOPMENT not in value or set(value) - set(REFERENCES):
        msg = f"references must map {DEVELOPMENT} (required) and {OUT_OF_TIME} (optional) to their rows"
        raise _fail(msg)
    development = _sample(DEVELOPMENT, value[DEVELOPMENT], sample_column)
    oot = value.get(OUT_OF_TIME)
    return development, None if oot is None else _sample(OUT_OF_TIME, oot, sample_column)


def _features(value: object) -> dict[str, dict[str, Any]]:
    if value in (None, []):
        return {}
    if isinstance(value, list):
        return {_name(c, "features item"): {} for c in value}
    if isinstance(value, dict):
        return {_name(k, "features key"): dict(v or {}) for k, v in value.items()}
    msg = "features must be a list of column names or a mapping of name to bin configuration"
    raise _fail(msg)


def _binning(value: object) -> tuple[str, str]:
    if value in (None, "own"):
        return "", "stability_"
    if isinstance(value, dict) and "stability" in value and not set(value) - {"stability", "prefix"}:
        prefix = str(value.get("prefix", "stability_"))
        return _name(value["stability"], "feature_binning stability"), prefix
    msg = "feature_binning must be 'own' or {stability: <dataset>, prefix?: <stability prefix>}"
    raise _fail(msg)


def _metrics(value: object) -> tuple[str, ...]:
    items = list(FAMILIES) if value is None else value
    if not isinstance(items, list) or not items or set(items) - set(FAMILIES):
        msg = f"metrics must be a non-empty list among {', '.join(FAMILIES)}"
        raise _fail(msg)
    return tuple(f for f in FAMILIES if f in items)


def _limits(value: object) -> dict[str, Limits | None]:
    if not isinstance(value, dict):
        msg = "limits must map a metric to {green, red, direction, center} (or null to disable)"
        raise _fail(msg)
    result = dict(DEFAULT_LIMITS)
    for metric, item in value.items():
        if metric not in METRICS:
            msg = f"unknown metric {metric!r} in limits; known: {', '.join(METRICS)}"
            raise _fail(msg)
        result[metric] = None if item is None else Limits.from_dict(item)
    return result


def _gate(value: object) -> Gate:
    if value is None:
        return Gate()
    if not isinstance(value, dict) or set(value) - {"fail_on", "on_error"}:
        msg = f"gate must be a mapping with fail_on and on_error, got {value!r}"
        raise _fail(msg)
    fail_on = value.get("fail_on", [])
    items = [fail_on] if isinstance(fail_on, str) else fail_on
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        msg = "gate fail_on must be a condition or a list of conditions"
        raise _fail(msg)
    return Gate(tuple(items), str(value.get("on_error", "warn")))


def _prefix(value: object) -> str:
    prefix = str(value)
    if not prefix.replace("_", "a").isalnum():
        msg = f"prefix must be letters, digits and underscores, got {prefix!r}"
        raise _fail(msg)
    return prefix


def parse_config(data: object) -> ModelMonitorConfig:
    """Validate one declaration mapping into a :class:`ModelMonitorConfig` (unknown keys are errors)."""
    if not isinstance(data, dict):
        msg = f"a model_monitoring declaration must be a mapping, got {data!r}"
        raise _fail(msg)
    unknown = sorted(set(data) - KEYS)
    if unknown:
        msg = f"unknown keys {', '.join(unknown)}"
        raise _fail(msg)
    missing = [k for k in ("model", "period", "score", "references") if k not in data]
    if missing:
        msg = f"{', '.join(missing)} required"
        raise _fail(msg)
    if data.get("model_version") and data.get("model_version_column"):
        msg = "declare model_version (a literal) or model_version_column, not both"
        raise _fail(msg)
    sample = _optional(data, "sample")
    development, oot = _references(data["references"], sample)
    stability_dataset, stability_prefix = _binning(data.get("feature_binning"))
    critical = data.get("critical_fields", [])
    if not isinstance(critical, list):
        msg = "critical_fields must be a list of column names"
        raise _fail(msg)
    try:
        return ModelMonitorConfig(
            model=_name(data["model"], "model"),
            period=_name(data["period"], "period"),
            score=_name(data["score"], "score"),
            development=development,
            output=str(data.get("output", "")),
            model_version=str(data.get("model_version", "") or ""),
            model_version_column=_optional(data, "model_version_column"),
            label=_optional(data, "label"),
            grade=_optional(data, "grade"),
            segment=_optional(data, "segment"),
            sample=sample,
            out_of_time=oot,
            features=_features(data.get("features")),
            stability_dataset=stability_dataset,
            stability_prefix=_prefix(stability_prefix),
            critical_fields=tuple(_name(c, "critical_fields item") for c in critical),
            metrics=_metrics(data.get("metrics")),
            label_lag=_int(data, "label_lag", 0, 0),
            limits=_limits(data.get("limits", {})),
            gate=_gate(data.get("gate")),
            min_rows=_int(data, "min_rows", 30, 0),
            max_periods=_int(data, "max_periods", 24, 1),
            score_bins=_int(data, "score_bins", 10, 1),
            calibration_bins=_int(data, "calibration_bins", 10, 1),
            scope=str(data.get("scope", "")),
            prefix=_prefix(data.get("prefix", "model_monitoring_")),
            epsilon=float(data.get("epsilon", 1e-4)),
        )
    except (TypeError, ValueError) as error:
        msg = f"invalid value: {error}"
        raise _fail(msg) from None


def parse_declarations(value: object) -> tuple[ModelMonitorConfig, ...]:
    """Validate the ``model_monitoring`` metadata of a task: one mapping, or a list with distinct outputs."""
    items = value if isinstance(value, list) else [value]
    configs = tuple(parse_config(item) for item in items)
    outputs = [c.output for c in configs]
    if len(set(outputs)) != len(outputs):
        msg = "several declarations name the same output; give each an explicit, distinct output"
        raise _fail(msg)
    return configs


__all__ = [
    "CALIBRATION",
    "COMPLETENESS",
    "DEFAULT_LIMITS",
    "DEVELOPMENT",
    "DISCRIMINATION",
    "FAMILIES",
    "FAMILY_METRICS",
    "FEATURE_CSI",
    "GRADE_PSI",
    "KEY",
    "LABEL_FAMILIES",
    "METRICS",
    "OUT_OF_TIME",
    "SCORE_PSI",
    "VOLUMETRY",
    "Gate",
    "ModelMonitorConfig",
    "Sample",
    "parse_config",
    "parse_declarations",
]
