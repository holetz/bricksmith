"""Stable read-model shapes for the delivery applicability and inventory stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from quenching.common.front import Finding


@dataclass(frozen=True)
class Applicability:
    """The signal that decides whether a target has a delivery surface to inspect."""

    root: str
    state: str
    signal: str | None
    provider: str | None = None
    artifacts: tuple[str, ...] = ()
    config_error: dict[str, Any] = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "signal": self.signal,
            "provider": self.provider,
            "artifacts": list(self.artifacts),
        }


@dataclass(frozen=True)
class Job:
    """Static evidence collected for one workflow job."""

    name: str
    needs: tuple[str, ...] = ()
    commands: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    checkout: bool = False
    setup: bool = False
    runtimes: tuple[str, ...] = ()
    stage: str | None = None
    release: bool = False
    environment: str | None = None
    permissions: tuple[str, ...] = ()
    active: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "needs": list(self.needs),
            "commands": list(self.commands),
            "actions": list(self.actions),
            "checkout": self.checkout,
            "setup": self.setup,
            "runtimes": list(self.runtimes),
            "stage": self.stage,
            "release": self.release,
            "environment": self.environment,
            "permissions": list(self.permissions),
            "active": self.active,
        }


@dataclass(frozen=True)
class Workflow:
    """One provider or provider-equivalent workflow artifact."""

    path: str
    provider: str
    kind: str
    parse: str
    triggers: tuple[str, ...] = ()
    jobs: tuple[str, ...] = ()
    stages: tuple[str, ...] = ()
    job_details: tuple[Job, ...] = ()
    permissions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "provider": self.provider,
            "kind": self.kind,
            "parse": self.parse,
            "triggers": list(self.triggers),
            "jobs": list(self.jobs),
            "stages": list(self.stages),
            "jobDetails": [job.as_dict() for job in self.job_details],
            "permissions": list(self.permissions),
        }


@dataclass(frozen=True)
class DeliveryInventory:
    """The single workflow read model consumed by later delivery checks."""

    repo_root: str
    applicability: Applicability
    workflows: tuple[Workflow, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "repoRoot": self.repo_root,
            "applicability": self.applicability.as_dict(),
            "workflows": [workflow.as_dict() for workflow in self.workflows],
        }
