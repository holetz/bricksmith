"""Stable read-model shapes for the toolchain inventory."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from quenching.common.front import Finding


@dataclass(frozen=True)
class Artifact:
    """One recognized toolchain artifact and the declarations read from it."""

    path: str
    kind: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "kind": self.kind, **self.details}


@dataclass(frozen=True)
class ToolchainInventory:
    """The one static inventory consumed by later toolchain checks."""

    repo_root: str
    manifests: tuple[Artifact, ...] = ()
    locks: tuple[Artifact, ...] = ()
    language_pins: tuple[Artifact, ...] = ()
    tool_configurations: tuple[Artifact, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "repoRoot": self.repo_root,
            "manifests": [item.as_dict() for item in self.manifests],
            "locks": [item.as_dict() for item in self.locks],
            "languagePins": [item.as_dict() for item in self.language_pins],
            "toolConfigurations": [item.as_dict() for item in self.tool_configurations],
        }
