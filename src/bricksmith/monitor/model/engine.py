"""One model monitoring check: freeze bins on the development reference, measure each monitoring period, judge.

``check`` is independent of where rows are stored: it reads earlier binnings through a :class:`Store` and returns
the rows to write per table, the run record included. Every metric family is measured in isolation, so a family
that raises marks the run ``partial`` and leaves the others intact. Metrics come from the shared core
(:func:`bricksmith.monitor.measure`), so rows and Spark DataFrames give identical results.

Each value is judged through its limit kind (absolute, relative to a reference, or control-chart distance from
the per-period values of the development and out-of-time references), checked against its trend, and every
series of lights is replayed into persistence and trend alerts with the shared core. The human actions stored for
the model (analysis, closure, risk acceptance) are folded into the alerts at the run date, and an alert with an
active risk acceptance no longer counts for the gate.
"""

from __future__ import annotations

import calendar
import contextlib
import dataclasses
import hashlib
import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from bricksmith.monitor.adapter import is_rows, measure
from bricksmith.monitor.bins import BinSpec, category_text, count_bins, fit_bins, is_null
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import COLORS, CONTROL, RED, RELATIVE, YELLOW, judge, rank
from bricksmith.monitor.measure import OK, segment_name
from bricksmith.monitor.model.actions import STATE_ACCEPTED, apply_actions
from bricksmith.monitor.model.config import (
    CALIBRATION,
    COMPLETENESS,
    DEVELOPMENT,
    FAMILY_METRICS,
    FEATURE_CSI,
    GRADE_PSI,
    LABEL_FAMILIES,
    MONTHS,
    OUT_OF_TIME,
    SCORE_PSI,
    VOLUMETRY,
)
from bricksmith.monitor.model.tables import (
    ACTIONS,
    ALERTS,
    BINNINGS,
    DETAILS,
    FAILED,
    PARTIAL,
    RESULTS,
    RUNS,
    SUCCESS,
)
from bricksmith.monitor.persistence import CRITICAL, OPEN, PersistencePolicy, alert_spans
from bricksmith.monitor.reference import Reference
from bricksmith.monitor.stability.profile import period_date, period_text
from bricksmith.monitor.trend import trend_check

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from bricksmith.monitor.measure import Measurement
    from bricksmith.monitor.model.config import Lag, ModelMonitorConfig, Sample

PASSED = "passed"
WARNED = "warned"
GATE_FAILED = "failed"
ERROR = "error"
VOLUME_METRICS = frozenset({"row_count", "volume_ratio"})
LIGHT = "light"
PERSISTENCE = "persistence"
TREND = "trend"
_REASON_LIMIT = 300


class ModelMonitorGateError(MonitorError):
    """The declared gate of a model monitoring check failed: the predictions are published, dependents blocked."""

    code = "model_monitoring_gate"


class Store(Protocol):
    """Reads stored binnings of a model, and stored stability binnings a declaration may share."""

    def rows(self, kind: str, model: str) -> list[dict[str, Any]]:
        """Return the rows of table ``kind`` whose ``model`` column equals ``model``."""
        ...

    def stability_binnings(self, dataset: str) -> list[dict[str, Any]]:
        """Return the stored stability binning rows of ``dataset`` (empty when there are none)."""
        ...


@dataclass
class MemoryStore:
    """A :class:`Store` over lists of rows (tests, the pure CLI, the in-memory table I/O)."""

    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    stability: list[dict[str, Any]] = field(default_factory=list)

    def rows(self, kind: str, model: str) -> list[dict[str, Any]]:
        """Return the stored rows of ``model``."""
        return [dict(r) for r in self.tables.get(kind, []) if r.get("model") == model]

    def stability_binnings(self, dataset: str) -> list[dict[str, Any]]:
        """Return the stored stability binning rows of ``dataset``."""
        return [dict(r) for r in self.stability if r.get("dataset") == dataset]


@dataclass(frozen=True)
class Outcome:
    """What one check concluded, summarized for the run report."""

    model: str
    status: str
    run_status: str = ""
    model_version: str = ""
    latest_period: str = ""
    periods: int = 0
    red: int = 0
    yellow: int = 0
    errors: int = 0
    message: str = ""
    output: str = ""
    open_alerts: int = 0
    critical_alerts: int = 0

    @property
    def failed(self) -> bool:
        """Tell whether the gate failed."""
        return self.status == GATE_FAILED

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "critical_alerts": self.critical_alerts,
            "errors": self.errors,
            "latest_period": self.latest_period,
            "message": self.message,
            "model": self.model,
            "model_version": self.model_version,
            "open_alerts": self.open_alerts,
            "output": self.output,
            "periods": self.periods,
            "red": self.red,
            "run_status": self.run_status,
            "status": self.status,
            "yellow": self.yellow,
        }


@dataclass
class CheckResult:
    """The rows to write per model monitoring table, the outcome and the binning used."""

    tables: dict[str, list[dict[str, Any]]]
    outcome: Outcome
    binning: Reference | None = None


@dataclass(frozen=True)
class Item:
    """One measured value of a family (or the reason there is none), with its per-bin details."""

    segment: str
    column: str
    metric: str
    value: float | None
    rows: int = 0
    reason: str = ""
    details: tuple[dict[str, Any], ...] = ()


class Frame(Protocol):
    """The operations the engine needs from rows or a DataFrame."""

    data: Any

    def periods(self, column: str) -> list[str]:
        """Return the sorted distinct period texts."""
        ...

    def select(self, *, column: str, keep: set[str] | None = None, drop: set[str] | None = None) -> Frame:
        """Keep rows whose ``column`` text is in ``keep`` and not in ``drop`` (nulls survive only a ``drop``)."""
        ...

    def count(self) -> int:
        """Return the number of rows."""
        ...

    def value_counts(self, column: str) -> dict[Any, int]:
        """Return ``value -> count`` of ``column``."""
        ...

    def null_count(self, column: str) -> int:
        """Return the number of null (or NaN) values of ``column``."""
        ...

    def numeric(self, column: str) -> bool:
        """Tell whether ``column`` holds numbers (booleans are not numbers)."""
        ...

    def segments(self, column: str) -> list[str]:
        """Return the sorted segment names of ``column`` (the ``segment_name`` spelling)."""
        ...

    def segment(self, column: str, name: str) -> Frame:
        """Keep the rows of one segment."""
        ...

    def texts(self, column: str) -> list[str]:
        """Return the sorted distinct non-null texts of ``column``."""
        ...


class _RowsFrame:
    """The pure engine over a list of mappings."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]) -> None:
        self.data = list(rows)

    def periods(self, column: str) -> list[str]:
        return sorted({p for p in (period_text(r.get(column)) for r in self.data) if p is not None})

    def select(self, *, column: str, keep: set[str] | None = None, drop: set[str] | None = None) -> _RowsFrame:
        def wanted(row: Mapping[str, Any]) -> bool:
            text = period_text(row.get(column))
            if keep is not None and text not in keep:
                return False
            return drop is None or text not in drop

        return _RowsFrame(r for r in self.data if wanted(r))

    def count(self) -> int:
        return len(self.data)

    def value_counts(self, column: str) -> dict[Any, int]:
        return dict(Counter(r.get(column) for r in self.data))

    def null_count(self, column: str) -> int:
        return sum(1 for r in self.data if is_null(r.get(column)))

    def numeric(self, column: str) -> bool:
        values = [r.get(column) for r in self.data if not is_null(r.get(column))]
        return bool(values) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)

    def segments(self, column: str) -> list[str]:
        return sorted({segment_name(r.get(column)) for r in self.data})

    def segment(self, column: str, name: str) -> _RowsFrame:
        return _RowsFrame(r for r in self.data if segment_name(r.get(column)) == name)

    def texts(self, column: str) -> list[str]:
        return sorted({category_text(r[column]) for r in self.data if not is_null(r.get(column))})


class _SparkFrame:
    """The Spark engine: every reduction runs on the cluster and returns small rows."""

    def __init__(self, df: Any) -> None:  # noqa: ANN401 - a pyspark DataFrame
        self.data = df

    @staticmethod
    def _f() -> Any:  # noqa: ANN401 - the pyspark functions module
        from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

        return f

    def periods(self, column: str) -> list[str]:
        from bricksmith.monitor.stability.spark import periods_spark  # noqa: PLC0415 - heavy integration

        return periods_spark(self.data, column)

    def select(self, *, column: str, keep: set[str] | None = None, drop: set[str] | None = None) -> _SparkFrame:
        from bricksmith.monitor.stability.spark import period_column  # noqa: PLC0415

        text = period_column(self.data, column)
        df = self.data
        if keep is not None:
            df = df.where(text.isin(sorted(keep)))
        if drop:
            df = df.where(text.isNull() | ~text.isin(sorted(drop)))
        return _SparkFrame(df)

    def count(self) -> int:
        return int(self.data.count())

    def value_counts(self, column: str) -> dict[Any, int]:
        return {r[column]: int(r["count"]) for r in self.data.groupBy(column).count().collect()}

    def null_count(self, column: str) -> int:
        f = self._f()
        missing = f.col(column).isNull()
        if self.numeric(column):
            missing = missing | f.isnan(f.col(column))
        return int(self.data.where(missing).count())

    def numeric(self, column: str) -> bool:
        kind = dict(self.data.dtypes).get(column, "")
        return kind in ("tinyint", "smallint", "int", "bigint", "float", "double") or kind.startswith("decimal")

    def _segment_text(self, column: str) -> Any:  # noqa: ANN401 - a pyspark Column
        from bricksmith.monitor.measure import NULL_SEGMENT  # noqa: PLC0415

        f = self._f()
        return f.coalesce(f.col(column).cast("string"), f.lit(NULL_SEGMENT))

    def segments(self, column: str) -> list[str]:
        rows = self.data.select(self._segment_text(column).alias("g")).distinct().collect()
        return sorted(str(r["g"]) for r in rows)

    def segment(self, column: str, name: str) -> _SparkFrame:
        return _SparkFrame(self.data.where(self._segment_text(column) == name))

    def texts(self, column: str) -> list[str]:
        from bricksmith.monitor.stability.spark import period_column  # noqa: PLC0415

        rows = self.data.select(period_column(self.data, column).alias("t")).where("t IS NOT NULL").distinct()
        return sorted(str(r["t"]) for r in rows.collect())


def frame_of(data: Any) -> Frame:  # noqa: ANN401 - rows or DataFrame
    """Wrap rows or a Spark DataFrame in the engine adapter."""
    return _RowsFrame(data) if is_rows(data) else _SparkFrame(data)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def code_version() -> str:
    """Return the installed bricksmith version (``""`` when it is not installed as a distribution)."""
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415 - only for the run record

    try:
        return version("bricksmith")
    except PackageNotFoundError:
        return ""


# --- references and periods -------------------------------------------------------------------------------------


def reference_frame(frame: Frame, config: ModelMonitorConfig, sample: Sample) -> Frame:
    """Return the rows of a reference: its sample value, or its inclusive period range."""
    if sample.value:
        return frame.select(column=config.sample, keep={sample.value})
    inside = {p for p in frame.periods(config.period) if sample.start <= p <= sample.end}
    return frame.select(column=config.period, keep=inside)


def monitoring_frame(frame: Frame, config: ModelMonitorConfig) -> Frame:
    """Return the rows that are not reference rows (neither a reference sample nor a reference period range)."""
    values = {s.value for s in config.references if s.value}
    scoped = frame.select(column=config.sample, drop=values) if values else frame
    ranges = [s for s in config.references if not s.value]
    if not ranges:
        return scoped
    inside = {p for p in scoped.periods(config.period) for s in ranges if s.start <= p <= s.end}
    return scoped.select(column=config.period, drop=inside) if inside else scoped


def data_period(periods: Sequence[str], monitoring: str, lag: int) -> str | None:
    """Return the period ``lag`` observed periods before ``monitoring`` (``None`` when the history is too short)."""
    index = periods.index(monitoring) - lag
    return periods[index] if index >= 0 else None


_PERIOD_FORMATS = (
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "{:04d}-{:02d}-{:02d}"),
    (re.compile(r"(\d{4})-(\d{2})"), "{:04d}-{:02d}"),
    (re.compile(r"(\d{4})(\d{2})(\d{2})"), "{:04d}{:02d}{:02d}"),
    (re.compile(r"(\d{4})(\d{2})"), "{:04d}{:02d}"),
)


def shift_months(text: str, months: int) -> str | None:
    """Return the period text ``months`` calendar months before ``text``, in the same format.

    Understands ``YYYY-MM-DD``, ``YYYY-MM``, ``YYYYMMDD`` and ``YYYYMM``; the day clamps to the month's last day,
    and the last day of a month maps to the last day of the target month.
    Any other text (or an impossible date) is ``None``.
    """
    for pattern, template in _PERIOD_FORMATS:
        found = pattern.fullmatch(text)
        if not found:
            continue
        year, month, *rest = (int(g) for g in found.groups())
        if not 1 <= month <= 12:  # noqa: PLR2004 - months of a year
            return None
        index = year * 12 + month - 1 - months
        target_year, target_month = divmod(index, 12)
        if not rest:
            return template.format(target_year, target_month + 1)
        try:
            date(year, month, rest[0])
        except ValueError:
            return None
        last = calendar.monthrange(target_year, target_month + 1)[1]
        closing = rest[0] == calendar.monthrange(year, month)[1]
        return template.format(target_year, target_month + 1, last if closing else min(rest[0], last))
    return None


def resolve_data_period(periods: Sequence[str], monitoring: str, lag: Lag) -> str | None:
    """Return the data period of ``monitoring`` under ``lag``, or ``None`` when it is not available.

    Observed lags count the periods present in ``periods``; calendar lags move whole months and need that period to
    be present, so a missing month is never replaced by a neighbour.
    """
    if lag.unit != MONTHS:
        return data_period(periods, monitoring, lag.amount)
    target = shift_months(monitoring, lag.amount)
    return target if target is not None and target in set(periods) else None


# --- binning --------------------------------------------------------------------------------------------------------


def _shared_binning(config: ModelMonitorConfig, store: Store) -> Reference | None:
    if not config.stability_dataset:
        return None
    rows = store.stability_binnings(config.stability_dataset)
    if not rows:
        msg = f"model_monitoring: no stored stability binning for dataset {config.stability_dataset!r}"
        raise MonitorError(msg)
    return Reference.from_json(str(max(rows, key=lambda r: int(r["version"]))["body"]))


def _feature_bins(column: str, development: Frame, settings: Mapping[str, Any], shared: Reference | None) -> BinSpec:
    counts = development.value_counts(column)
    if shared is not None and column in {c.column for c in shared.columns}:
        spec = shared.column(column)
        return dataclasses.replace(spec, counts=count_bins(spec, counts))
    if not settings:
        settings = {"type": "numeric"} if development.numeric(column) else {"type": "categorical"}
    return fit_bins(column, counts, settings)


def _fit(
    development: Frame, config: ModelMonitorConfig, shared: Reference | None, *, version: int, source: str
) -> Reference:
    rows = development.count()
    if rows == 0:
        msg = f"model_monitoring: the development reference ({config.development.label}) has no rows"
        raise MonitorError(msg)
    specs = [fit_bins(config.score, development.value_counts(config.score), {"bins": config.score_bins})]
    if config.grade:
        specs.append(fit_bins(config.grade, development.value_counts(config.grade), {"type": "categorical"}))
    for column, settings in sorted(config.features.items()):
        if column in (config.score, config.grade):
            continue
        with contextlib.suppress(MonitorError):  # an unfittable feature reports an error result per period
            specs.append(_feature_bins(column, development, settings, shared))
    return Reference(
        name=config.model,
        version=version,
        columns=tuple(specs),
        fit_source=source,
        fit_rows=rows,
        fit_window={"reference": DEVELOPMENT, "rows": config.development.label},
        score=config.score,
        label=config.label,
        segment=config.segment,
        time=config.period,
        metric_config={"calibration_bins": config.calibration_bins, "epsilon": config.epsilon, "stability_period": ""},
    )


def active_binning(  # noqa: PLR0913 - one keyword per binning input
    development: Frame, config: ModelMonitorConfig, store: Store, *, source: str, run_id: str, now: str
) -> tuple[Reference, list[dict[str, Any]]]:
    """Return the frozen binning of the declaration, fitting a new version when none matches its fingerprint."""
    shared = _shared_binning(config, store)
    shared_from = f"{config.stability_dataset} v{shared.version}" if shared else ""
    wanted = hashlib.sha256(
        f"{config.binning_fingerprint()}|{shared.fingerprint if shared else ''}".encode()
    ).hexdigest()
    stored = store.rows(BINNINGS, config.model)
    matching = [r for r in stored if r.get("config_fingerprint") == wanted]
    if matching:
        return Reference.from_json(str(max(matching, key=lambda r: int(r["version"]))["body"])), []
    version = max((int(r["version"]) for r in stored), default=0) + 1
    binning = _fit(development, config, shared, version=version, source=source)
    row = {
        "model": config.model, "version": version, "config_fingerprint": wanted, "fingerprint": binning.fingerprint,
        "source": source, "fit_rows": binning.fit_rows, "columns": len(binning.columns), "shared_from": shared_from,
        "body": binning.to_json(), "created_run_id": run_id, "created_at": now,
    }  # fmt: skip
    return binning, [row]


# --- families --------------------------------------------------------------------------------------------------------


def _part(binning: Reference, columns: Sequence[str], *, label: bool) -> Reference:
    """Return a reference holding only ``columns`` of the binning (the score role only when it is among them)."""
    score = binning.score if binning.score in columns else ""
    return dataclasses.replace(
        binning,
        columns=tuple(binning.column(c) for c in columns),
        score=score,
        label=binning.label if label and score else "",
    )


def _psi_details(spec: BinSpec, m: Measurement) -> tuple[dict[str, Any], ...]:
    order = {b: i for i, b in enumerate(spec.bin_ids())}
    bins = m.detail.get("bins", {})
    return tuple(
        {"bin": b, "bin_order": order.get(b, len(order)), "reference_share": t["reference"],
         "current_share": t["current"], "contribution": t["contribution"]}
        for b, t in sorted(bins.items(), key=lambda kv: order.get(kv[0], len(order)))
    )  # fmt: skip


def _item(m: Measurement, metric: str, details: tuple[dict[str, Any], ...] = ()) -> Item:
    value = m.value if m.status == OK else None
    return Item(m.segment, m.subject, metric, value, m.count, "" if m.status == OK else m.reason, details)


def _distribution(binning: Reference, column: str, metric: str, frame: Frame, *, segmented: bool) -> list[Item]:
    measurements = measure(_part(binning, [column], label=False), frame.data, segments=segmented)
    spec = binning.column(column)
    wanted = "psi" if column == binning.score else "csi"
    return [_item(m, metric, _psi_details(spec, m)) for m in measurements if m.metric == wanted]


def _ratio(m: Measurement) -> Item:
    bins = m.detail.get("bins", {})
    observed = sum(b["count"] * b["observed_rate"] for b in bins.values())
    expected = sum(b["count"] * b["mean_score"] for b in bins.values())
    if m.status != OK or expected <= 0:
        return Item(m.segment, m.subject, "calibration_ratio", None, m.count, m.reason or "no_expected_events")
    return Item(m.segment, m.subject, "calibration_ratio", observed / expected, m.count)


def _labelled(binning: Reference, family: str, frame: Frame, *, segmented: bool) -> list[Item]:
    measurements = measure(_part(binning, [binning.score], label=True), frame.data, segments=segmented)
    names = FAMILY_METRICS[family]
    items = [_item(m, m.metric) for m in measurements if m.metric in names]
    if family == CALIBRATION:
        for m in (m for m in measurements if m.metric == "ece"):
            details = tuple(
                {"bin": str(i), "bin_order": int(i), "count": b["count"], "mean_score": b["mean_score"],
                 "observed_rate": b["observed_rate"]}
                for i, b in sorted(m.detail.get("bins", {}).items(), key=lambda kv: int(kv[0]))
            )  # fmt: skip
            items = [dataclasses.replace(i, details=details) if i.metric == "ece" and i.segment == m.segment else i
                     for i in items]  # fmt: skip
            items.append(_ratio(m))
    return items


def _by_segment(frame: Frame, segment: str) -> list[tuple[str, Frame]]:
    return [("", frame)] + ([(s, frame.segment(segment, s)) for s in frame.segments(segment)] if segment else [])


def _volumetry(frame: Frame, periods: int, baseline: Mapping[str, float], segment: str) -> list[Item]:
    items: list[Item] = []
    for name, part in _by_segment(frame, segment):
        rows = part.count()
        per_period = rows / max(periods, 1)
        base = baseline.get(name)
        ratio = per_period / base if base else None
        items.append(Item(name, "", "row_count", per_period, rows))
        items.append(Item(name, "", "volume_ratio", ratio, rows, "" if base else "empty_reference"))
    return items


def _completeness(frame: Frame, fields: Sequence[str], segment: str) -> list[Item]:
    items: list[Item] = []
    for name, part in _by_segment(frame, segment):
        rows = part.count()
        for column in fields:
            value = part.null_count(column) / rows if rows else None
            items.append(Item(name, column, "null_rate", value, rows, "" if rows else "empty_window"))
    return items


_METRIC = {SCORE_PSI: "psi", GRADE_PSI: "grade_psi", FEATURE_CSI: "csi"}


def jobs_of(config: ModelMonitorConfig) -> list[tuple[str, str]]:
    """Return the isolated measurements of a declaration as ``(family, column)`` pairs."""
    found: list[tuple[str, str]] = []
    for family in config.families():
        if family == FEATURE_CSI:
            found += [(family, c) for c in sorted(config.features) if c not in (config.score, config.grade)]
        else:
            column = {GRADE_PSI: config.grade, VOLUMETRY: "", COMPLETENESS: ""}.get(family, config.score)
            found.append((family, column))
    return found


@dataclass(frozen=True)
class _Measurer:
    """Measures one family on one frame, isolating its failure."""

    config: ModelMonitorConfig
    binning: Reference
    baseline: Mapping[str, float]

    def items(self, family: str, column: str, frame: Frame, periods: int) -> list[Item]:
        segmented = bool(self.config.segment)
        if family in _METRIC:
            return _distribution(self.binning, column, _METRIC[family], frame, segmented=segmented)
        if family in LABEL_FAMILIES:
            return _labelled(self.binning, family, frame, segmented=segmented)
        if family == VOLUMETRY:
            return _volumetry(frame, periods, self.baseline, self.config.segment)
        return _completeness(frame, self.config.critical_fields, self.config.segment)

    def run(self, family: str, column: str, frame: Frame, periods: int) -> tuple[list[Item], int]:
        try:
            return self.items(family, column, frame, periods), 0
        except Exception as error:  # noqa: BLE001 - one family failing must not stop the others
            reason = f"error: {type(error).__name__}: {error}"[:_REASON_LIMIT]
            columns = list(self.config.critical_fields) if family == COMPLETENESS else [column]
            return [Item("", c, m, None, 0, reason) for m in FAMILY_METRICS[family] for c in columns], 1


# --- judging and rows ------------------------------------------------------------------------------------------------


def _per_period(frame: Frame, config: ModelMonitorConfig) -> dict[str, float]:
    """Return the average rows per period of each segment (``""`` for all) of a reference frame."""
    periods = max(len(frame.periods(config.period)), 1)
    return {name: part.count() / periods for name, part in _by_segment(frame, config.segment)}


def _reference_values(
    measurer: _Measurer, jobs: Sequence[tuple[str, str]], frame: Frame | None
) -> tuple[dict[tuple[str, str, str], float | None], int]:
    if frame is None:
        return {}, 0
    periods = max(len(frame.periods(measurer.config.period)), 1)
    values: dict[tuple[str, str, str], float | None] = {}
    errors = 0
    for family, column in jobs:
        items, failed = measurer.run(family, column, frame, periods)
        errors += failed
        values.update({(i.segment, i.column, i.metric): i.value for i in items})
    return values, errors


Key = tuple[str, str, str]
Baseline = tuple[float | None, float | None, int]


def control_baselines(
    measurer: _Measurer, jobs: Sequence[tuple[str, str]], frames: Sequence[Frame | None]
) -> tuple[dict[Key, Baseline], int]:
    """Return ``(mean, sd, points)`` per control-judged result key from per-period values of the reference frames.

    Every observed period of every reference (development, then out-of-time) is one point. Fewer than
    ``control_min_points`` finite points leave the mean and standard deviation unset (the light is then
    ``no_baseline``).
    """
    config = measurer.config
    wanted = config.control_metrics()
    if not wanted:
        return {}, 0
    points: dict[Key, list[float]] = {}
    errors = 0
    chosen = [(f, c) for f, c in jobs if set(FAMILY_METRICS[f]) & wanted]
    for frame in (f for f in frames if f is not None):
        for period in frame.periods(config.period):
            current = frame.select(column=config.period, keep={period})
            for family, column in chosen:
                items, failed = measurer.run(family, column, current, 1)
                errors += failed
                for item in items:
                    if item.metric in wanted and item.value is not None and math.isfinite(item.value):
                        points.setdefault((item.segment, item.column, item.metric), []).append(item.value)
    baselines: dict[Key, Baseline] = {}
    for key, values in points.items():
        if len(values) < config.control_min_points:
            baselines[key] = (None, None, len(values))
        else:
            baselines[key] = (statistics.fmean(values), statistics.stdev(values), len(values))
    return baselines, errors


def _version(frame: Frame, config: ModelMonitorConfig) -> str:
    if config.model_version_column:
        return ",".join(frame.texts(config.model_version_column))
    return config.model_version


@dataclass(frozen=True)
class _Context:
    config: ModelMonitorConfig
    binning: Reference
    run_id: str
    now: str
    development: Mapping[tuple[str, str, str], float | None]
    out_of_time: Mapping[tuple[str, str, str], float | None]
    control: Mapping[tuple[str, str, str], Baseline] = field(default_factory=dict)


def _result(ctx: _Context, item: Item, family: str, version: str, when: tuple[str, str, Lag]) -> dict[str, Any]:
    monitoring, data, lag = when
    config = ctx.config
    limits = config.limits_for(item.metric)
    minimum = 0 if item.metric in VOLUME_METRICS else config.min_rows
    key = (item.segment, item.column, item.metric)
    control = limits is not None and limits.kind == CONTROL
    mean, sd, points = ctx.control.get(key, (None, None, 0)) if control else (None, None, None)
    baseline = mean
    if limits is not None and limits.kind == RELATIVE:
        baseline = (ctx.out_of_time if limits.reference == OUT_OF_TIME else ctx.development).get(key)
    light, compared = judge(
        item.value, limits, baseline=baseline, spread=sd, count=item.rows, min_count=minimum, reason=item.reason
    )
    return {
        "model": config.model, "model_version": version, "run_id": ctx.run_id, "monitoring_period": monitoring,
        "data_period": data, "lag": lag.amount, "lag_unit": lag.unit, "period_date": period_date(monitoring),
        "segment": item.segment,
        "column": item.column, "metric": item.metric, "family": family, "value": item.value,
        "light": light.status, "light_rank": rank(light.status), "reason": item.reason or light.reason,
        "green_limit": limits.green if limits else None, "red_limit": limits.red if limits else None,
        "direction": limits.direction if limits else "", "rows": item.rows, "reference": DEVELOPMENT,
        "development_value": ctx.development.get(key), "out_of_time_value": ctx.out_of_time.get(key),
        "binning_version": ctx.binning.version, "computed_at": ctx.now,
        "limit_kind": limits.kind if limits else "", "limit_reference": limits.reference if limits else "",
        "compared_value": _finite(compared), "baseline_mean": mean, "baseline_sd": sd, "baseline_points": points,
        "trend_status": "", "trend_expected": None, "trend_deviation": None,
    }  # fmt: skip


def _finite(value: float | None) -> float | None:
    return value if value is None or math.isfinite(value) else None


def apply_trend(results: Sequence[dict[str, Any]], config: ModelMonitorConfig) -> None:
    """Fill the trend columns of colored results of ``trend_metrics``, series by series in period order.

    A series is one model version, segment, column and metric; the trend judges the compared value (the value
    itself for absolute limits) in the direction its limits call worse.
    """
    policy = config.trend
    if policy is None:
        return
    history: dict[tuple[str, ...], list[float]] = {}
    for row in sorted(results, key=lambda r: str(r["monitoring_period"])):
        current = row.get("compared_value")
        if row["metric"] not in config.trend_metrics or not row.get("direction") or row["light"] not in COLORS:
            continue
        if current is None or not math.isfinite(current):
            continue
        key = _series(row)
        found = trend_check(history.setdefault(key, []), current, policy, str(row["direction"]))
        history[key].append(current)
        row.update(
            trend_status=found.status, trend_expected=_finite(found.expected), trend_deviation=_finite(found.deviation)
        )


def _detail_rows(result: Mapping[str, Any], item: Item) -> list[dict[str, Any]]:
    keys = ("model", "model_version", "run_id", "monitoring_period", "data_period", "period_date", "segment",
            "column", "metric")  # fmt: skip
    return [{**{k: result[k] for k in keys}, **d} for d in item.details]


def _series(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(k) or "") for k in ("model", "model_version", "segment", "column", "metric"))


def _span_alerts(
    results: Sequence[Mapping[str, Any]], persistence: PersistencePolicy, created_at: str
) -> list[dict[str, Any]]:
    """Replay each series' lights (persistence) and trend statuses (trend) into alert spans."""
    trend_policy = PersistencePolicy(
        yellow=0, red=1, close_after=persistence.close_after, immediate_red=True, recurrence=persistence.recurrence
    )
    series: dict[tuple[str, ...], list[Mapping[str, Any]]] = {}
    for row in results:
        if row.get("direction"):
            series.setdefault(_series(row), []).append(row)
    out: list[dict[str, Any]] = []
    for key in sorted(series):
        rows = {str(r["monitoring_period"]): r for r in series[key]}
        for kind, field_name, policy in ((PERSISTENCE, "light", persistence), (TREND, "trend_status", trend_policy)):
            pairs = [(p, str(r.get(field_name) or "skipped")) for p, r in rows.items()]
            for span in alert_spans(pairs, policy):
                last = rows[span.last_period]
                model, version, segment, column, metric = key
                identity = "|".join((*key, kind, span.opened))
                verb = "departs from its trend" if kind == TREND else "is persistently off"
                where = f" [{segment}]" if segment else ""
                subject = f" {column}" if column else ""
                value = last.get("value")
                out.append({
                    "model": model, "model_version": version, "run_id": last["run_id"],
                    "alert_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
                    "monitoring_period": span.last_period, "data_period": last.get("data_period"),
                    "segment": segment, "column": column, "metric": metric, "family": last.get("family") or "",
                    "light": last.get("light"), "severity": span.severity,
                    "severity_rank": 2 if span.severity == CRITICAL else 1, "value": value, "created_at": created_at,
                    "message": f"{model} {metric}{subject}{where} {verb}: {span.severity} since {span.opened}, "
                               f"last {span.last_status} in {span.last_period}"
                               + (f" (value {value:.4g})" if value is not None else ""),
                    "kind": kind, "state": span.state, "opened_period": span.opened,
                    "escalated_period": span.escalated, "closed_period": span.closed,
                    "last_period": span.last_period, "flagged_periods": len(span.flagged),
                    "recurrence": span.recurrence,
                })  # fmt: skip
    return out


def alert_rows(
    results: Iterable[Mapping[str, Any]], created_at: str, persistence: PersistencePolicy | None = None
) -> list[dict[str, Any]]:
    """Return the alerts of ``results``: one ``light`` alert per red or yellow result, then the alert spans.

    A light alert is ``critical`` for red and ``warning`` for yellow. Persistence spans replay each series' lights
    with ``persistence`` (the default policy when ``None``); trend spans replay its trend statuses, one red trend
    opening a critical alert.
    """
    rows = list(results)
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.get("light") not in (RED, YELLOW):
            continue
        identity = "|".join(str(r.get(k) or "") for k in ("model", "model_version", "metric", "column", "segment",
                                                           "monitoring_period"))  # fmt: skip
        red = r["light"] == RED
        where = f" [{r['segment']}]" if r.get("segment") else ""
        subject = f" {r['column']}" if r.get("column") else ""
        out.append({
            "model": r["model"], "model_version": r.get("model_version") or "", "run_id": r["run_id"],
            "alert_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
            "monitoring_period": r["monitoring_period"], "data_period": r.get("data_period"),
            "segment": r.get("segment") or "", "column": r.get("column") or "", "metric": r["metric"],
            "family": r.get("family") or "", "light": r["light"], "severity": "critical" if red else "warning",
            "severity_rank": 2 if red else 1, "value": r.get("value"), "created_at": created_at,
            "message": f"{r['model']} {r['metric']}{subject}{where} {r['monitoring_period']}: "
                       f"{r.get('value'):.4g} is {r['light']}",
            "kind": LIGHT, "state": OPEN, "opened_period": r["monitoring_period"], "escalated_period": "",
            "closed_period": "", "last_period": r["monitoring_period"], "flagged_periods": 1, "recurrence": False,
        })  # fmt: skip
    return out + _span_alerts(rows, persistence or PersistencePolicy(), created_at)


def _light_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Return what ties a light alert to its result: version, segment, column, metric and monitoring period."""
    return (*_series(row), str(row.get("monitoring_period") or ""))


def open_counts(alerts: Iterable[Mapping[str, Any]]) -> tuple[int, int]:
    """Return ``(open, critical)`` counts of the open persistence and trend alerts."""
    spans = [a for a in alerts if a.get("kind") in (PERSISTENCE, TREND) and a.get("state") == OPEN]
    return len(spans), sum(1 for a in spans if a.get("severity") == CRITICAL)


def _gate(config: ModelMonitorConfig, red: int, yellow: int, open_alerts: int, critical: int) -> list[str]:
    reasons = []
    if "red" in config.gate.fail_on and red:
        reasons.append(f"{red} red result(s)")
    if "yellow" in config.gate.fail_on and red + yellow:
        reasons.append(f"{red + yellow} yellow or red result(s)")
    if "critical" in config.gate.fail_on and critical:
        reasons.append(f"{critical} open critical alert(s)")
    if "alert" in config.gate.fail_on and open_alerts:
        reasons.append(f"{open_alerts} open alert(s)")
    return reasons


@dataclass
class _Window:
    """What measuring the monitoring periods produced."""

    periods: list[str] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)
    versions: set[str] = field(default_factory=set)
    errors: int = 0


def _measure_family(  # noqa: PLR0913, PLR0917 - one argument per thing the family needs
    ctx: _Context,
    measurer: _Measurer,
    job: tuple[str, str],
    current: tuple[str, str, Frame],
    history: tuple[Sequence[str], Frame],
    out: _Window,
) -> None:
    """Measure one family of one monitoring period; metrics sharing a data period are measured in one pass."""
    family, column = job
    period, version, frame = current
    observed, monitored = history
    by_data: dict[str | None, list[tuple[str, Lag]]] = {}
    for metric in FAMILY_METRICS[family]:
        lag = ctx.config.lag_for(family, metric)
        by_data.setdefault(resolve_data_period(observed, period, lag), []).append((metric, lag))
    for lagged, metrics in by_data.items():
        names = {metric for metric, _ in metrics}
        if lagged is None:
            items = [Item("", column, m, None, 0, "data_period_unavailable") for m in sorted(names)]
            failed = 0
        else:
            rows = frame if lagged == period else monitored.select(column=ctx.config.period, keep={lagged})
            items, failed = measurer.run(family, column, rows, 1)
            items = [i for i in items if i.metric in names]
        out.errors += failed
        lags = dict(metrics)
        for item in items:
            row = _result(ctx, item, family, version, (period, lagged or "", lags[item.metric]))
            out.results.append(row)
            out.details += _detail_rows(row, item)


def _measure_window(ctx: _Context, measurer: _Measurer, jobs: Sequence[tuple[str, str]], monitored: Frame) -> _Window:
    """Measure every job in each monitoring period; label families read the lagged data period."""
    config = ctx.config
    observed = monitored.periods(config.period)
    if not observed:
        msg = f"model_monitoring: {config.model} has no monitoring periods outside its references"
        raise MonitorError(msg)
    out = _Window(observed[-config.max_periods :])
    for period in out.periods:
        current = monitored.select(column=config.period, keep={period})
        version = _version(current, config)
        out.versions.add(version)
        for family, column in jobs:
            _measure_family(ctx, measurer, (family, column), (period, version, current), (observed, monitored), out)
    return out


def check(  # noqa: PLR0913 - one keyword per documented input
    data: Any,  # noqa: ANN401 - rows or DataFrame
    config: ModelMonitorConfig,
    store: Store,
    *,
    run_id: str,
    task_id: str = "",
    physical: str = "",
    input_version: str = "",
    now: str | None = None,
) -> CheckResult:
    """Measure ``data`` (rows or a DataFrame of predictions) against its references and return the rows to write."""
    started = now or _now()
    frame = frame_of(data)
    development = reference_frame(frame, config, config.development)
    binning, binning_rows = active_binning(
        development, config, store, source=physical or config.model, run_id=run_id, now=started
    )
    measurer = _Measurer(config, binning, _per_period(development, config))
    jobs = jobs_of(config)
    oot = reference_frame(frame, config, config.out_of_time) if config.out_of_time else None
    dev_values, errors = _reference_values(measurer, jobs, development)
    oot_values, oot_errors = _reference_values(measurer, jobs, oot)
    control, control_errors = control_baselines(measurer, jobs, (development, oot))
    errors += oot_errors + control_errors
    ctx = _Context(config, binning, run_id, started, dev_values, oot_values, control)
    measured = _measure_window(ctx, measurer, jobs, monitoring_frame(frame, config))
    window, results, errors = measured.periods, measured.results, errors + measured.errors
    apply_trend(results, config)
    latest = window[-1]
    in_latest = [r for r in results if r["monitoring_period"] == latest]
    red = sum(1 for r in in_latest if r["light"] == RED)
    yellow = sum(1 for r in in_latest if r["light"] == YELLOW)
    alerts = apply_actions(
        alert_rows(results, started, config.persistence), store.rows(ACTIONS, config.model), started[:10]
    )
    accepted = {_light_key(a) for a in alerts if a["kind"] == LIGHT and a["state"] == STATE_ACCEPTED}
    red -= sum(1 for r in in_latest if r["light"] == RED and _light_key(r) in accepted)
    yellow -= sum(1 for r in in_latest if r["light"] == YELLOW and _light_key(r) in accepted)
    open_alerts, critical = open_counts(alerts)
    reasons = _gate(config, red, yellow, open_alerts, critical)
    status = GATE_FAILED if reasons else WARNED if red + yellow + open_alerts else PASSED
    run_status = PARTIAL if errors else SUCCESS
    suppressed = sum(1 for a in alerts if a["state"] == STATE_ACCEPTED)
    message = (
        f"{config.model} {latest}: {red} red, {yellow} yellow, {open_alerts} open alert(s), {errors} error(s)"
        + (f"; {suppressed} accepted" if suppressed else "")
        + (f"; gate failed on {'; '.join(reasons)}" if reasons else "")
    )
    version_text = ",".join(sorted(v for v in measured.versions if v))
    outcome = Outcome(
        config.model, status, run_status, version_text, latest, len(window), red, yellow, errors, message,
        config.output, open_alerts, critical,
    )  # fmt: skip
    run_row = run_record(
        config, run_id=run_id, task_id=task_id, physical=physical, input_version=input_version, binning=binning,
        outcome=outcome, started=started, alerts=len(alerts),
    )  # fmt: skip
    tables = {
        BINNINGS: binning_rows, RESULTS: results, DETAILS: measured.details, ALERTS: alerts, ACTIONS: [],
        RUNS: [run_row],
    }  # fmt: skip
    return CheckResult(tables, outcome, binning)


def run_record(  # noqa: PLR0913 - one keyword per run record field
    config: ModelMonitorConfig,
    *,
    run_id: str,
    task_id: str,
    physical: str,
    input_version: str,
    binning: Reference | None,
    outcome: Outcome,
    started: str,
    alerts: int = 0,
) -> dict[str, Any]:
    """Return the run record row (written last): versions, references, status and counts."""
    references = "; ".join(f"{s.name}: {s.label}" for s in config.references)
    return {
        "run_id": run_id, "model": config.model, "model_version": outcome.model_version, "task_id": task_id,
        "physical": physical, "input_version": input_version, "code_version": code_version(),
        "binning_version": binning.version if binning else None,
        "binning_fingerprint": binning.fingerprint if binning else "", "references": references,
        "latest_period": outcome.latest_period, "periods": outcome.periods,
        "status": outcome.run_status or FAILED, "gate": ",".join(config.gate.fail_on), "red": outcome.red,
        "yellow": outcome.yellow, "errors": outcome.errors, "alerts": alerts, "message": outcome.message,
        "started_at": started, "finished_at": _now(), "open_alerts": outcome.open_alerts,
        "critical_alerts": outcome.critical_alerts,
    }  # fmt: skip


def error_outcome(model: str, error: BaseException, output: str = "") -> Outcome:
    """Return the outcome recorded when the check itself failed."""
    return Outcome(model, ERROR, FAILED, message=f"{type(error).__name__}: {error}", output=output)


__all__ = [
    "ERROR",
    "GATE_FAILED",
    "LIGHT",
    "OUT_OF_TIME",
    "PASSED",
    "PERSISTENCE",
    "TREND",
    "WARNED",
    "CheckResult",
    "Item",
    "MemoryStore",
    "ModelMonitorGateError",
    "Outcome",
    "Store",
    "active_binning",
    "alert_rows",
    "apply_trend",
    "check",
    "control_baselines",
    "data_period",
    "error_outcome",
    "frame_of",
    "jobs_of",
    "monitoring_frame",
    "open_counts",
    "reference_frame",
    "resolve_data_period",
    "run_record",
    "shift_months",
]
