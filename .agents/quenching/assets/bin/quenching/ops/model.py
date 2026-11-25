"""The read-model shapes shared by the ops inventory, checks, and reports.

The inventory is deliberately a value model rather than a collection of scanner-specific
dictionaries.  One traversal produces these shapes and every later operation consumes the same
rows.  Paths inside nested payloads are relative to the declared operations root; the inventory's
own ``root`` is the one absolute value carried by the envelope.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from quenching.common.front import Finding


def _relative(path: str) -> str:
    """Use the payload's stable separator on every platform."""
    return path.replace(os.sep, "/")


def _require_relative(path: str, field_name: str) -> str:
    value = _relative(path)
    if os.path.isabs(value):
        raise ValueError(f"{field_name} must be relative to the declared operations root")
    return value


@dataclass(frozen=True)
class EntryPoint:
    """One operation found below the declared operations root."""

    path: str
    module: str | None
    invocation: str
    purpose: str
    flags: tuple[str, ...] = ()
    writes_outside_repo: bool = False
    lifecycle: str | None = None
    language: str = "python"

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_relative(self.path, "entry-point path"))
        object.__setattr__(self, "flags", tuple(sorted(set(self.flags))))

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "module": self.module,
            "invocation": self.invocation,
            "purpose": self.purpose,
            "flags": list(self.flags),
            "writesOutsideRepo": self.writes_outside_repo,
            "lifecycle": self.lifecycle,
            "language": self.language,
        }

@dataclass(frozen=True)
class Router:
    """The one configured canonical router and the operations it exposes."""

    path: str
    kind: str | None
    exists: bool
    entries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_relative(self.path, "router path"))
        object.__setattr__(self, "entries", tuple(sorted(set(self.entries))))

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "exists": self.exists,
            "entries": list(self.entries),
        }


@dataclass(frozen=True)
class Inventory:
    """The single read model shared by inventory, doctor, and status."""

    root: str
    router: Router
    entry_points: tuple[EntryPoint, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entry_points", tuple(
            sorted(self.entry_points, key=lambda entry: entry.path)
        ))

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "router": self.router.as_dict(),
            "entryPoints": [entry.as_dict() for entry in self.entry_points],
        }
