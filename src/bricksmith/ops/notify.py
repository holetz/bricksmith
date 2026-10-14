"""Opt-in alert delivery: a generic alert event, adapters, deduplication, bounded payloads and retries."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from bricksmith.ops.events import classify_error, describe_error
from bricksmith.ops.redact import redact_text, redact_value
from bricksmith.runner.report import FAILED

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from bricksmith.runner.report import RunReport

MIN_PAYLOAD_BYTES = 256
DELIVERED = "delivered"
SUPPRESSED = "suppressed"
DELIVERY_FAILED = "failed"
SEVERITIES = ("info", "warning", "critical")


@dataclass(frozen=True)
class AlertEvent:
    """The generic alert BS-13 monitoring (and run reports) emit: what happened, to what, how bad.

    ``dedup_key`` is stable across runs and occurrences: it hashes ``kind``, ``error_class``, ``subject``
    and ``target`` (never the time, run id or message), unless ``key`` is set explicitly.
    """

    kind: str
    subject: str
    message: str
    severity: str = "warning"
    error_class: str = ""
    target: str = ""
    run_id: str = ""
    plan_fingerprint: str = ""
    occurred_at: str = ""
    key: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)

    @property
    def dedup_key(self) -> str:
        """Return the stable deduplication key."""
        if self.key:
            return self.key
        raw = f"{self.kind}\x1f{self.error_class}\x1f{self.subject}\x1f{self.target}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "dedup_key": self.dedup_key,
            "details": dict(self.details),
            "error_class": self.error_class,
            "kind": self.kind,
            "message": self.message,
            "occurred_at": self.occurred_at,
            "plan_fingerprint": self.plan_fingerprint,
            "run_id": self.run_id,
            "severity": self.severity,
            "subject": self.subject,
            "target": self.target,
        }


def bound_payload(alert: AlertEvent, max_bytes: int, secrets: Sequence[str] = ()) -> dict[str, Any]:
    """Return a redacted payload whose JSON encoding is at most ``max_bytes``; shrinks details, then the message."""
    payload = redact_value(alert.to_dict(), secrets)
    payload["dedup_key"] = alert.dedup_key

    def size() -> int:
        return len(json.dumps(payload, sort_keys=True).encode())

    if size() <= max_bytes:
        return payload
    payload["truncated"] = True
    payload["details"] = {"omitted_keys": sorted(payload["details"])[:10]}
    for field_name in ("message", "subject", "target"):
        while size() > max_bytes and payload[field_name]:
            over = size() - max_bytes
            payload[field_name] = redact_text(payload[field_name], limit=max(len(payload[field_name]) - over - 1, 0))
    for droppable in ("details", "plan_fingerprint", "run_id", "occurred_at"):
        if size() > max_bytes:
            payload.pop(droppable, None)
    return payload


class NotificationAdapter(Protocol):
    """Delivers one bounded payload; raise any exception to signal a failed delivery."""

    name: str

    def deliver(self, payload: Mapping[str, Any]) -> None:
        """Send ``payload``."""
        ...


class AlertStore(Protocol):
    """Remembers when a deduplication key was last delivered."""

    def last_delivered(self, key: str) -> float | None:
        """Return the epoch seconds of the last delivery, or ``None``."""
        ...

    def record(self, key: str, at: float) -> None:
        """Remember a delivery."""
        ...


class InMemoryAlertStore:
    """Process-local store (tests and single-run use)."""

    def __init__(self) -> None:
        """Start empty."""
        self._seen: dict[str, float] = {}

    def last_delivered(self, key: str) -> float | None:
        """Return the last delivery time."""
        return self._seen.get(key)

    def record(self, key: str, at: float) -> None:
        """Remember a delivery."""
        self._seen[key] = at


class JsonFileAlertStore:
    """JSON file store that survives across runs (for example on a volume path)."""

    def __init__(self, path: Path | str) -> None:
        """Keep the path; the file is created on first record."""
        self._path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, float]:
        try:
            return {str(k): float(v) for k, v in json.loads(self._path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            return {}

    def last_delivered(self, key: str) -> float | None:
        """Return the last delivery time."""
        return self._load().get(key)

    def record(self, key: str, at: float) -> None:
        """Remember a delivery."""
        with self._lock:
            data = self._load()
            data[key] = at
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


@dataclass(frozen=True)
class DeliveryRecord:
    """The recorded outcome of delivering one alert through one adapter."""

    dedup_key: str
    adapter: str
    status: str
    attempts: int = 0
    error: str = ""
    at: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "adapter": self.adapter, "at": self.at, "attempts": self.attempts, "dedup_key": self.dedup_key,
            "error": self.error, "status": self.status,
        }  # fmt: skip


class AlertNotifier:
    """Delivers alerts through opt-in adapters; :meth:`notify` never raises and never touches a run result.

    An alert whose key was delivered through an adapter within ``dedup_window_seconds`` is suppressed for
    that adapter. A failed delivery is retried ``max_attempts`` times, then recorded as ``failed`` (and
    not remembered, so the next occurrence tries again).
    """

    def __init__(  # noqa: PLR0913 - one keyword per documented knob
        self,
        adapters: Sequence[NotificationAdapter],
        store: AlertStore | None = None,
        *,
        dedup_window_seconds: float = 3600.0,
        max_payload_bytes: int = 4096,
        max_attempts: int = 3,
        retry_delay: float = 0.0,
        secrets: Sequence[str] = (),
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Configure delivery; ``max_payload_bytes`` must be at least 256."""
        if max_payload_bytes < MIN_PAYLOAD_BYTES or max_attempts < 1 or dedup_window_seconds < 0:
            msg = f"notifier needs max_payload_bytes >= {MIN_PAYLOAD_BYTES}, max_attempts >= 1 and a window >= 0"
            raise ValueError(msg)
        self.adapters = tuple(adapters)
        self.store = store or InMemoryAlertStore()
        self.window = dedup_window_seconds
        self.max_bytes = max_payload_bytes
        self.max_attempts = max_attempts
        self.retry_delay = retry_delay
        self.secrets = tuple(secrets)
        self._sleep = sleep
        self._clock = clock
        self.records: list[DeliveryRecord] = []

    def notify(self, alert: AlertEvent) -> tuple[DeliveryRecord, ...]:
        """Deliver ``alert`` through every adapter and return (and keep) the records."""
        out = tuple(self._one(adapter, alert) for adapter in self.adapters)
        self.records.extend(out)
        return out

    def _one(self, adapter: NotificationAdapter, alert: AlertEvent) -> DeliveryRecord:
        key = alert.dedup_key
        now = datetime.fromtimestamp(self._clock(), UTC).isoformat(timespec="seconds")
        store_key = f"{adapter.name}:{key}"
        try:
            last = self.store.last_delivered(store_key)
            if last is not None and self._clock() - last < self.window:
                return DeliveryRecord(key, adapter.name, SUPPRESSED, 0, "", now)
            payload = bound_payload(alert, self.max_bytes, self.secrets)
        except Exception as error:  # noqa: BLE001 - a broken store must not escape
            return DeliveryRecord(key, adapter.name, DELIVERY_FAILED, 0, self._safe(error), now)
        attempts = 0
        problem = ""
        while attempts < self.max_attempts:
            attempts += 1
            try:
                adapter.deliver(payload)
            except Exception as error:  # noqa: BLE001 - adapters are third-party code
                problem = self._safe(error)
                if attempts < self.max_attempts:
                    self._sleep(self.retry_delay * 2 ** (attempts - 1))
            else:
                try:
                    self.store.record(store_key, self._clock())
                except Exception as error:  # noqa: BLE001 - delivery succeeded; only dedup memory failed
                    return DeliveryRecord(
                        key, adapter.name, DELIVERED, attempts, "dedup store: " + self._safe(error), now
                    )
                return DeliveryRecord(key, adapter.name, DELIVERED, attempts, "", now)
        return DeliveryRecord(key, adapter.name, DELIVERY_FAILED, attempts, problem, now)

    def _safe(self, error: BaseException) -> str:
        return f"{type(error).__name__}: {redact_text(str(error), self.secrets, 200)}"


def alerts_from_report(report: RunReport, *, run_id: str = "", severity: str = "critical") -> tuple[AlertEvent, ...]:
    """Build one alert per failed task of a report; statistical alerts keep their own class and kind."""
    out = []
    for r in report.results:
        if r.state != FAILED:
            continue
        record = describe_error(r.error or {"code": "unknown", "message": r.reason})
        kind = "statistical_alert" if record["class"] == "statistical_alert" else "task_failed"
        out.append(
            AlertEvent(
                kind,
                r.task_id,
                f"{record['code']}: {record['message']}",
                severity,
                record["class"],
                run_id=run_id,
                plan_fingerprint=report.plan_fingerprint,
                details={"attempts": r.attempts, "error_type": record["type"]},
            )
        )
    return tuple(out)


def notify_report(report: RunReport, notifier: AlertNotifier, *, run_id: str = "") -> tuple[DeliveryRecord, ...]:
    """Alert on a report's failed tasks. The report is returned unchanged by design: delivery results live apart."""
    records: list[DeliveryRecord] = []
    for alert in alerts_from_report(report, run_id=run_id):
        records.extend(notifier.notify(alert))
    return tuple(records)


__all__ = [
    "AlertEvent", "AlertNotifier", "AlertStore", "DeliveryRecord", "InMemoryAlertStore", "JsonFileAlertStore",
    "NotificationAdapter", "alerts_from_report", "bound_payload", "classify_error", "notify_report",
]  # fmt: skip
