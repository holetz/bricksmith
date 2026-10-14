"""Pure governance data model: desired controls, live state and audit states."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TAG = "tag"
GRANT = "grant"
COMMENT = "comment"
PROPERTY = "property"
MASK = "mask"
ROW_FILTER = "row_filter"
CONTROL_KINDS = (TAG, GRANT, COMMENT, PROPERTY, MASK, ROW_FILTER)
RESOURCE_KINDS = ("table", "view", "schema", "catalog")

DESIRED = "desired"
APPLIED = "applied"
VERIFIED = "verified"
UNAVAILABLE = "unavailable"
STATES = (DESIRED, APPLIED, VERIFIED, UNAVAILABLE)

NO_MUTATION_ADAPTER = "no mutation adapter for column masks and row filters in this release"


@dataclass(frozen=True)
class Control:
    """One desired control on one resource. ``key`` is the tag/property name or ``principal:PRIVILEGE``."""

    resource: str
    kind: str
    key: str
    value: str = ""
    source: str = "declared"

    @property
    def id(self) -> str:
        """Stable identifier, independent of values."""
        return f"{self.kind}:{self.resource}:{self.key}"

    def to_dict(self) -> dict[str, str]:
        """Return the JSON form."""
        return {"id": self.id, "kind": self.kind, "resource": self.resource, "key": self.key, "value": self.value,
                "source": self.source}  # fmt: skip


@dataclass(frozen=True)
class ResolvedResource:
    """A declared resource after environment resolution."""

    name: str
    kind: str
    managed: bool
    classification: str  # a category or "unclassified"
    owner: str = ""
    retention: str = ""


@dataclass(frozen=True)
class DesiredState:
    """Resolved resources and the controls desired on the managed ones."""

    resources: tuple[ResolvedResource, ...]
    controls: tuple[Control, ...]

    def managed(self) -> tuple[ResolvedResource, ...]:
        """Resources governed by bricksmith."""
        return tuple(r for r in self.resources if r.managed)


@dataclass(frozen=True)
class LiveResource:
    """What the platform reports for one resource; ``unreadable`` maps a section to a redacted reason."""

    exists: bool = True
    tags: dict[str, str] = field(default_factory=dict)
    grants: frozenset[tuple[str, str]] = frozenset()
    comment: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    masks: tuple[str, ...] = ()
    row_filters: tuple[str, ...] = ()
    unreadable: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {
            "exists": self.exists,
            "tags": dict(sorted(self.tags.items())),
            "grants": sorted([p, a] for p, a in self.grants),
            "comment": self.comment,
            "properties": dict(sorted(self.properties.items())),
            "masks": list(self.masks),
            "row_filters": list(self.row_filters),
            "unreadable": dict(sorted(self.unreadable.items())),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LiveResource:
        """Rebuild from the JSON form."""
        return cls(
            exists=bool(data.get("exists", True)),
            tags=dict(data.get("tags", {})),
            grants=frozenset((str(p), str(a)) for p, a in data.get("grants", [])),
            comment=data.get("comment"),
            properties=dict(data.get("properties", {})),
            masks=tuple(data.get("masks", ())),
            row_filters=tuple(data.get("row_filters", ())),
            unreadable=dict(data.get("unreadable", {})),
        )
