"""Run results: states per task and a serializable report."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

REPORT_VERSION = 1
SUCCEEDED = "succeeded"
FAILED = "failed"
BLOCKED = "blocked"
SKIPPED = "skipped"
NOT_RUN = "not-run"
STATES = (SUCCEEDED, FAILED, BLOCKED, SKIPPED, NOT_RUN)


@dataclass(frozen=True)
class TaskResult:
    """The outcome of one plan entry."""

    task_id: str
    state: str
    reason: str = ""
    attempts: int = 0
    error: Mapping[str, str] | None = None
    blocked_by: tuple[str, ...] = ()
    writes: tuple[tuple[str, str], ...] = ()
    seconds: float = 0.0

    def __post_init__(self) -> None:
        """Round the duration so reports compare equal after a JSON round trip."""
        object.__setattr__(self, "seconds", round(self.seconds, 3))

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "attempts": self.attempts,
            "blocked_by": list(self.blocked_by),
            "error": dict(self.error) if self.error else None,
            "reason": self.reason,
            "seconds": self.seconds,
            "state": self.state,
            "task_id": self.task_id,
            "writes": [{"object": o, "operation": p} for o, p in self.writes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> TaskResult:
        """Rebuild a result from :meth:`to_dict` output."""
        return cls(
            task_id=data["task_id"],
            state=data["state"],
            reason=data["reason"],
            attempts=int(data["attempts"]),
            error=dict(data["error"]) if data["error"] else None,
            blocked_by=tuple(data["blocked_by"]),
            writes=tuple((w["object"], w["operation"]) for w in data["writes"]),
            seconds=float(data["seconds"]),
        )


@dataclass(frozen=True)
class RunReport:
    """What a run did, in plan order, tied to the plan fingerprint it executed."""

    plan_fingerprint: str
    dry_run: bool
    concurrency: int
    results: tuple[TaskResult, ...]

    @property
    def ok(self) -> bool:
        """Tell whether no task failed or was blocked."""
        return not any(r.state in (FAILED, BLOCKED) for r in self.results)

    def ids(self, *states: str) -> tuple[str, ...]:
        """Return the task ids whose state is one of ``states``, in plan order."""
        return tuple(r.task_id for r in self.results if r.state in states)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form, with state counts."""
        return {
            "concurrency": self.concurrency,
            "counts": {s: sum(1 for r in self.results if r.state == s) for s in STATES},
            "dry_run": self.dry_run,
            "ok": self.ok,
            "plan_fingerprint": self.plan_fingerprint,
            "results": [r.to_dict() for r in self.results],
            "version": REPORT_VERSION,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunReport:
        """Rebuild a report from :meth:`to_dict` output."""
        if data.get("version") != REPORT_VERSION:
            msg = f"unsupported report version {data.get('version')!r}; expected {REPORT_VERSION}"
            raise ValueError(msg)
        return cls(
            plan_fingerprint=data["plan_fingerprint"],
            dry_run=bool(data["dry_run"]),
            concurrency=int(data["concurrency"]),
            results=tuple(TaskResult.from_dict(r) for r in data["results"]),
        )

    def to_json(self) -> str:
        """Return sorted, indented JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> RunReport:
        """Rebuild a report from :meth:`to_json` output."""
        return cls.from_dict(json.loads(text))
