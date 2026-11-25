"""One stability check of a table: pick or fit the binning, profile new periods, judge, replay alerts, gate.

``check`` is independent of where rows are stored: it reads earlier rows through a :class:`Store` and returns
the rows to write per table. The task hook and the ``bricksmith stability check`` command both call it.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, Protocol

from bricksmith.monitor.actions import STATE_ACCEPTED, STATE_CLOSED, apply_actions, latest_actions
from bricksmith.monitor.adapter import is_rows
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import COLORS, RED, YELLOW
from bricksmith.monitor.persistence import CLOSED, CRITICAL, OPEN, SUPERSEDED
from bricksmith.monitor.reference import Reference
from bricksmith.monitor.stability.cadence import (
    build_calendar,
    cadence_results,
    gap_between,
    has_cadence,
    parse_as_of,
    settling_periods,
)
from bricksmith.monitor.stability.config import (
    CADENCE_METRICS,
    CHANGED,
    ESCALATED,
    FIXED,
    OPENED,
    PERIOD_LATE,
    PREVIOUS,
    PSI,
    RECURRED,
    REMINDED,
    ROLLING,
    VOLUME_CHANGE,
)
from bricksmith.monitor.stability.incident import (
    Incident,
    Upstream,
    apply_noise,
    build_incident,
    digest_event,
    false_positive_rates,
    upstream_origin,
)
from bricksmith.monitor.stability.metrics import alerts, compute, counts_in
from bricksmith.monitor.stability.profile import fit_periods_of, fit_rows_binning, periods_of_rows, profile_rows
from bricksmith.monitor.stability.revision import revision_results, snapshot_rows
from bricksmith.monitor.stability.tables import (
    ACTIONS,
    ALERT_EVENTS,
    ALERTS,
    BINNINGS,
    PROFILES,
    RESULTS,
    RESULTS_HISTORY,
    REVISIONS,
    RUNS,
    SCHEMAS,
)
from bricksmith.monitor.trend import keeps

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from bricksmith.monitor.stability.config import NotifyPolicy, StabilityConfig
    from bricksmith.ops.notify import AlertEvent

PASSED = "passed"
WARNED = "warned"
FAILED = "failed"
ERROR = "error"


class StabilityGateError(MonitorError):
    """The declared gate of a stability check failed: the table is published, its dependents are blocked."""

    code = "stability_gate"


class Store(Protocol):
    """Reads the rows already stored for a dataset in one stability table."""

    def rows(
        self,
        kind: str,
        dataset: str,
        *,
        version: int | None = None,
        periods: Collection[str] | None = None,
        states: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the rows of table ``kind`` whose ``dataset`` column equals ``dataset``.

        ``version``, ``periods`` and ``states`` narrow the read to one binning version, a set of periods and a set of
        alert states; a filter left ``None`` keeps every row. Implementations push them into the storage read.
        """
        ...


@dataclass
class MemoryStore:
    """A :class:`Store` over lists of rows per table kind (tests, the pure CLI, the in-memory table I/O)."""

    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def rows(
        self,
        kind: str,
        dataset: str,
        *,
        version: int | None = None,
        periods: Collection[str] | None = None,
        states: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the stored rows of ``dataset``, narrowed by the optional filters."""
        keep = {} if periods is None else dict.fromkeys(periods)
        return [
            dict(r)
            for r in self.tables.get(kind, [])
            if r.get("dataset") == dataset
            and (version is None or int(r.get("version") or 0) == version)
            and (periods is None or str(r.get("period")) in keep)
            and (states is None or r.get("state") in states)
        ]


@dataclass(frozen=True)
class Outcome:
    """What one check concluded, summarized for the run report."""

    dataset: str
    status: str
    version: int = 0
    latest_period: str = ""
    periods_profiled: int = 0
    red: int = 0
    yellow: int = 0
    open_alerts: int = 0
    critical_alerts: int = 0
    message: str = ""
    output: str = ""
    suppressed: int = 0
    incident_id: str = ""
    severity_score: float = 0.0
    upstream: str = ""
    silenced: int = 0

    @property
    def failed(self) -> bool:
        """Tell whether the gate failed."""
        return self.status == FAILED

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        extra: dict[str, Any] = {"incident_id": self.incident_id} if self.incident_id else {}
        if self.suppressed:
            extra["suppressed"] = self.suppressed
        if self.incident_id:
            extra["severity_score"] = self.severity_score
        if self.upstream:
            extra["upstream"] = self.upstream
        if self.silenced:
            extra["silenced"] = self.silenced
        return {
            **extra,
            "critical_alerts": self.critical_alerts,
            "dataset": self.dataset,
            "latest_period": self.latest_period,
            "message": self.message,
            "open_alerts": self.open_alerts,
            "output": self.output,
            "periods_profiled": self.periods_profiled,
            "red": self.red,
            "status": self.status,
            "version": self.version,
            "yellow": self.yellow,
        }


@dataclass
class CheckResult:
    """The rows to write per stability table, the outcome and what profiling skipped."""

    tables: dict[str, list[dict[str, Any]]]
    outcome: Outcome
    skipped: dict[str, str] = field(default_factory=dict)
    binning: Reference | None = None
    notifications: list[AlertEvent] = field(default_factory=list)
    incident: Incident | None = None


def judgment_fingerprint(config: StabilityConfig, actions: Sequence[Mapping[str, Any]] = ()) -> str:
    """Hash what decides colors and alerts (not bins): a change rewrites the results of the whole window.

    The human actions of the dataset are part of the judgment: their latest decision per alert enters the hash, so a
    new decision re-judges the window and replays stay deterministic. Without actions the hash is the declaration's.
    """
    data = config.to_dict()
    content = {k: data[k] for k in ("baseline", "column_limits", "limits", "min_rows", "persistence", "trend")}
    content |= {"epsilon": config.epsilon, "trend_metrics": data["trend_metrics"], "weight": config.weight}
    for optional in (
        "noise", "rules", "revision", "trend_directions", "cadence", "expected_by", "settle_periods", "settle_days",
    ):  # fmt: skip
        if optional in data:
            content[optional] = data[optional]
    if actions:
        decided = latest_actions(actions, entity="dataset")
        content["actions"] = sorted(
            [a, str(r["action_id"]), str(r.get("valid_until") or "")] for a, r in decided.items()
        )
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _date_of(stamp: str) -> date:
    """Return the date of an ISO timestamp, or today when ``stamp`` is not one (tests pass labels as ``now``)."""
    try:
        return date.fromisoformat(stamp[:10])
    except ValueError:
        return parse_as_of(None)


def _engine_periods(data: Any, period: str) -> list[str]:  # noqa: ANN401 - rows or DataFrame
    if is_rows(data):
        return periods_of_rows(data, period)
    from bricksmith.monitor.stability.spark import periods_spark  # noqa: PLC0415 - heavy integration

    return periods_spark(data, period)


def _fit(  # noqa: PLR0913 - one keyword per reference field
    data: Any,  # noqa: ANN401 - rows or DataFrame
    config: StabilityConfig,
    periods: Sequence[str],
    *,
    dataset: str,
    version: int,
    source: str,
) -> tuple[Reference, dict[str, str]]:
    if is_rows(data):
        return fit_rows_binning(data, config, periods, dataset=dataset, version=version, source=source)
    from bricksmith.monitor.stability.spark import fit_spark_binning  # noqa: PLC0415

    return fit_spark_binning(data, config, periods, dataset=dataset, version=version, source=source)


def _profile(  # noqa: PLR0913 - one keyword per profile field
    binning: Reference,
    data: Any,  # noqa: ANN401 - rows or DataFrame
    config: StabilityConfig,
    periods: Sequence[str],
    *,
    dataset: str,
    run_id: str,
    segments: tuple[Sequence[str], Mapping[str, str]] = ((), {}),
) -> list[dict[str, Any]]:
    """Profile ``periods``; ``segments`` is ``(periods the kept set is ranked over, carried segments)``."""
    if not periods:
        return []
    rank, carried = segments
    if is_rows(data):
        return profile_rows(
            binning, data, config, periods, dataset=dataset, run_id=run_id, rank_periods=rank, carried=carried
        )[0]
    from bricksmith.monitor.stability.spark import profile_spark  # noqa: PLC0415

    return profile_spark(
        binning, data, config, periods, dataset=dataset, run_id=run_id, rank_periods=rank, carried=carried
    )[0]


def carried_segments(stored: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Return ``segment key -> earliest period`` of the segments with stored rows: they stay kept and judged."""
    first: dict[str, str] = {}
    for row in stored:
        segment, period = str(row["segment"]), str(row["period"])
        if segment and int(row.get("count") or 0) > 0:
            first[segment] = min(period, first.get(segment, period))
    return first


def fit_range(config: StabilityConfig, periods: Sequence[str]) -> list[str]:
    """Return the periods a new binning is fitted on: the fixed baseline range, else the earliest ``fit_periods``."""
    if config.baseline.kind == FIXED and config.baseline.start:
        chosen = [p for p in periods if config.baseline.start <= p <= config.baseline.end]
        if not chosen:
            msg = (
                f"stability: no period of the table lies in the fixed baseline "
                f"{config.baseline.start}..{config.baseline.end}"
            )
            raise MonitorError(msg)
        return chosen
    return list(periods[: config.fit_periods])


def _active_binning(  # noqa: PLR0913 - one keyword per binning input
    data: Any,  # noqa: ANN401 - rows or DataFrame
    config: StabilityConfig,
    store: Store,
    periods: Sequence[str],
    *,
    dataset: str,
    source: str,
    run_id: str,
    now: str,
) -> tuple[Reference, list[dict[str, Any]], dict[str, str]]:
    """Return the binning of the current configuration, fitting a new version when none matches."""
    wanted = config.binning_fingerprint()
    stored = store.rows(BINNINGS, dataset)
    matching = [r for r in stored if r.get("config_fingerprint") == wanted]
    if matching:
        row = max(matching, key=lambda r: int(r["version"]))
        skipped = dict(item.split(":", 1) for item in str(row.get("skipped") or "").split(",") if ":" in item)
        return Reference.from_json(str(row["body"])), [], skipped
    version = max((int(r["version"]) for r in stored), default=0) + 1
    binning, skipped = _fit(data, config, fit_range(config, periods), dataset=dataset, version=version, source=source)
    row = {
        "dataset": dataset, "version": version, "config_fingerprint": wanted, "fingerprint": binning.fingerprint,
        "period_column": config.period, "fit_periods": ",".join(fit_periods_of(binning)), "fit_rows": binning.fit_rows,
        "columns": len(binning.columns), "skipped": ",".join(f"{c}:{r}" for c, r in sorted(skipped.items())),
        "body": binning.to_json(), "created_run_id": run_id, "created_at": now,
    }  # fmt: skip
    return binning, [row], skipped


def _history_span(config: StabilityConfig) -> int:
    """Return how many periods before the window the baseline may reach."""
    if config.baseline.kind == PREVIOUS:
        return config.baseline.lag
    if config.baseline.kind == ROLLING:
        return config.baseline.periods
    return 0


def _loaded_periods(config: StabilityConfig, binning: Reference, periods: Sequence[str]) -> list[str]:
    """Return the periods of the window plus the history its baseline reads: all a run ever loads."""
    start = max(0, len(periods) - config.max_periods - _history_span(config))
    loaded = set(periods[start:])
    if config.baseline.kind == FIXED:
        base = (
            [p for p in periods if config.baseline.start <= p <= config.baseline.end]
            if config.baseline.start
            else list(fit_periods_of(binning))
        )
        loaded |= set(base)
    return sorted(loaded)


def reprocessed_periods(
    config: StabilityConfig, periods: Sequence[str], reprocess: Sequence[str], *, skip_unknown: bool = False
) -> list[str]:
    """Return the periods to re-profile, sorted and unique.

    Each item is a period or an inclusive ``first..last`` range of the data's periods. The ``max_periods`` window
    bounds how much of a table a reprocess may rescan: a period of the data outside it, or not in the data at all,
    is an error, or is dropped with ``skip_unknown`` (the runner hands one request to every declaring task).
    """
    window = list(periods[-config.max_periods :])
    chosen: set[str] = set()
    unknown: list[str] = []
    for item in reprocess:
        first, sep, last = item.partition("..")
        found = [p for p in window if first <= p <= last] if sep else [p for p in window if p == item]
        chosen.update(found)
        if not found:
            unknown.append(item)
    if unknown and not skip_unknown:
        msg = (
            f"stability: cannot reprocess {', '.join(sorted(unknown))}: no such period of the data inside the last "
            f"{config.max_periods} periods"
        )
        raise MonitorError(msg)
    return sorted(chosen)


def _plan_periods(
    config: StabilityConfig, binning: Reference, periods: Sequence[str], profiled: set[str], reprocess: Sequence[str]
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(window, periods to profile, periods to load)``: new periods, the refresh range, the reprocessed."""
    window = list(periods[-config.max_periods :])
    loaded = _loaded_periods(config, binning, periods)
    refresh = (set(window[-config.refresh_periods :]) if config.refresh_periods else set()) | set(reprocess)
    todo = [p for p in loaded if p not in profiled or p in refresh]
    return window, todo, loaded


def _gap_of(cal: Any) -> Any:  # noqa: ANN401 - the cadence calendar or None
    """Return the ``baseline_gap`` function for ``compute`` (``None`` without a cadence)."""
    if cal is None:
        return None

    def gap(wanted: Sequence[str], period: str) -> int:
        known = [cal.dates[p] for p in wanted if p in cal.dates]
        return gap_between(cal, min(known), cal.dates[period]) if known and period in cal.dates else 0

    return gap


def _trend_seed(  # noqa: PLR0913 - the run's periods and the store coordinates
    config: StabilityConfig,
    store: Store,
    periods: Sequence[str],
    window: Sequence[str],
    *,
    dataset: str,
    version: int,
) -> dict[tuple[str, str, str], list[float]]:
    """Return the stored colored values of the ``trend.window`` periods before the window, per series."""
    policy = config.trend
    if policy is None or not window:
        return {}
    before = [p for p in periods if p < window[0]][-policy.window :]
    if not before:
        return {}
    series: dict[tuple[str, str, str], list[tuple[str, float]]] = {}
    for row in store.rows(RESULTS, dataset, version=version, periods=before):
        value = row.get("value")
        if row.get("metric") not in config.trend_metrics or row.get("status") not in COLORS or not row.get("direction"):
            continue
        if row.get("settling"):
            continue
        if value is None or not math.isfinite(float(value)):
            continue
        if not keeps(policy, str(row.get("status") or ""), str(row.get("trend_status") or "")):
            continue
        key = (str(row.get("segment") or ""), str(row.get("column") or ""), str(row["metric"]))
        series.setdefault(key, []).append((str(row["period"]), float(value)))
    return {key: [v for _, v in sorted(points)] for key, points in series.items()}


def _red_seed(  # noqa: PLR0913 - the run's periods and the store coordinates
    config: StabilityConfig,
    store: Store,
    periods: Sequence[str],
    window: Sequence[str],
    *,
    dataset: str,
    version: int,
) -> dict[str, set[str]]:
    """Return, per segment, the stored periods before the window whose ``volume_change`` or ``psi`` was red."""
    if config.baseline.exclude != "red" or config.baseline.kind == FIXED or not window:
        return {}
    before = [p for p in periods if p < window[0]][-_history_span(config) :]
    if not before:
        return {}
    found: dict[str, set[str]] = {}
    for row in store.rows(RESULTS, dataset, version=version, periods=before):
        if row.get("status") == RED and row.get("metric") in (VOLUME_CHANGE, PSI):
            found.setdefault(str(row.get("segment") or ""), set()).add(str(row["period"]))
    return found


def _counts(results: Sequence[Mapping[str, Any]], periods: Collection[str]) -> dict[str, int]:
    """Return how many results of ``periods`` together are red and yellow."""
    total = {RED: 0, YELLOW: 0}
    for period in periods:
        found = counts_in(results, period)
        total[RED] += found[RED]
        total[YELLOW] += found[YELLOW]
    return total


def _gate(config: StabilityConfig, red: int, yellow: int, open_alerts: int, critical: int) -> list[str]:
    reasons = []
    for condition in config.gate.fail_on:
        if condition == "red" and red:
            reasons.append(f"{red} red result(s)")
        elif condition == "yellow" and red + yellow:
            reasons.append(f"{red + yellow} yellow or red result(s)")
        elif condition == "critical" and critical:
            reasons.append(f"{critical} open critical alert(s)")
        elif condition == "alert" and open_alerts:
            reasons.append(f"{open_alerts} open alert(s)")
    return reasons


def _superseded(  # noqa: PLR0913 - the rows to compare and where the replay starts
    stored: Sequence[Mapping[str, Any]],
    fresh: Sequence[Mapping[str, Any]],
    run_id: str,
    now: str,
    *,
    version: int,
    start: str,
    rejudged: bool = False,
) -> list[dict[str, Any]]:
    """Return stored alerts that the replay no longer produces, marked ``superseded``.

    A ``closed`` alert of ``version`` is final when it opened at or before the window ``start`` (the replay can no
    longer re-judge its opening) or when the judgment is unchanged (``rejudged`` false): the window only slid past
    the run that produced it. Either way it stays closed.
    """
    ids = {r["alert_id"] for r in fresh}
    return [
        {**r, "state": SUPERSEDED, "run_id": run_id, "updated_at": now}
        for r in stored
        if r["alert_id"] not in ids
        and r.get("state") in (OPEN, CLOSED)
        and not (
            r.get("state") == CLOSED
            and int(r.get("version") or 0) == version
            and (not rejudged or str(r.get("opened_period") or "") <= start)
        )
    ]


def _changed(row: Mapping[str, Any], before: Mapping[str, Any], threshold: float) -> bool:
    """Tell whether the last value moved by at least ``threshold`` (relative) since the stored open row."""
    new, old = row.get("last_value"), before.get("last_value")
    if new is None or old is None:
        return False
    return abs(float(new) - float(old)) >= threshold * max(abs(float(old)), 1e-12)


def _transitions(row: Mapping[str, Any], before: Mapping[str, Any] | None, policy: NotifyPolicy) -> list[str]:
    """Return the transitions an open alert row went through since ``before`` (its stored open row, if any).

    An alert that stays open adds ``reminded`` when its flagged periods grew to a multiple of ``remind_after`` and
    ``changed`` when its value moved materially; an unchanged open alert yields nothing.
    """
    if before is None:
        return [RECURRED if row.get("recurrence") else OPENED]
    found = []
    if str(before.get("severity")) != CRITICAL and str(row["severity"]) == CRITICAL:
        found.append(ESCALATED)
    if row.get("recurrence") and not before.get("recurrence"):
        found.append(RECURRED)
    flagged, seen = int(row.get("flagged_periods") or 0), int(before.get("flagged_periods") or 0)
    if policy.remind_after and flagged > seen and flagged % policy.remind_after == 0:
        found.append(REMINDED)
    if policy.material_change and not found and _changed(row, before, policy.material_change):
        found.append(CHANGED)
    return found


def alert_events(  # noqa: PLR0913 - one keyword per event field
    fresh: Sequence[Mapping[str, Any]],
    stored: Sequence[Mapping[str, Any]],
    policy: NotifyPolicy | None,
    *,
    dataset: str,
    target: str,
    run_id: str,
    occurred_at: str,
) -> list[AlertEvent]:
    """Return one event per alert that opened, escalated or recurred, against the stored open alerts.

    An alert whose stored open row says the same thing yields nothing, so replays and sliding windows (a carried
    alert keeps its id) stay silent. The key ``stability:<dataset>:<alert_id>:<transition>`` is stable across runs.
    """
    if policy is None or not policy.on:
        return []
    from bricksmith.ops.events import STATISTICAL_ALERT  # noqa: PLC0415 - keeps the engine import light
    from bricksmith.ops.notify import AlertEvent  # noqa: PLC0415

    before = {str(r["alert_id"]): r for r in stored if r.get("state") == OPEN}
    events = []
    for row in sorted(fresh, key=lambda r: str(r["alert_id"])):
        if row["state"] != OPEN:
            continue
        for transition in _transitions(row, before.get(str(row["alert_id"])), policy):
            severity = "critical" if row["severity"] == CRITICAL else "warning"
            if not policy.wants(transition, severity):
                continue
            events.append(
                AlertEvent(
                    STATISTICAL_ALERT,
                    dataset,
                    f"{transition}: {row['message']}",
                    severity,
                    STATISTICAL_ALERT,
                    target,
                    run_id,
                    occurred_at=occurred_at,
                    key=f"stability:{dataset}:{row['alert_id']}:{transition}",
                    details={
                        "alert_id": row["alert_id"],
                        "transition": transition,
                        "kind": row["kind"],
                        "metric": row["metric"],
                        "column": row["column"],
                        "segment": row["segment_label"],
                        "opened_period": row["opened_period"],
                        "last_period": row["last_period"],
                    },
                )
            )
    return events


def _vanished(
    config: StabilityConfig, store: Store, periods: Sequence[str], *, dataset: str, version: int
) -> list[str]:
    """Return the snapshotted periods of the refresh range that the data no longer has, oldest first.

    The range is the ``refresh_periods`` most recent periods of stored history (the periods with a first-publication
    snapshot), the latest included. Periods older than the range are not judged.
    """
    if not config.revision or not config.refresh_periods:
        return []
    stored = sorted({str(r["period"]) for r in store.rows(REVISIONS, dataset, version=version)})
    return [p for p in stored[-config.refresh_periods :] if p not in set(periods)]


def _emptied(  # noqa: PLR0913 - the store read and the zeroed fields
    store: Store, vanished: Sequence[str], *, dataset: str, version: int, weighted: bool, run_id: str
) -> list[dict[str, Any]]:
    """Return the stored profile rows of ``vanished`` periods replaced by zero-count rows of this run."""
    if not vanished:
        return []
    return [
        {
            **r,
            "count": 0,
            "weight": 0.0 if weighted else None,
            "value_sum": None if r.get("value_sum") is None else 0.0,
            "value_sum_sq": None if r.get("value_sum_sq") is None else 0.0,
            "run_id": run_id,
        }
        for r in store.rows(PROFILES, dataset, version=version, periods=vanished)
    ]


def _history_rows(  # noqa: PLR0913 - the run's facts stamped on every row
    results: Sequence[Mapping[str, Any]],
    config: StabilityConfig,
    periods: Sequence[str],
    *,
    judged_at: str,
    judgment: str,
    data_version: int | None,
) -> list[dict[str, Any]]:
    """Return the results this run emitted as history rows, limited to the latest ``history_periods`` periods."""
    kept = set(sorted(set(periods))[-config.history_periods :]) if config.history_periods else set(periods)
    return [
        {
            **{c: r.get(c) for c in SCHEMAS[RESULTS_HISTORY]},
            "judged_at": judged_at,
            "judgment_fingerprint": judgment,
            "data_version": data_version,
        }
        for r in results
        if r["period"] in kept
    ]


def _event_row(kind: str, alert: Mapping[str, Any], run_id: str, occurred_at: str, **extra: str) -> dict[str, Any]:
    state = str(alert.get("state") or "")
    period = alert.get("closed_period") if state == CLOSED else alert.get("last_period")
    row = {
        "dataset": alert.get("dataset"), "alert_id": alert["alert_id"], "event": kind, "run_id": run_id,
        "period": period or "", "severity": alert.get("severity") or "", "state": state, "resolution": "",
        "action_id": "", "message": alert.get("message") or "", "occurred_at": occurred_at,
    }  # fmt: skip
    return {**row, **extra}


def _alert_event_rows(  # noqa: PLR0913 - the alert sets compared and the run's identity
    fresh: Sequence[Mapping[str, Any]],
    stored: Sequence[Mapping[str, Any]],
    superseded: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    recorded: Collection[str],
    *,
    run_id: str,
    occurred_at: str,
) -> list[dict[str, Any]]:
    """Return one row per alert transition of this run, and per human action not recorded before.

    Transitions compare the automatic alert rows with the stored ones: ``opened`` (no stored open row),
    ``escalated`` (became critical), ``closed`` (an open alert now closed) and ``superseded`` (a stored alert the
    replay no longer produces). An action yields ``action_applied`` once, keyed by its ``action_id``.
    """
    before = {str(r["alert_id"]): r for r in stored if r.get("state") == OPEN}
    events: list[dict[str, Any]] = []
    for row in sorted(fresh, key=lambda r: str(r["alert_id"])):
        prior = before.get(str(row["alert_id"]))
        if row["state"] == OPEN:
            if prior is None:
                events.append(_event_row(OPENED, row, run_id, occurred_at))
            elif str(prior.get("severity")) != CRITICAL and str(row["severity"]) == CRITICAL:
                events.append(_event_row(ESCALATED, row, run_id, occurred_at))
        elif row["state"] == CLOSED and prior is not None:
            events.append(_event_row(CLOSED, row, run_id, occurred_at))
    events += [
        _event_row(SUPERSEDED, r, run_id, occurred_at) for r in sorted(superseded, key=lambda r: str(r["alert_id"]))
    ]
    known = {str(r["alert_id"]): r for r in [*stored, *fresh]}
    for action in sorted(actions, key=lambda a: str(a["action_id"])):
        if str(action["action_id"]) in recorded:
            continue
        alert = {**known.get(str(action["alert_id"]), {}), "alert_id": action["alert_id"], "dataset": action["dataset"]}
        events.append(
            _event_row(
                "action_applied",
                {**alert, "state": action["action"], "message": action.get("reason") or ""},
                run_id,
                occurred_at,
                resolution=str(action.get("resolution") or ""),
                action_id=str(action["action_id"]),
            )
        )
    return events


def _history_tables(  # noqa: PLR0913, PLR0917 - the run's rows and the facts stamped on the history
    config: StabilityConfig,
    store: Store,
    results: Sequence[Mapping[str, Any]],
    judged: tuple[Sequence[str], Sequence[Mapping[str, Any]], Sequence[Mapping[str, Any]]],
    superseded: Sequence[Mapping[str, Any]],
    actions: Sequence[Mapping[str, Any]],
    *,
    dataset: str,
    run_id: str,
    judged_at: str,
    judgment: str,
    data_version: int | None,
) -> dict[str, list[dict[str, Any]]]:
    """Return the ``history`` tables of one run: its emitted results and its alert transitions."""
    periods, fresh, stored = judged
    recorded = {str(r["action_id"]) for r in store.rows(ALERT_EVENTS, dataset) if r.get("action_id")}
    return {
        RESULTS_HISTORY: _history_rows(
            results, config, periods, judged_at=judged_at, judgment=judgment, data_version=data_version
        ),
        ALERT_EVENTS: _alert_event_rows(
            fresh, stored, superseded, actions, recorded, run_id=run_id, occurred_at=judged_at
        ),
    }


def _revisions(  # noqa: PLR0913, PLR0917 - the inputs of one comparison and one snapshot
    computed: Any,  # noqa: ANN401 - the metrics.Computed being extended
    binning: Reference,
    config: StabilityConfig,
    store: Store,
    history: tuple[Sequence[str], Sequence[str]],
    profiles: tuple[Sequence[dict[str, Any]], Sequence[dict[str, Any]], Sequence[dict[str, Any]]],
    *,
    dataset: str,
    run_id: str,
    now: str,
    vanished: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Add the revision results to ``computed`` and return the first-publication rows of periods not seen before."""
    window, loaded = history
    stored, fresh, current = profiles
    snapshots = store.rows(REVISIONS, dataset, version=binning.version, periods=[*loaded, *vanished])
    computed.results += revision_results(
        binning, current, snapshots, config, dataset=dataset, window=sorted({*window, *vanished}), run_id=run_id,
        computed_at=now,
    )  # fmt: skip
    seen = {str(r["period"]) for r in snapshots}
    stored_periods = {str(r["period"]) for r in stored}
    new = [p for p in loaded if p not in seen]
    first = [r for r in stored if r["period"] in new] + [r for r in fresh if r["period"] not in stored_periods]
    return snapshot_rows(first, new, run_id=run_id, published_at=now)


def _budgeted(
    alert_rows: list[dict[str, Any]],
    stored: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    config: StabilityConfig,
    latest: str,
) -> tuple[list[dict[str, Any]], int]:
    """Apply the declared noise budget to the alerts new in this run; no budget keeps every row."""
    if config.noise is None:
        return alert_rows, 0
    return apply_noise(alert_rows, {str(r["alert_id"]) for r in stored}, results, config, config.noise, latest=latest)


def _grouped(
    open_rows: Sequence[Mapping[str, Any]],
    rows: tuple[Sequence[Any], Sequence[Any], Sequence[Any], Sequence[Any]],
    notifications: list[AlertEvent],
    config: StabilityConfig,
    run: tuple[str, str, str, str, str, int, str],
) -> tuple[Incident | None, list[AlertEvent], dict[str, dict[str, Any]]]:
    """Return the incident of the run, the notifications (one digest when declared) and the false-positive rates."""
    results, profiles, alert_rows, actions = rows
    dataset, target, run_id, occurred_at, latest, suppressed, upstream = run
    rates = false_positive_rates(actions, alert_rows, results) if actions else {}
    incident = build_incident(
        open_rows, results, profiles, config, dataset=dataset, period=latest, suppressed=suppressed,
        false_positive_rates=rates, upstream=upstream,
    )  # fmt: skip
    if config.notify is not None and config.notify.digest and notifications:
        notifications = [
            digest_event(
                notifications, incident, dataset=dataset, target=target, run_id=run_id, occurred_at=occurred_at,
                top=config.notify.top, meta=ownership(config, upstream),
            )
        ]  # fmt: skip
    return incident, notifications, rates


def ownership(config: StabilityConfig, upstream: str = "") -> dict[str, str]:
    """Return the routing facts of a dataset: its owner, domain and criticality, and the upstream origin if any."""
    found = {"owner": config.owner, "domain": config.domain, "criticality": config.criticality, "upstream": upstream}
    return {k: v for k, v in found.items() if v}


def routed(events: Sequence[AlertEvent], meta: Mapping[str, str]) -> list[AlertEvent]:
    """Return ``events`` with the routing facts (owner, domain, criticality, upstream) added to their details."""
    return [replace(e, details={**e.details, **meta}) for e in events]


def _silenced(notifications: list[AlertEvent], origin: str, config: StabilityConfig) -> tuple[list[AlertEvent], int]:
    """Return the notifications left and how many were silenced because the incident is caused upstream."""
    if origin and config.suppress_downstream:
        return [], len(notifications)
    return notifications, 0


def _labeled(alert_rows: Sequence[dict[str, Any]], origin: str, open_rows: Sequence[Any]) -> str:
    """Stamp ``upstream`` on the alert rows (the origin on open ones) and return the origin that applies."""
    origin = origin if open_rows else ""
    for row in alert_rows:
        row["upstream"] = origin if row["state"] == OPEN else ""
    return origin


def _held(
    alert_rows: list[dict[str, Any]], actions: list[dict[str, Any]], today: str
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Return the alerts a human accepted or closed, and the rows with that state applied."""
    human = {
        str(a["alert_id"]): a["state"]
        for a in apply_actions(alert_rows, actions, today, "dataset")
        if a["state"] in (STATE_ACCEPTED, STATE_CLOSED)
    }
    return human, [{**r, "state": human[str(r["alert_id"])]} if str(r["alert_id"]) in human else r for r in alert_rows]


def _caused(origin: str, silenced: int) -> str:
    """Return the run message part naming the upstream origin and the notifications it silenced."""
    if not origin:
        return ""
    return f"caused upstream by {origin}" + (f", {silenced} notification(s) silenced" if silenced else "")


def _tail(
    accepted: int, suppressed: int, reasons: Sequence[str], rates: Mapping[str, Mapping[str, Any]], caused: str = ""
) -> str:
    """Return what the run message appends: accepted alerts, the budget's suppressions, the gate and the rates."""
    found = [
        f"{accepted} accepted" if accepted else "",
        f"{suppressed} suppressed by the noise budget" if suppressed else "",
        caused,
        f"gate failed on {'; '.join(reasons)}" if reasons else "",
        "false positives " + ", ".join(f"{k} {v['false_positive']}/{v['closed']}" for k, v in rates.items())
        if rates
        else "",
    ]
    return "".join(f"; {part}" for part in found if part)


def _accepted(alert_rows: Sequence[Mapping[str, Any]], human: Mapping[str, str]) -> int:
    """Count the open alerts a human accepted."""
    return sum(1 for r in alert_rows if r["state"] == OPEN and human.get(str(r["alert_id"])) == STATE_ACCEPTED)


def _rewrites(previous: Sequence[Mapping[str, Any]], judgment: str) -> bool:
    """Tell whether the whole window is judged again: no earlier run, or its judgment fingerprint differs."""
    last = max(previous, key=lambda r: str(r.get("finished_at") or ""), default=None)
    return last is None or last.get("judgment_fingerprint") != judgment


def check(  # noqa: PLR0913, PLR0915 - one keyword per documented input; one sequential check
    data: Any,  # noqa: ANN401 - rows or DataFrame
    config: StabilityConfig,
    store: Store,
    *,
    dataset: str,
    run_id: str,
    task_id: str = "",
    physical: str = "",
    now: str | None = None,
    as_of: str | date | None = None,
    reprocess: Sequence[str] = (),
    skip_unknown: bool = False,
    data_version: int | None = None,
    upstream: Sequence[Upstream] = (),
) -> CheckResult:
    """Check ``data`` (rows or a DataFrame) against its stored history and return the rows to write.

    ``as_of`` is the date the cadence is judged at (an ISO date); by default the date of ``now``.
    ``reprocess`` names periods rewritten since their stored profile (a backfill or a correction): they are
    profiled again with the declared refresh range, the whole window is judged again and the alerts are replayed.
    An item is a period or a ``first..last`` range; ``skip_unknown`` drops what the data does not have.
    ``data_version`` is the Delta version of the monitored table this run read, stored with the history rows.
    ``upstream`` lists the red incidents the tasks this one reads from left in the same run: one of the incident's
    period labels it ``upstream:<origin>`` and, with ``suppress_downstream``, silences its notifications.
    """
    started = now or _now()
    as_of_date = parse_as_of(as_of) if as_of else _date_of(started)
    periods = _engine_periods(data, config.period)
    if not periods:
        msg = f"stability: {dataset} has no rows with a {config.period!r} period"
        raise MonitorError(msg)
    redo = reprocessed_periods(config, periods, reprocess, skip_unknown=skip_unknown)
    binning, binning_rows, skipped = _active_binning(
        data, config, store, periods, dataset=dataset, source=physical or dataset, run_id=run_id, now=started
    )
    stored_profiles = store.rows(
        PROFILES, dataset, version=binning.version, periods=_loaded_periods(config, binning, periods)
    )
    cal = build_calendar(config, periods) if has_cadence(config) else None
    settling = settling_periods(config, periods, as_of_date, cal)
    window, todo, loaded = _plan_periods(config, binning, periods, {str(r["period"]) for r in stored_profiles}, redo)
    keep = set(loaded) - set(todo)
    fresh = _profile(
        binning, data, config, todo, dataset=dataset, run_id=run_id,
        segments=(loaded, carried_segments([r for r in stored_profiles if r["period"] in keep])),
    )  # fmt: skip
    vanished = _vanished(config, store, periods, dataset=dataset, version=binning.version)
    emptied = _emptied(
        store, vanished, dataset=dataset, version=binning.version, weighted=bool(config.weight), run_id=run_id
    )
    profiles = [r for r in stored_profiles if r["period"] in keep] + fresh + emptied
    computed = compute(
        binning, profiles, config, dataset=dataset, window=window, run_id=run_id, computed_at=started,
        emptied=vanished, settling=settling, gap=_gap_of(cal),
        trend_seed=_trend_seed(config, store, periods, window, dataset=dataset, version=binning.version),
        red_seed=_red_seed(config, store, periods, window, dataset=dataset, version=binning.version),
    )  # fmt: skip
    revision_rows = (
        _revisions(
            computed, binning, config, store, (window, loaded), (stored_profiles, fresh, profiles),
            dataset=dataset, run_id=run_id, now=started, vanished=vanished,
        )
        if config.revision
        else []
    )  # fmt: skip
    fresh = fresh + emptied
    for row in fresh:
        share, base, contribution = computed.shares.get(
            (row["period"], row["segment"], row["column"], row["bin"]), (None, None, None)
        )
        row.update(share=share, baseline_share=base, contribution=contribution)
    actions = store.rows(ACTIONS, dataset)
    judgment = judgment_fingerprint(config, actions)
    rewrite_all = _rewrites(store.rows(RUNS, dataset, version=binning.version), judgment) or bool(redo)
    cadence_rows = (
        cadence_results(
            config, cal, binning=binning, dataset=dataset, as_of=as_of_date, run_id=run_id, computed_at=started,
            stored=[r for r in store.rows(RESULTS, dataset, version=binning.version, periods=window)
                    if str(r.get("metric")) in CADENCE_METRICS],
        )
        if cal is not None
        else []
    )  # fmt: skip
    emitted = (
        set(window) if rewrite_all or config.settle_periods or config.settle_days else set(todo) & set(window)
    ) | set(vanished)
    results = [r for r in computed.results if r["period"] in emitted] + cadence_rows
    stored_alerts = store.rows(ALERTS, dataset, states=(OPEN, CLOSED))
    alert_rows = alerts(
        [*computed.results, *cadence_rows], config, dataset=dataset, version=binning.version, run_id=run_id,
        updated_at=started,
        stored=stored_alerts, periods=sorted({*periods, *vanished}), keep_inside=not rewrite_all,
    )  # fmt: skip
    latest = max(
        [window[-1], *vanished, *(r["period"] for r in cadence_rows if r["metric"] == PERIOD_LATE and r["value"])]
    )
    alert_rows, suppressed = _budgeted(alert_rows, stored_alerts, computed.results, config, latest)
    human, held = _held(alert_rows, actions, started[:10])
    origin = upstream_origin(upstream, latest)
    notifications = routed(
        alert_events(
            held,
            stored_alerts,
            config.notify,
            dataset=dataset,
            target=physical or dataset,
            run_id=run_id,
            occurred_at=started,
        ),
        ownership(config, origin),
    )
    notifications, silenced = _silenced(notifications, origin, config)
    superseded = _superseded(
        stored_alerts, alert_rows, run_id, started, version=binning.version, start=window[0], rejudged=rewrite_all
    )
    alert_rows += superseded
    judged = {latest} | (set(todo) & set(window) | set(vanished) if config.gate.periods == "profiled" else set())
    counts = _counts([*computed.results, *cadence_rows], judged)
    open_rows = [r for r in alert_rows if r["state"] == OPEN and str(r["alert_id"]) not in human]
    origin = _labeled(alert_rows, origin, open_rows)
    critical = sum(1 for r in open_rows if r["severity"] == CRITICAL)
    reasons = _gate(config, counts[RED], counts[YELLOW], len(open_rows), critical)
    status = FAILED if reasons else WARNED if counts[RED] + counts[YELLOW] + len(open_rows) else PASSED
    incident, notifications, rates = _grouped(
        open_rows, (computed.results, fresh, [*alert_rows, *stored_alerts], actions), notifications, config,
        (dataset, physical or dataset, run_id, started, latest, suppressed, origin),
    )  # fmt: skip
    message = (
        f"{dataset} {latest}: {counts[RED]} red, {counts[YELLOW]} yellow, {len(open_rows)} open alert(s)"
        + (f" over {len(judged)} periods" if len(judged) > 1 else "")
        + (f"; reprocessed {', '.join(redo)} ({len(todo)} periods profiled)" if redo else "")
        + _tail(_accepted(alert_rows, human), suppressed, reasons, rates, _caused(origin, silenced))
    )
    outcome = Outcome(
        dataset, status, binning.version, latest, len(todo), counts[RED], counts[YELLOW], len(open_rows), critical,
        message, config.output, suppressed, incident.incident_id if incident else "",
        incident.severity_score if incident else 0.0, origin, silenced,
    )  # fmt: skip
    run_row = {
        "run_id": run_id, "dataset": dataset, "task_id": task_id, "physical": physical, "version": binning.version,
        "latest_period": latest, "periods_profiled": len(todo), "status": status,
        "gate": ",".join(config.gate.fail_on), "red": counts[RED], "yellow": counts[YELLOW],
        "open_alerts": len(open_rows), "critical_alerts": critical, "message": message,
        "judgment_fingerprint": judgment, "started_at": started, "finished_at": _now(),
        "periods_reprocessed": ",".join(redo), "owner": config.owner, "domain": config.domain,
        "criticality": config.criticality, "severity_score": incident.severity_score if incident else 0.0,
        "upstream": origin,
    }  # fmt: skip
    tables = {
        BINNINGS: binning_rows,
        PROFILES: fresh,
        RESULTS: results,
        ALERTS: alert_rows,
        ACTIONS: [],
        RUNS: [run_row],
        **({REVISIONS: revision_rows} if config.revision else {}),
    }
    if config.history:
        tables |= _history_tables(
            config, store, results,
            (sorted({*window, *vanished}), alert_rows[: len(alert_rows) - len(superseded)], stored_alerts),
            superseded, actions, dataset=dataset, run_id=run_id, judged_at=started, judgment=judgment,
            data_version=data_version,
        )  # fmt: skip
    return CheckResult(tables, outcome, skipped, binning, notifications, incident)


def error_outcome(dataset: str, error: BaseException, output: str = "") -> Outcome:
    """Return the outcome recorded when the check itself failed."""
    return Outcome(dataset, ERROR, message=f"{type(error).__name__}: {error}", output=output)


__all__ = [
    "ERROR",
    "FAILED",
    "PASSED",
    "WARNED",
    "CheckResult",
    "MemoryStore",
    "Outcome",
    "StabilityGateError",
    "Store",
    "alert_events",
    "check",
    "error_outcome",
    "fit_range",
    "judgment_fingerprint",
    "ownership",
    "reprocessed_periods",
    "routed",
]
