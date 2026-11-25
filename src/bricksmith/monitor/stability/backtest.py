"""Backtest a stability judgment change over stored profiles before applying it.

The judgment is pure over the stored profiles, so a candidate configuration can be replayed without scanning the
table: :func:`backtest` reads the stored binnings, profiles, results, alerts, actions and runs through a
:class:`~bricksmith.monitor.stability.engine.Store`, runs the same ``compute`` and alert replay a real run would,
and reports what would change. It has no write path: nothing it does can alter the stored tables.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.actions import STATE_ACCEPTED, STATE_CLOSED, apply_actions
from bricksmith.monitor.limits import RED, YELLOW
from bricksmith.monitor.persistence import CLOSED, CRITICAL, OPEN
from bricksmith.monitor.reference import Reference
from bricksmith.monitor.stability.engine import (
    FAILED,
    PASSED,
    WARNED,
    _gate,  # pyright: ignore[reportPrivateUsage]
    _trend_seed,  # pyright: ignore[reportPrivateUsage]
    judgment_fingerprint,
)
from bricksmith.monitor.stability.metrics import alerts, compute
from bricksmith.monitor.stability.tables import ACTIONS, ALERTS, BINNINGS, PROFILES, RESULTS, RUNS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.monitor.stability.config import StabilityConfig
    from bricksmith.monitor.stability.engine import Store

REQUIRES_REFIT = "requires refit"
NO_STORED_PROFILES = "no stored profiles"
_FIELDS = ("status", "trend_status")
type Series = tuple[str, str, str]  # segment, column, metric


@dataclass(frozen=True)
class Change:
    """One result whose color differs between the stored judgment and the replay (``None``: absent on that side)."""

    period: str
    segment: str
    column: str
    metric: str
    field: str
    before: str | None
    after: str | None

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return dict(self.__dict__)


@dataclass(frozen=True)
class SeriesCount:
    """Color and open-alert counts of one (segment, column, metric) series, stored and replayed."""

    segment: str
    column: str
    metric: str
    before: Mapping[str, int]
    after: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "segment": self.segment, "column": self.column, "metric": self.metric,
            "before": dict(self.before), "after": dict(self.after),
        }  # fmt: skip


@dataclass(frozen=True)
class PeriodGate:
    """The gate of one period before and after, with the counts it was decided on."""

    period: str
    before: str
    after: str
    before_counts: Mapping[str, int]
    after_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "period": self.period, "before": self.before, "after": self.after,
            "before_counts": dict(self.before_counts), "after_counts": dict(self.after_counts),
        }  # fmt: skip


@dataclass
class BacktestReport:
    """What a candidate configuration would change over the stored profiles."""

    dataset: str
    version: int = 0
    periods: list[str] = field(default_factory=list)
    requires_refit: bool = False
    reason: str = ""
    judgment_changed: bool = False
    series: list[SeriesCount] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    alerts_opened: list[dict[str, Any]] = field(default_factory=list)
    alerts_closed: list[dict[str, Any]] = field(default_factory=list)
    gates: list[PeriodGate] = field(default_factory=list)

    @property
    def differences(self) -> int:
        """Count the changed results, the alerts that would open or close and the periods whose gate differs."""
        gates = sum(1 for g in self.gates if g.before != g.after)
        return len(self.changes) + len(self.alerts_opened) + len(self.alerts_closed) + gates

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "dataset": self.dataset,
            "version": self.version,
            "periods": list(self.periods),
            "requires_refit": self.requires_refit,
            "reason": self.reason,
            "judgment_changed": self.judgment_changed,
            "differences": self.differences,
            "series": [s.to_dict() for s in self.series],
            "changes": [c.to_dict() for c in self.changes],
            "alerts_opened": self.alerts_opened,
            "alerts_closed": self.alerts_closed,
            "gates": [g.to_dict() for g in self.gates],
        }

    def summary(self) -> str:
        """Return the report as text lines."""
        if self.requires_refit or not self.periods:
            return f"stability backtest {self.dataset}: {self.reason}"
        lines = [
            f"stability backtest {self.dataset} v{self.version}: {self.differences} difference(s) over "
            f"{len(self.periods)} period(s)" + ("; the judgment changed" if self.judgment_changed else "")
        ]
        lines += [
            f"  {c.period} {c.column or '-'} {c.metric} {c.field}: {c.before} -> {c.after}"
            + (f" [{c.segment}]" if c.segment else "")
            for c in self.changes
        ]
        lines += [f"  alert would open {a['severity']}: {a['message']}" for a in self.alerts_opened]
        lines += [f"  alert would close: {a['message']}" for a in self.alerts_closed]
        lines += [f"  gate {g.period}: {g.before} -> {g.after}" for g in self.gates if g.before != g.after]
        return "\n".join(lines)


def _selected(period: str, selection: Sequence[str]) -> bool:
    for item in selection:
        first, sep, last = item.partition("..")
        if (sep and first <= period <= last) or (not sep and period == item):
            return True
    return not selection


def _counts(
    results: Sequence[Mapping[str, Any]], alert_rows: Sequence[Mapping[str, Any]]
) -> dict[Series, dict[str, int]]:
    found: dict[Series, dict[str, int]] = {}
    for row in results:
        key = (str(row.get("segment") or ""), str(row.get("column") or ""), str(row["metric"]))
        slot = found.setdefault(key, {RED: 0, YELLOW: 0, "trend_red": 0, "trend_yellow": 0, "open_alerts": 0})
        for color in (RED, YELLOW):
            slot[color] += row.get("status") == color
            slot[f"trend_{color}"] += row.get("trend_status") == color
    for row in alert_rows:
        if row.get("state") != OPEN:
            continue
        key = (str(row.get("segment") or ""), str(row.get("column") or ""), str(row.get("metric") or ""))
        found.setdefault(key, {RED: 0, YELLOW: 0, "trend_red": 0, "trend_yellow": 0, "open_alerts": 0})
        found[key]["open_alerts"] += 1
    return found


def _open_at(row: Mapping[str, Any], period: str) -> bool:
    """Tell whether an alert row was open in ``period``: opened by then and not yet closed."""
    if row.get("state") not in (OPEN, CLOSED):
        return False
    if str(row.get("opened_period") or "") > period:
        return False
    closed = str(row.get("closed_period") or "")
    return not (row.get("state") == CLOSED and closed and closed <= period)


def _gate_of(
    config: StabilityConfig,
    results: Sequence[Mapping[str, Any]],
    alert_rows: Sequence[Mapping[str, Any]],
    human: Mapping[str, str],
    period: str,
) -> tuple[str, dict[str, int]]:
    red = sum(1 for r in results if r["period"] == period and r.get("status") == RED)
    yellow = sum(1 for r in results if r["period"] == period and r.get("status") == YELLOW)
    open_rows = [r for r in alert_rows if _open_at(r, period) and str(r["alert_id"]) not in human]
    critical = sum(1 for r in open_rows if r.get("severity") == CRITICAL)
    reasons = _gate(config, red, yellow, len(open_rows), critical)
    status = FAILED if reasons else WARNED if red + yellow + len(open_rows) else PASSED
    return status, {"red": red, "yellow": yellow, "open_alerts": len(open_rows), "critical_alerts": critical}


def _human_states(
    alert_rows: Sequence[Mapping[str, Any]], actions: Sequence[Mapping[str, Any]], today: str
) -> dict[str, str]:
    return {
        str(a["alert_id"]): str(a["state"])
        for a in apply_actions(alert_rows, actions, today, "dataset")
        if a["state"] in (STATE_ACCEPTED, STATE_CLOSED)
    }


def _diff_results(
    stored: Sequence[Mapping[str, Any]], fresh: Sequence[Mapping[str, Any]], selection: Sequence[str]
) -> list[Change]:
    def index(rows: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str, str, str], Mapping[str, Any]]:
        return {
            (str(r["period"]), str(r.get("segment") or ""), str(r.get("column") or ""), str(r["metric"])): r
            for r in rows
        }

    before, after = index(stored), index(fresh)
    changes = []
    for key in sorted({*before, *after}):
        if not _selected(key[0], selection):
            continue
        for name in _FIELDS:
            old = before[key].get(name) if key in before else None
            new = after[key].get(name) if key in after else None
            if (old or None) != (new or None):
                changes.append(Change(*key, name, old or None, new or None))
    return changes


def _diff_alerts(
    stored: Sequence[Mapping[str, Any]], fresh: Sequence[Mapping[str, Any]], human: Mapping[str, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    was = {str(r["alert_id"]): r for r in stored if r.get("state") == OPEN}
    now = {str(r["alert_id"]): r for r in fresh}
    opened = [dict(r) for i, r in sorted(now.items()) if r["state"] == OPEN and i not in was and i not in human]
    closed = [dict(r) for i, r in sorted(was.items()) if (i not in now or now[i]["state"] != OPEN) and i not in human]
    return opened, closed


def _stored_binning(config: StabilityConfig, store: Store, dataset: str) -> Reference | None:
    wanted = config.binning_fingerprint()
    matching = [r for r in store.rows(BINNINGS, dataset) if r.get("config_fingerprint") == wanted]
    if not matching:
        return None
    return Reference.from_json(str(max(matching, key=lambda r: int(r["version"]))["body"]))


def backtest(
    config: StabilityConfig,
    store: Store,
    *,
    dataset: str,
    periods: Sequence[str] = (),
    now: str | None = None,
) -> BacktestReport:
    """Replay ``config`` over the stored profiles of ``dataset`` and report how the judgment would change.

    The replay mirrors a real run: the window is the last ``max_periods`` stored periods, the trend is seeded with the
    stored values before it and the alerts are replayed with the stored ones carried (re-judged when the judgment
    fingerprint differs from the last run's). ``periods`` (items ``a`` or ``first..last``) narrows what is reported,
    not what is replayed. A configuration that needs another binning is reported as ``requires refit``, nothing
    compared. Nothing is ever written.
    """
    report = BacktestReport(dataset)
    binning = _stored_binning(config, store, dataset)
    if binning is None:
        report.requires_refit = True
        report.reason = f"{REQUIRES_REFIT}: the candidate bins differ from every stored binning"
        return report
    report.version = binning.version
    profiles = store.rows(PROFILES, dataset, version=binning.version)
    known = sorted({str(r["period"]) for r in profiles})
    emptied = sorted(p for p in known if sum(int(r.get("count") or 0) for r in profiles if r["period"] == p) == 0)
    live = [p for p in known if p not in emptied]
    if not live:
        report.reason = NO_STORED_PROFILES
        return report
    window = live[-config.max_periods :]
    seed_rows = _trend_seed(config, store, live, window, dataset=dataset, version=binning.version)
    computed = compute(binning, profiles, config, dataset=dataset, window=window, emptied=emptied, trend_seed=seed_rows)
    actions = store.rows(ACTIONS, dataset)
    judgment = judgment_fingerprint(config, actions)
    runs = store.rows(RUNS, dataset, version=binning.version)
    last = max(runs, key=lambda r: str(r.get("finished_at") or ""), default=None)
    report.judgment_changed = last is None or last.get("judgment_fingerprint") != judgment
    stored_alerts = store.rows(ALERTS, dataset, states=(OPEN, CLOSED))
    fresh_alerts = alerts(
        computed.results, config, dataset=dataset, version=binning.version, stored=stored_alerts,
        periods=known, keep_inside=not report.judgment_changed,
    )  # fmt: skip
    stored_alerts = [r for r in stored_alerts if int(r.get("version") or 0) == binning.version]
    covered = {*window, *emptied}
    stored_results = [r for r in store.rows(RESULTS, dataset, version=binning.version) if r["period"] in covered]
    today = (now or datetime.now(UTC).isoformat(timespec="seconds"))[:10]
    human_after = _human_states(fresh_alerts, actions, today)
    human_before = _human_states(stored_alerts, actions, today)
    selection = [str(p) for p in periods]
    report.periods = [p for p in sorted(covered) if _selected(p, selection)]
    report.changes = _diff_results(stored_results, computed.results, selection)
    opened, closed = _diff_alerts(stored_alerts, fresh_alerts, {**human_before, **human_after})
    report.alerts_opened, report.alerts_closed = opened, closed
    before_counts, after_counts = _counts(stored_results, stored_alerts), _counts(computed.results, fresh_alerts)
    report.series = [
        SeriesCount(*key, before_counts.get(key, {}), after_counts.get(key, {}))
        for key in sorted({*before_counts, *after_counts})
    ]
    for period in report.periods:
        before, b_counts = _gate_of(config, stored_results, stored_alerts, human_before, period)
        after, a_counts = _gate_of(config, computed.results, fresh_alerts, human_after, period)
        report.gates.append(PeriodGate(period, before, after, b_counts, a_counts))
    return report


__all__ = [
    "BacktestReport",
    "Change",
    "PeriodGate",
    "SeriesCount",
    "backtest",
]
