"""Structured run and task events: JSON lines through an injectable sink, with error classification."""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import IO, TYPE_CHECKING, Any, Protocol

from bricksmith.errors import BricksmithError
from bricksmith.ops.redact import redact_text, redact_value

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

EVENT_VERSION = 1
TASK_FAILURE = "task_failure"
MISSING_INPUT = "missing_input"
CONFIGURATION = "configuration"
PLATFORM = "platform"
STATISTICAL_ALERT = "statistical_alert"
ERROR_CLASSES = (TASK_FAILURE, MISSING_INPUT, CONFIGURATION, PLATFORM, STATISTICAL_ALERT)

RUN_STARTED = "run_started"
RUN_FINISHED = "run_finished"
TASK_STARTED = "task_started"
TASK_RETRY = "task_retry"
TASK_FINISHED = "task_finished"

_STATISTICAL = frozenset({"statistical_alert", "quality_check_failed", "write_check_failed"})
_MISSING = frozenset({"empty_input", "io_not_found", "missing_input"})
_CONFIG = frozenset(
    {
        "config_error", "auth_conflict", "missing_extra", "io_secret", "io_path", "io_limit_exceeded",
        "write_spec_error",
        "schema_spec_error", "quality_invalid", "registry_format_error", "plan_error", "stale_plan",
        "authorization_error", "validation_error", "unsupported", "budget_exceeded", "ops_config_invalid",
    }
)  # fmt: skip
_PLATFORM = frozenset({"io_transient", "io_permanent", "io_transfer_failed"})
_PLATFORM_TYPES = (
    "DatabricksError", "PermissionDenied", "TemporarilyUnavailable", "ResourceExhausted", "DeadlineExceeded",
    "TimeoutError", "ConnectionError", "SparkConnectGrpcException", "InternalError", "BadGateway", "ServiceUnavailable",
)  # fmt: skip
_MISSING_TYPES = ("FileNotFoundError", "NotFound", "TableNotFound", "NoSuchTableException")
_MISSING_MARKERS = ("TABLE_OR_VIEW_NOT_FOUND", "table or view not found", "does not exist", "no such table")
_PLATFORM_MARKERS = ("PERMISSION_DENIED", "UNAVAILABLE", "timed out", "connection reset", "RESOURCE_EXHAUSTED")


def _describe(error: BaseException | Mapping[str, str]) -> tuple[str, frozenset[str], str]:
    if isinstance(error, BaseException):
        code = error.code if isinstance(error, BricksmithError) else type(error).__name__
        return code, frozenset(c.__name__ for c in type(error).__mro__), str(error)
    return error.get("code", ""), frozenset({error.get("type", "")}), error.get("message", "")


def classify_error(error: BaseException | Mapping[str, str]) -> str:  # noqa: PLR0911 - one rule per class
    """Return one of :data:`ERROR_CLASSES`; an explicit ``error_class`` attribute or ``class`` key wins."""
    explicit = getattr(error, "error_class", None) if isinstance(error, BaseException) else error.get("class")
    if explicit in ERROR_CLASSES:
        return str(explicit)
    code, names, message = _describe(error)
    if code in _STATISTICAL:
        return STATISTICAL_ALERT
    if code in _MISSING or names & set(_MISSING_TYPES):
        return MISSING_INPUT
    if code in _PLATFORM or names & set(_PLATFORM_TYPES) or isinstance(error, (TimeoutError, ConnectionError)):
        return PLATFORM
    if code in _CONFIG or names & {"ConfigError", "ValidationError"}:
        return CONFIGURATION
    if any(marker in message for marker in _MISSING_MARKERS):
        return MISSING_INPUT
    if any(marker in message for marker in _PLATFORM_MARKERS):
        return PLATFORM
    return TASK_FAILURE


def describe_error(error: BaseException | Mapping[str, str], secrets: Iterable[str] = ()) -> dict[str, str]:
    """Return the redacted ``code``/``type``/``message``/``class`` record carried by events and reports."""
    code, _, message = _describe(error)
    kind = type(error).__name__ if isinstance(error, BaseException) else error.get("type", "")
    return {
        "class": classify_error(error),
        "code": code,
        "message": redact_text(message, secrets),
        "type": kind,
    }


@dataclass(frozen=True)
class Event:
    """One structured event; ``fields`` carry redacted extras."""

    kind: str
    run_id: str
    plan_fingerprint: str
    environment: str = ""
    time: str = ""
    task_id: str = ""
    targets: tuple[str, ...] = ()
    attempt: int = 0
    seconds: float | None = None
    state: str = ""
    error: Mapping[str, str] | None = None
    fields: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form (empty optional parts are omitted)."""
        data: dict[str, Any] = {
            "attempt": self.attempt,
            "environment": self.environment,
            "kind": self.kind,
            "plan_fingerprint": self.plan_fingerprint,
            "run_id": self.run_id,
            "time": self.time,
            "version": EVENT_VERSION,
        }
        optional: dict[str, Any] = {
            "error": dict(self.error) if self.error else None,
            "fields": dict(self.fields) or None,
            "seconds": self.seconds,
            "state": self.state or None,
            "targets": list(self.targets) or None,
            "task_id": self.task_id or None,
        }
        data.update({k: v for k, v in optional.items() if v is not None})
        return data

    def to_json_line(self) -> str:
        """Return one sorted JSON line."""
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


class EventSink(Protocol):
    """Receives events; implementations must be thread safe."""

    def emit(self, event: Event) -> None:
        """Deliver one event."""
        ...


class JsonLinesSink:
    """Write one JSON line per event to a text stream."""

    def __init__(self, stream: IO[str]) -> None:
        """Keep the stream."""
        self._stream = stream
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        """Write and flush one line."""
        with self._lock:
            self._stream.write(event.to_json_line() + "\n")
            self._stream.flush()


class LoggingSink:
    """Send events to a stdlib logger; the JSON line is the message and the dict is ``extra['event']``."""

    def __init__(self, logger: logging.Logger | None = None, level: int = logging.INFO) -> None:
        """Use ``logger`` (default ``bricksmith.ops``); failures log at ``ERROR`` for the failed state."""
        self._logger = logger or logging.getLogger("bricksmith.ops")
        self._level = level

    def emit(self, event: Event) -> None:
        """Log the event."""
        level = logging.ERROR if event.state == "failed" else self._level
        self._logger.log(level, "%s", event.to_json_line(), extra={"event": event.to_dict()})


class ListSink:
    """Collect events in memory (tests and in-process consumers)."""

    def __init__(self) -> None:
        """Start empty."""
        self.events: list[Event] = []
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        """Keep the event."""
        with self._lock:
            self.events.append(event)


class RunEvents:
    """Builds and emits the events of one run; a failing sink never affects the run."""

    def __init__(  # noqa: PLR0913 - one keyword per run-identity part
        self,
        sink: EventSink,
        plan_fingerprint: str,
        *,
        environment: str = "",
        run_id: str | None = None,
        secrets: Iterable[str] = (),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Bind the sink and the run identity (``run_id`` defaults to a random hex id)."""
        self.sink = sink
        self.plan_fingerprint = plan_fingerprint
        self.environment = environment
        self.run_id = run_id or uuid.uuid4().hex
        self.secrets = tuple(secrets)
        self._clock = clock or (lambda: datetime.now(UTC))
        self.sink_failures = 0

    def emit(self, kind: str, **values: Any) -> None:  # noqa: ANN401 - Event keyword values
        """Emit one event; extras go to redacted ``fields``. Sink errors are counted, never raised."""
        extras = values.pop("fields", {})
        error = values.pop("error", None)
        event = Event(
            kind=kind,
            run_id=self.run_id,
            plan_fingerprint=self.plan_fingerprint,
            environment=self.environment,
            time=self._clock().isoformat(timespec="milliseconds"),
            error=None if error is None else describe_error(error, self.secrets),
            fields=redact_value(extras, self.secrets),
            **values,
        )
        try:
            self.sink.emit(event)
        except Exception:  # noqa: BLE001 - observability must not fail the run
            self.sink_failures += 1


__all__ = [
    "CONFIGURATION", "ERROR_CLASSES", "MISSING_INPUT", "PLATFORM", "STATISTICAL_ALERT", "TASK_FAILURE", "Event",
    "EventSink", "JsonLinesSink", "ListSink", "LoggingSink", "RunEvents", "classify_error", "describe_error",
]  # fmt: skip
