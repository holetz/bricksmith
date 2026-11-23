"""One stability check of a table: pick or fit the binning, profile new periods, judge, replay alerts, gate.

``check`` is independent of where rows are stored: it reads earlier rows through a :class:`Store` and returns
the rows to write per table. The task hook and the ``bricksmith stability check`` command both call it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from bricksmith.monitor.adapter import is_rows
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import RED, YELLOW
from bricksmith.monitor.persistence import CLOSED, CRITICAL, OPEN, SUPERSEDED
from bricksmith.monitor.reference import Reference
from bricksmith.monitor.stability.config import FIXED, PREVIOUS, ROLLING
from bricksmith.monitor.stability.metrics import alerts, compute, counts_in
from bricksmith.monitor.stability.profile import fit_periods_of, fit_rows_binning, periods_of_rows, profile_rows
from bricksmith.monitor.stability.tables import ALERTS, BINNINGS, PROFILES, RESULTS, RUNS

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.monitor.stability.config import StabilityConfig

PASSED = "passed"
WARNED = "warned"
FAILED = "failed"
ERROR = "error"


class StabilityGateError(MonitorError):
    """The declared gate of a stability check failed: the table is published, its dependents are blocked."""

    code = "stability_gate"


class Store(Protocol):
    """Reads the rows already stored for a dataset in one stability table."""

    def rows(self, kind: str, dataset: str) -> list[dict[str, Any]]:
        """Return the rows of table ``kind`` whose ``dataset`` column equals ``dataset``."""
        ...


@dataclass
class MemoryStore:
    """A :class:`Store` over lists of rows per table kind (tests, the pure CLI, the in-memory table I/O)."""

    tables: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def rows(self, kind: str, dataset: str) -> list[dict[str, Any]]:
        """Return the stored rows of ``dataset``."""
        return [dict(r) for r in self.tables.get(kind, []) if r.get("dataset") == dataset]


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

    @property
    def failed(self) -> bool:
        """Tell whether the gate failed."""
        return self.status == FAILED

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
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


def judgment_fingerprint(config: StabilityConfig) -> str:
    """Hash what decides colors and alerts (not bins): a change rewrites the results of the whole window."""
    data = config.to_dict()
    content = {k: data[k] for k in ("baseline", "column_limits", "limits", "min_rows", "persistence", "trend")}
    content |= {"epsilon": config.epsilon, "trend_metrics": data["trend_metrics"], "weight": config.weight}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


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
) -> list[dict[str, Any]]:
    if not periods:
        return []
    if is_rows(data):
        return profile_rows(binning, data, config, periods, dataset=dataset, run_id=run_id)[0]
    from bricksmith.monitor.stability.spark import profile_spark  # noqa: PLC0415

    return profile_spark(binning, data, config, periods, dataset=dataset, run_id=run_id)[0]


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


def _plan_periods(
    config: StabilityConfig, binning: Reference, periods: Sequence[str], profiled: set[str]
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(window, periods to profile, periods to load)`` for this run."""
    window = list(periods[-config.max_periods :])
    start = max(0, len(periods) - config.max_periods - _history_span(config))
    loaded = set(periods[start:])
    if config.baseline.kind == FIXED:
        base = (
            [p for p in periods if config.baseline.start <= p <= config.baseline.end]
            if config.baseline.start
            else list(fit_periods_of(binning))
        )
        loaded |= set(base)
    refresh = set(window[-config.refresh_periods :]) if config.refresh_periods else set()
    todo = sorted(p for p in loaded if p not in profiled or p in refresh)
    return window, todo, sorted(loaded)


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


def _superseded(
    stored: Sequence[Mapping[str, Any]], fresh: Sequence[Mapping[str, Any]], run_id: str, now: str
) -> list[dict[str, Any]]:
    """Return stored alerts that the replay no longer produces, marked ``superseded``."""
    ids = {r["alert_id"] for r in fresh}
    return [
        {**r, "state": SUPERSEDED, "run_id": run_id, "updated_at": now}
        for r in stored
        if r["alert_id"] not in ids and r.get("state") in (OPEN, CLOSED)
    ]


def check(  # noqa: PLR0913 - one keyword per documented input
    data: Any,  # noqa: ANN401 - rows or DataFrame
    config: StabilityConfig,
    store: Store,
    *,
    dataset: str,
    run_id: str,
    task_id: str = "",
    physical: str = "",
    now: str | None = None,
) -> CheckResult:
    """Check ``data`` (rows or a DataFrame) against its stored history and return the rows to write."""
    started = now or _now()
    periods = _engine_periods(data, config.period)
    if not periods:
        msg = f"stability: {dataset} has no rows with a {config.period!r} period"
        raise MonitorError(msg)
    binning, binning_rows, skipped = _active_binning(
        data, config, store, periods, dataset=dataset, source=physical or dataset, run_id=run_id, now=started
    )
    stored_profiles = [r for r in store.rows(PROFILES, dataset) if int(r["version"]) == binning.version]
    window, todo, loaded = _plan_periods(config, binning, periods, {str(r["period"]) for r in stored_profiles})
    fresh = _profile(binning, data, config, todo, dataset=dataset, run_id=run_id)
    keep = set(loaded) - set(todo)
    profiles = [r for r in stored_profiles if r["period"] in keep] + fresh
    computed = compute(binning, profiles, config, dataset=dataset, window=window, run_id=run_id, computed_at=started)
    for row in fresh:
        share, base, contribution = computed.shares.get(
            (row["period"], row["segment"], row["column"], row["bin"]), (None, None, None)
        )
        row.update(share=share, baseline_share=base, contribution=contribution)
    judgment = judgment_fingerprint(config)
    previous = [r for r in store.rows(RUNS, dataset) if int(r.get("version") or 0) == binning.version]
    last = max(previous, key=lambda r: str(r.get("finished_at") or ""), default=None)
    rewrite_all = last is None or last.get("judgment_fingerprint") != judgment
    emitted = set(window) if rewrite_all else set(todo) & set(window)
    results = [r for r in computed.results if r["period"] in emitted]
    alert_rows = alerts(
        computed.results, config, dataset=dataset, version=binning.version, run_id=run_id, updated_at=started
    )
    alert_rows += _superseded(store.rows(ALERTS, dataset), alert_rows, run_id, started)
    latest = window[-1]
    counts = counts_in(computed.results, latest)
    open_rows = [r for r in alert_rows if r["state"] == OPEN]
    critical = sum(1 for r in open_rows if r["severity"] == CRITICAL)
    reasons = _gate(config, counts[RED], counts[YELLOW], len(open_rows), critical)
    status = FAILED if reasons else WARNED if counts[RED] + counts[YELLOW] + len(open_rows) else PASSED
    message = f"{dataset} {latest}: {counts[RED]} red, {counts[YELLOW]} yellow, {len(open_rows)} open alert(s)" + (
        f"; gate failed on {'; '.join(reasons)}" if reasons else ""
    )
    outcome = Outcome(
        dataset, status, binning.version, latest, len(todo), counts[RED], counts[YELLOW], len(open_rows), critical,
        message, config.output,
    )  # fmt: skip
    run_row = {
        "run_id": run_id, "dataset": dataset, "task_id": task_id, "physical": physical, "version": binning.version,
        "latest_period": latest, "periods_profiled": len(todo), "status": status,
        "gate": ",".join(config.gate.fail_on), "red": counts[RED], "yellow": counts[YELLOW],
        "open_alerts": len(open_rows), "critical_alerts": critical, "message": message,
        "judgment_fingerprint": judgment, "started_at": started, "finished_at": _now(),
    }  # fmt: skip
    tables = {BINNINGS: binning_rows, PROFILES: fresh, RESULTS: results, ALERTS: alert_rows, RUNS: [run_row]}
    return CheckResult(tables, outcome, skipped, binning)


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
    "check",
    "error_outcome",
    "fit_range",
    "judgment_fingerprint",
]
