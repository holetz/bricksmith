"""The ``stability`` declaration of a task: what is watched, against which baseline, and what the run does about it."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.infer import InferenceOptions
from bricksmith.monitor.limits import ABSOLUTE, BOTH, DIRECTIONS, HIGHER, Limits
from bricksmith.monitor.persistence import PersistencePolicy
from bricksmith.monitor.rules import LimitRule, Resolution, parse_rules, resolve
from bricksmith.monitor.trend import TrendPolicy

if TYPE_CHECKING:
    from pathlib import Path

PSI = "psi"
NULL_RATE = "null_rate"
NULL_RATE_DELTA = "null_rate_delta"
UNSEEN_RATE = "unseen_rate"
UNSEEN_RATE_DELTA = "unseen_rate_delta"
MEAN = "mean"
MEAN_SHIFT = "mean_shift"
ROW_COUNT = "row_count"
VOLUME_CHANGE = "volume_change"
WEIGHT_CHANGE = "weight_change"
REVISION_ROW_CHANGE = "revision_row_change"
REVISION_PSI = "revision_psi"
REVISION_MEAN_SHIFT = "revision_mean_shift"
#: Metrics judged per column, and per period and segment for the whole table (column ``""``).
COLUMN_METRICS = (PSI, NULL_RATE, NULL_RATE_DELTA, UNSEEN_RATE, UNSEEN_RATE_DELTA, MEAN, MEAN_SHIFT)
TABLE_METRICS = (ROW_COUNT, VOLUME_CHANGE, WEIGHT_CHANGE)
#: Metrics comparing a period with its own first publication; judged only when ``revision`` is declared.
REVISION_METRICS = (REVISION_ROW_CHANGE, REVISION_PSI, REVISION_MEAN_SHIFT)
METRICS = (*COLUMN_METRICS, *TABLE_METRICS, *REVISION_METRICS)
#: Metrics computed from bin shares, so too few rows make them unreliable (``min_rows``).
DISTRIBUTION_METRICS = frozenset({
    PSI, NULL_RATE_DELTA, UNSEEN_RATE_DELTA, MEAN_SHIFT, REVISION_PSI, REVISION_MEAN_SHIFT,
})  # fmt: skip
DEFAULT_LIMITS: dict[str, Limits | None] = {
    PSI: Limits(0.10, 0.25, HIGHER),
    NULL_RATE_DELTA: Limits(0.05, 0.15, HIGHER),
    UNSEEN_RATE_DELTA: Limits(0.05, 0.15, HIGHER),
    MEAN_SHIFT: Limits(0.25, 0.50, BOTH),
    VOLUME_CHANGE: Limits(0.20, 0.50, BOTH),
    WEIGHT_CHANGE: Limits(0.20, 0.50, BOTH),
}
#: Used only for the revision metrics, so declarations without ``revision`` keep their serialized limits.
REVISION_LIMITS: dict[str, Limits] = {
    REVISION_ROW_CHANGE: Limits(0.01, 0.05, BOTH),
    REVISION_PSI: Limits(0.05, 0.10, HIGHER),
    REVISION_MEAN_SHIFT: Limits(0.10, 0.25, BOTH),
}
DEFAULT_TREND_METRICS = (PSI, VOLUME_CHANGE)
#: Trend direction of the metrics that carry no limits; a limit's own direction (declared, per column or by rule) wins.
DEFAULT_TREND_DIRECTIONS: dict[str, str] = {ROW_COUNT: BOTH, MEAN: BOTH, NULL_RATE: HIGHER, UNSEEN_RATE: HIGHER}
FIXED = "fixed"
PREVIOUS = "previous"
ROLLING = "rolling"
BASELINES = (FIXED, PREVIOUS, ROLLING)
GATE_CONDITIONS = ("red", "yellow", "critical", "alert")
GATE_PERIODS = ("latest", "profiled")
TABLES = ("binnings", "profiles", "results", "alerts", "runs")
KEYS = frozenset({
    "output", "period", "columns", "exclude", "segments", "weight", "baseline", "fit_periods", "limits",
    "column_limits", "trend", "trend_metrics", "trend_directions", "persistence", "gate", "min_rows", "refresh_periods",
    "max_periods", "max_segments", "scope", "prefix", "inference", "dataset", "epsilon", "rules", "notify", "revision",
    "noise",
})  # fmt: skip
OPENED = "opened"
ESCALATED = "escalated"
RECURRED = "recurred"
TRANSITIONS = (OPENED, ESCALATED, RECURRED)
#: Transitions of an alert that stays open: delivered only when ``remind_after`` or ``material_change`` is declared.
REMINDED = "reminded"
CHANGED = "changed"
SEVERITIES = ("warning", "critical")
_NOTIFY_KEYS = frozenset({"on", "min_severity", "digest", "top", "remind_after", "material_change"})


def _fail(message: str) -> MonitorError:
    return MonitorError(f"stability: {message}")


@dataclass(frozen=True)
class Baseline:
    """What a period is compared with.

    ``fixed``: the periods from ``start`` to ``end`` (inclusive, compared as text), by default the periods the
    binning was fitted on. ``previous``: the period ``lag`` steps earlier. ``rolling``: the ``periods`` earlier
    periods, needing at least ``min_periods`` of them.
    """

    kind: str = FIXED
    start: str = ""
    end: str = ""
    lag: int = 1
    periods: int = 3
    min_periods: int = 1

    def __post_init__(self) -> None:
        """Validate the kind and its parameters."""
        if self.kind not in BASELINES:
            msg = f"baseline kind must be one of {', '.join(BASELINES)}, got {self.kind!r}"
            raise _fail(msg)
        if bool(self.start) != bool(self.end) or (self.start and self.start > self.end):
            msg = "a fixed baseline range needs start <= end (both, or neither)"
            raise _fail(msg)
        if self.lag < 1 or self.periods < 1 or not 1 <= self.min_periods <= self.periods:
            msg = "baseline lag and periods must be >= 1 and 1 <= min_periods <= periods"
            raise _fail(msg)

    @property
    def label(self) -> str:
        """Return a short readable description (``fixed 2026-01..2026-03``, ``previous 1``, ``rolling 3``)."""
        if self.kind == FIXED:
            return f"fixed {self.start}..{self.end}" if self.start else "fixed fit periods"
        return f"previous {self.lag}" if self.kind == PREVIOUS else f"rolling {self.periods}"

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "end": self.end,
            "kind": self.kind,
            "lag": self.lag,
            "min_periods": self.min_periods,
            "periods": self.periods,
            "start": self.start,
        }


@dataclass(frozen=True)
class Gate:
    """What the run does: ``fail_on`` conditions, the periods they judge, and the policy for engine errors.

    ``periods`` is ``latest`` (the latest period only) or ``profiled`` (every window period profiled in the run).
    """

    fail_on: tuple[str, ...] = ()
    on_error: str = "warn"
    periods: str = "latest"

    def __post_init__(self) -> None:
        """Validate the conditions, the error policy and the judged periods."""
        unknown = set(self.fail_on) - set(GATE_CONDITIONS)
        if unknown or self.on_error not in ("warn", "fail"):
            msg = f"gate fail_on takes {', '.join(GATE_CONDITIONS)} and on_error warn or fail"
            raise _fail(msg)
        if self.periods not in GATE_PERIODS:
            msg = f"gate periods takes {' or '.join(GATE_PERIODS)}, got {self.periods!r}"
            raise _fail(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form (``periods`` only when not the default: fingerprints stay)."""
        data: dict[str, Any] = {"fail_on": list(self.fail_on), "on_error": self.on_error}
        if self.periods != "latest":
            data["periods"] = self.periods
        return data


@dataclass(frozen=True)
class NotifyPolicy:
    """Which alert transitions are delivered through the run's notifier, and from which severity.

    No ``on`` transition means notifications are switched off (``notify: false``). ``digest`` delivers one message
    per run listing the incident and its ``top`` causes instead of one per alert. ``remind_after`` (periods flagged)
    and ``material_change`` (relative change of the last value) re-notify an alert that stays open; both are off by
    default, so an unchanged open alert never notifies.
    """

    on: tuple[str, ...] = TRANSITIONS
    min_severity: str = "warning"
    digest: bool = False
    top: int = 5
    remind_after: int = 0
    material_change: float = 0.0

    def __post_init__(self) -> None:
        """Validate the transitions, the severity and the digest and re-notification options."""
        if set(self.on) - set(TRANSITIONS) or self.min_severity not in SEVERITIES:
            msg = f"notify on takes {', '.join(TRANSITIONS)} and min_severity {' or '.join(SEVERITIES)}"
            raise _fail(msg)
        if self.top < 1 or self.remind_after < 0 or not math.isfinite(self.material_change) or self.material_change < 0:
            msg = "notify top must be >= 1, remind_after >= 0 and material_change a finite number >= 0"
            raise _fail(msg)

    def wants(self, transition: str, severity: str) -> bool:
        """Tell whether a ``transition`` of an alert of ``severity`` is delivered."""
        if SEVERITIES.index(severity) < SEVERITIES.index(self.min_severity):
            return False
        if transition == REMINDED:
            return self.remind_after > 0
        if transition == CHANGED:
            return self.material_change > 0
        return transition in self.on

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form (the digest and re-notification options only when declared)."""
        data: dict[str, Any] = {"min_severity": self.min_severity, "on": list(self.on)}
        if self.digest:
            data["digest"] = True
            data["top"] = self.top
        if self.remind_after:
            data["remind_after"] = self.remind_after
        if self.material_change:
            data["material_change"] = self.material_change
        return data


@dataclass(frozen=True)
class NoiseBudget:
    """How many alerts one run may open: at most ``max_alerts_per_run`` and, with ``fdr``, only the discoveries.

    ``fdr`` is the Benjamini-Hochberg level applied over the trend p-values of the run: a new warning-severity
    trend alert opens only when its series is a discovery at that level. Stored open alerts are never filtered.
    """

    max_alerts_per_run: int | None = None
    fdr: float | None = None

    def __post_init__(self) -> None:
        """Validate the cap and the level."""
        if self.max_alerts_per_run is not None and self.max_alerts_per_run < 1:
            msg = "noise max_alerts_per_run must be an integer >= 1"
            raise _fail(msg)
        if self.fdr is not None and not 0 < self.fdr < 1:
            msg = "noise fdr must be a number between 0 and 1 (exclusive)"
            raise _fail(msg)
        if self.max_alerts_per_run is None and self.fdr is None:
            msg = "noise needs max_alerts_per_run, fdr or both"
            raise _fail(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form (only the declared parts)."""
        data: dict[str, Any] = {}
        if self.max_alerts_per_run is not None:
            data["max_alerts_per_run"] = self.max_alerts_per_run
        if self.fdr is not None:
            data["fdr"] = self.fdr
        return data


def parse_noise(value: object) -> NoiseBudget | None:
    """Validate a `noise` value: {max_alerts_per_run, fdr}; None or false is unset."""
    if value is None or value is False:
        return None
    if not isinstance(value, dict) or set(value) - {"max_alerts_per_run", "fdr"}:
        msg = f"noise must be a mapping with max_alerts_per_run and fdr, got {value!r}"
        raise _fail(msg)
    cap, level = value.get("max_alerts_per_run"), value.get("fdr")
    if (cap is not None and (isinstance(cap, bool) or not isinstance(cap, int))) or (
        level is not None and (isinstance(level, bool) or not isinstance(level, (int, float)))
    ):
        msg = f"noise max_alerts_per_run must be an integer and fdr a number, got {value!r}"
        raise _fail(msg)
    return NoiseBudget(cap, None if level is None else float(level))


def parse_notify(value: object) -> NotifyPolicy | None:
    """Validate a `notify` value: true (all transitions), false (off) or a mapping; None is unset."""
    if value is None:
        return None
    if isinstance(value, bool):
        return NotifyPolicy() if value else NotifyPolicy(on=())
    if not isinstance(value, dict) or set(value) - _NOTIFY_KEYS:
        msg = f"notify must be true, false or a mapping of {', '.join(sorted(_NOTIFY_KEYS))}, got {value!r}"
        raise _fail(msg)
    on = value.get("on", list(TRANSITIONS))
    items = [on] if isinstance(on, str) else on
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        msg = "notify on must be a transition or a list of transitions"
        raise _fail(msg)
    digest = value.get("digest", False)
    if not isinstance(digest, bool):
        msg = f"notify digest must be true or false, got {digest!r}"
        raise _fail(msg)
    try:
        return NotifyPolicy(
            tuple(items),
            str(value.get("min_severity", "warning")),
            digest,
            int(value.get("top", 5)),
            int(value.get("remind_after", 0)),
            float(value.get("material_change", 0.0)),
        )
    except (TypeError, ValueError):
        msg = f"notify top and remind_after must be integers and material_change a number, got {value!r}"
        raise _fail(msg) from None


@dataclass(frozen=True)
class StabilityConfig:
    """A validated ``stability`` declaration for one output of a task."""

    period: str
    output: str = ""
    dataset: str = ""
    columns: dict[str, dict[str, Any]] | None = None
    exclude: tuple[str, ...] = ()
    segments: tuple[tuple[str, ...], ...] = ()
    weight: str = ""
    baseline: Baseline = field(default_factory=Baseline)
    fit_periods: int = 1
    limits: dict[str, Limits | None] = field(default_factory=lambda: dict(DEFAULT_LIMITS))
    column_limits: dict[str, dict[str, Limits | None]] = field(default_factory=dict)
    trend: TrendPolicy | None = field(default_factory=TrendPolicy)
    trend_metrics: tuple[str, ...] = DEFAULT_TREND_METRICS
    trend_directions: dict[str, str] = field(default_factory=dict)
    persistence: PersistencePolicy = field(default_factory=PersistencePolicy)
    gate: Gate = field(default_factory=Gate)
    min_rows: int = 30
    refresh_periods: int = 1
    max_periods: int = 36
    max_segments: int = 50
    scope: str = ""
    prefix: str = "stability_"
    inference: InferenceOptions = field(default_factory=InferenceOptions)
    epsilon: float = 1e-4
    rules: tuple[LimitRule, ...] = ()
    notify: NotifyPolicy | None = None
    revision: bool = False
    noise: NoiseBudget | None = None

    @property
    def auto(self) -> bool:
        """Tell whether the columns are inferred from the data."""
        return self.columns is None

    def table(self, kind: str) -> str:
        """Return the short table name of ``kind`` (``binnings``, ``profiles``, ``results``, ``alerts``, ``runs``)."""
        return f"{self.prefix}{kind}"

    def limits_for(self, metric: str, column: str) -> Limits | None:
        """Return the limits of ``metric`` for ``column``: a column override, the declared limit, then the default."""
        override = self.column_limits.get(column, {})
        if metric in override:
            return override[metric]
        return self.limits[metric] if metric in self.limits else REVISION_LIMITS.get(metric)

    def trend_direction(self, metric: str) -> str:
        """Return the trend direction of a metric without limits: the declared one, else the default, else ``""``."""
        return self.trend_directions.get(metric) or DEFAULT_TREND_DIRECTIONS.get(metric, "")

    def resolve(self, *, dataset: str, column: str, segment: str, metric: str, period_date: str | None) -> Resolution:
        """Return the rules that apply to one result (see :func:`bricksmith.monitor.rules.resolve`)."""
        return resolve(
            self.rules, dataset=dataset, column=column, segment=segment, metric=metric, period_date=period_date
        )

    def limits_of(self, found: Resolution, metric: str, column: str) -> Limits | None:
        """Return the limits of a result: its limits rule when one applies, else the declaration's."""
        return found.limits_rule.limits if found.limits_rule else self.limits_for(metric, column)

    def excluded(self) -> tuple[str, ...]:
        """Return the columns never monitored: the declared exclusions, the period, segments and weight."""
        found = {*self.exclude, self.period, *(c for s in self.segments for c in s)}
        if self.weight:
            found.add(self.weight)
        return tuple(sorted(found))

    def binning_fingerprint(self) -> str:
        """Hash what decides the bins; limits, gate and baselines that do not fit bins never trigger a refit."""
        fit_range = [self.baseline.start, self.baseline.end] if self.baseline.kind == FIXED else []
        content = {
            "columns": self.columns,
            "exclude": list(self.excluded()),
            "fit_periods": self.fit_periods,
            "fit_range": fit_range,
            "inference": self.inference.__dict__,
            "period": self.period,
        }
        return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form (``rules``, ``notify``, ``revision`` only when declared: fingerprints stay)."""
        data = self._base_dict()
        if self.rules:
            data["rules"] = [r.to_dict() for r in self.rules]
        if self.notify is not None:
            data["notify"] = self.notify.to_dict()
        if self.revision:
            data["revision"] = True
        if self.trend_directions:
            data["trend_directions"] = dict(sorted(self.trend_directions.items()))
        if self.noise is not None:
            data["noise"] = self.noise.to_dict()
        return data

    def _base_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.to_dict(),
            "column_limits": {
                c: {m: lim.to_dict() if lim else None for m, lim in sorted(v.items())}
                for c, v in sorted(self.column_limits.items())
            },
            "columns": self.columns,
            "dataset": self.dataset,
            "epsilon": self.epsilon,
            "exclude": list(self.exclude),
            "fit_periods": self.fit_periods,
            "gate": self.gate.to_dict(),
            "inference": dict(self.inference.__dict__),
            "limits": {m: lim.to_dict() if lim else None for m, lim in sorted(self.limits.items())},
            "max_periods": self.max_periods,
            "max_segments": self.max_segments,
            "min_rows": self.min_rows,
            "output": self.output,
            "period": self.period,
            "persistence": self.persistence.to_dict(),
            "prefix": self.prefix,
            "refresh_periods": self.refresh_periods,
            "scope": self.scope,
            "segments": [list(s) for s in self.segments],
            "trend": self.trend.to_dict() if self.trend else None,
            "trend_metrics": list(self.trend_metrics),
            "weight": self.weight,
        }


def _name(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{what} must be a non-empty column name, got {value!r}"
        raise _fail(msg)
    return value


def _int(data: dict[str, Any], key: str, default: int, minimum: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        msg = f"{key} must be an integer >= {minimum}, got {value!r}"
        raise _fail(msg)
    return value


def _columns(value: object) -> dict[str, dict[str, Any]] | None:
    if value in (None, "auto"):
        return None
    if isinstance(value, list) and value:
        return {_name(c, "columns item"): {} for c in value}
    if isinstance(value, dict) and value:
        return {_name(k, "columns key"): dict(v or {}) for k, v in value.items()}
    msg = "columns must be 'auto', a list of names or a mapping of name to bin configuration"
    raise _fail(msg)


def _exclude(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        msg = f"exclude must be a list of column names, got {value!r}"
        raise _fail(msg)
    return tuple(_name(c, "exclude item") for c in value)


def _epsilon(value: object) -> float:
    number = math.nan
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            number = float(value)
        except ValueError:
            number = math.nan
    if not math.isfinite(number) or number <= 0:
        msg = f"epsilon must be a finite number > 0, got {value!r}"
        raise _fail(msg)
    return number


def _rules(value: object, root: Path | str | None) -> tuple[LimitRule, ...]:
    rules = parse_rules(value, owner="stability", root=root)
    for rule in rules:
        if rule.metric and rule.metric not in METRICS:
            msg = f"rule {rule.id!r}: unknown metric {rule.metric!r}; known: {', '.join(METRICS)}"
            raise _fail(msg)
    return rules


def _segments(value: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list):
        msg = "segments must be a list of column names or lists of column names"
        raise _fail(msg)
    found: list[tuple[str, ...]] = []
    for item in value:
        cols = (item,) if isinstance(item, str) else tuple(item) if isinstance(item, list) else ()
        if not cols:
            msg = f"segment {item!r} must be a column name or a non-empty list of names"
            raise _fail(msg)
        found.append(tuple(_name(c, "segment column") for c in cols))
    if len(set(found)) != len(found):
        msg = "segments repeats a segment"
        raise _fail(msg)
    return tuple(found)


def _limits(value: object, base: dict[str, Limits | None]) -> dict[str, Limits | None]:
    if not isinstance(value, dict):
        msg = "limits must be a mapping of metric to {green, red, direction} (or null to disable)"
        raise _fail(msg)
    result = dict(base)
    for metric, item in value.items():
        if metric not in METRICS:
            msg = f"unknown metric {metric!r} in limits; known: {', '.join(METRICS)}"
            raise _fail(msg)
        limits = None if item is None else Limits.from_dict(item)
        if limits is not None and limits.kind != ABSOLUTE:
            msg = f"limits of {metric!r}: kind {limits.kind!r} is not supported by stability (absolute only)"
            raise _fail(msg)
        result[metric] = limits
    return result


def _baseline(value: object) -> Baseline:
    if value is None:
        return Baseline()
    if isinstance(value, str):
        return Baseline(kind=value)
    if not isinstance(value, dict) or set(value) - {"kind", "start", "end", "lag", "periods", "min_periods"}:
        msg = f"baseline must be a kind or a mapping (kind, start, end, lag, periods, min_periods), got {value!r}"
        raise _fail(msg)
    periods = int(value.get("periods", 3))
    return Baseline(
        kind=str(value.get("kind", FIXED)),
        start=str(value.get("start", "")),
        end=str(value.get("end", "")),
        lag=int(value.get("lag", 1)),
        periods=periods,
        min_periods=int(value.get("min_periods", 1)),
    )


def _gate(value: object) -> Gate:
    if value is None:
        return Gate()
    if not isinstance(value, dict) or set(value) - {"fail_on", "on_error", "periods"}:
        msg = f"gate must be a mapping with fail_on, on_error and periods, got {value!r}"
        raise _fail(msg)
    fail_on = value.get("fail_on", [])
    items = [fail_on] if isinstance(fail_on, str) else fail_on
    if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
        msg = "gate fail_on must be a condition or a list of conditions"
        raise _fail(msg)
    return Gate(
        tuple(i for i in items if i != "never"), str(value.get("on_error", "warn")), str(value.get("periods", "latest"))
    )


def _inference(value: object) -> InferenceOptions:
    if value is None:
        return InferenceOptions()
    if not isinstance(value, dict) or set(value) - set(InferenceOptions.__dataclass_fields__):
        msg = f"inference takes {', '.join(InferenceOptions.__dataclass_fields__)}; got {value!r}"
        raise _fail(msg)
    return InferenceOptions(**value)


def _revision(value: object) -> bool:
    if value is None:
        return False
    if not isinstance(value, bool):
        msg = f"revision must be true or false, got {value!r}"
        raise _fail(msg)
    return value


def _trend_directions(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        msg = "trend_directions must map a metric to higher, lower or both"
        raise _fail(msg)
    for metric, direction in value.items():
        if metric not in METRICS:
            msg = f"trend_directions has an unknown metric {metric!r}; metrics are {', '.join(METRICS)}"
            raise _fail(msg)
        if direction not in DIRECTIONS:
            msg = f"trend_directions of {metric} must be one of {', '.join(DIRECTIONS)}, got {direction!r}"
            raise _fail(msg)
    return dict(value)


def _directional(config: StabilityConfig, metric: str) -> bool:
    """Tell whether the trend of ``metric`` has a direction: a trend direction, a limit or a column limit."""
    if config.trend_direction(metric):
        return True
    if any(metric in over and over[metric] is not None for over in config.column_limits.values()):
        return True
    return config.limits_for(metric, "") is not None


def parse_config(data: object, root: Path | str | None = None) -> StabilityConfig:
    """Validate one declaration mapping into a :class:`StabilityConfig` (unknown keys are errors)."""
    if not isinstance(data, dict):
        msg = f"a stability declaration must be a mapping, got {data!r}"
        raise _fail(msg)
    unknown = sorted(set(data) - KEYS)
    if unknown:
        msg = f"unknown keys {', '.join(unknown)}"
        raise _fail(msg)
    if "period" not in data:
        msg = "period (the column that splits the table into periods) is required"
        raise _fail(msg)
    trend_value = data.get("trend", {})
    trend_metrics = tuple(data.get("trend_metrics", DEFAULT_TREND_METRICS))
    if set(trend_metrics) - set(METRICS):
        msg = f"trend_metrics must be metrics among {', '.join(METRICS)}"
        raise _fail(msg)
    column_limits = data.get("column_limits", {})
    if not isinstance(column_limits, dict):
        msg = "column_limits must map a column to its limits"
        raise _fail(msg)
    prefix = str(data.get("prefix", "stability_"))
    if not prefix.replace("_", "a").isalnum():
        msg = f"prefix must be letters, digits and underscores, got {prefix!r}"
        raise _fail(msg)
    try:
        config = StabilityConfig(
            period=_name(data["period"], "period"),
            output=str(data.get("output", "")),
            dataset=str(data.get("dataset", "")),
            columns=_columns(data.get("columns", "auto")),
            exclude=_exclude(data.get("exclude", [])),
            segments=_segments(data.get("segments", [])),
            weight=str(data.get("weight", "") or ""),
            baseline=_baseline(data.get("baseline")),
            fit_periods=_int(data, "fit_periods", 1, 1),
            limits=_limits(data.get("limits", {}), dict(DEFAULT_LIMITS)),
            column_limits={_name(c, "column_limits key"): _limits(v, {}) for c, v in column_limits.items()},
            trend=None if trend_value in (None, False) else TrendPolicy.from_dict(trend_value or {}),
            trend_metrics=trend_metrics,
            persistence=PersistencePolicy.from_dict(data.get("persistence", {})),
            gate=_gate(data.get("gate")),
            min_rows=_int(data, "min_rows", 30, 0),
            refresh_periods=_int(data, "refresh_periods", 1, 0),
            max_periods=_int(data, "max_periods", 36, 1),
            max_segments=_int(data, "max_segments", 50, 1),
            scope=str(data.get("scope", "")),
            prefix=prefix,
            inference=_inference(data.get("inference")),
            epsilon=_epsilon(data.get("epsilon", 1e-4)),
            rules=_rules(data.get("rules"), root),
            notify=parse_notify(data.get("notify")),
            revision=_revision(data.get("revision")),
            trend_directions=_trend_directions(data.get("trend_directions")),
            noise=parse_noise(data.get("noise")),
        )
    except (TypeError, ValueError) as error:
        msg = f"invalid value: {error}"
        raise _fail(msg) from None
    blind = [m for m in config.trend_metrics if "trend_metrics" in data and not _directional(config, m)]
    if blind:
        msg = f"trend_metrics {', '.join(blind)} have no direction: declare limits or trend_directions for them"
        raise _fail(msg)
    return config


def parse_declarations(value: object, root: Path | str | None = None) -> tuple[StabilityConfig, ...]:
    """Validate the ``stability`` metadata of a task: one mapping, or a list with distinct ``output`` names."""
    items = value if isinstance(value, list) else [value]
    configs = tuple(parse_config(item, root) for item in items)
    outputs = [c.output for c in configs]
    if len(set(outputs)) != len(outputs):
        msg = "several declarations name the same output; give each an explicit, distinct output"
        raise _fail(msg)
    return configs


__all__ = [
    "BASELINES",
    "CHANGED",
    "COLUMN_METRICS",
    "DEFAULT_LIMITS",
    "ESCALATED",
    "METRICS",
    "OPENED",
    "RECURRED",
    "REMINDED",
    "REVISION_LIMITS",
    "REVISION_METRICS",
    "TABLES",
    "TABLE_METRICS",
    "TRANSITIONS",
    "Baseline",
    "Gate",
    "NoiseBudget",
    "NotifyPolicy",
    "StabilityConfig",
    "parse_config",
    "parse_declarations",
    "parse_noise",
    "parse_notify",
]
