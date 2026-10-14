"""Pure planning and auditing: desired controls against live state. No I/O, no Spark."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.governance.model import (
    APPLIED,
    COMMENT,
    DESIRED,
    GRANT,
    MASK,
    NO_MUTATION_ADAPTER,
    PROPERTY,
    ROW_FILTER,
    TAG,
    UNAVAILABLE,
    VERIFIED,
    Control,
    DesiredState,
    LiveResource,
)
from bricksmith.governance.statements import render

if TYPE_CHECKING:
    from collections.abc import Mapping

_SECTION = {TAG: "tags", GRANT: "grants", COMMENT: "comment", PROPERTY: "properties", MASK: "masks",
            ROW_FILTER: "row_filters"}  # fmt: skip
_ORDER = {TAG: 0, GRANT: 1, COMMENT: 2, PROPERTY: 3}
_SYSTEM_TAG_PREFIX = "system."


@dataclass(frozen=True)
class ControlDiff:
    """Desired control against live state: ``ok``, ``missing``, ``different`` or ``unavailable``."""

    control: Control
    status: str
    live: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class External:
    """A live control nobody declared: reported, never changed or deleted."""

    resource: str
    kind: str  # tag | grant | mask | row_filter
    key: str
    value: str = ""

    @property
    def id(self) -> str:
        """Stable identifier."""
        return f"{self.kind}:{self.resource}:{self.key}"

    def to_dict(self) -> dict[str, str]:
        """Return the JSON form."""
        return {"id": self.id, "kind": self.kind, "resource": self.resource, "key": self.key, "value": self.value}


def _live_value(control: Control, live: LiveResource) -> str | None:
    if control.kind == TAG:
        return live.tags.get(control.key)
    if control.kind == COMMENT:
        return live.comment
    if control.kind == PROPERTY:
        return live.properties.get(control.key)
    principal, _, privilege = control.key.rpartition(":")
    return privilege if (principal, privilege) in live.grants else None


def _diff_one(control: Control, live: LiveResource | None) -> ControlDiff:
    if control.kind in (MASK, ROW_FILTER):
        seen = live is not None and bool(live.masks if control.kind == MASK else live.row_filters)
        note = "; a live control exists (read-only, not managed here)" if seen else ""
        return ControlDiff(control, UNAVAILABLE, None, NO_MUTATION_ADAPTER + note)
    if live is None or not live.exists:
        return ControlDiff(control, UNAVAILABLE, None, "resource not found on the platform")
    blocked = live.unreadable.get(_SECTION[control.kind])
    if blocked is not None:
        return ControlDiff(control, UNAVAILABLE, None, f"live state unreadable: {blocked}")
    current = _live_value(control, live)
    if current is None or (control.kind == COMMENT and current == "" and control.value != ""):
        return ControlDiff(control, "missing")
    if control.kind != GRANT and current != control.value:
        return ControlDiff(control, "different", current)
    return ControlDiff(control, "ok", current)


def _externals(desired: DesiredState, live: Mapping[str, LiveResource]) -> tuple[External, ...]:
    wanted = {c.id for c in desired.controls}
    found: list[External] = []
    for resource in desired.managed():
        state = live.get(resource.name)
        if state is None or not state.exists:
            continue
        found += [
            External(resource.name, TAG, k, v)
            for k, v in state.tags.items()
            if f"{TAG}:{resource.name}:{k}" not in wanted and not k.startswith(_SYSTEM_TAG_PREFIX)
        ]
        found += [
            External(resource.name, GRANT, f"{p}:{a}")
            for p, a in state.grants
            if f"{GRANT}:{resource.name}:{p}:{a}" not in wanted
        ]
        found += [External(resource.name, MASK, m) for m in state.masks if f"{MASK}:{resource.name}:{m}" not in wanted]
        found += [External(resource.name, ROW_FILTER, f) for f in state.row_filters]
    return tuple(sorted(found, key=lambda e: e.id))


@dataclass(frozen=True)
class Comparison:
    """Per-control diffs and external controls."""

    diffs: tuple[ControlDiff, ...]
    external: tuple[External, ...]


def compare(desired: DesiredState, live: Mapping[str, LiveResource]) -> Comparison:
    """Compare every desired control with the live state of its resource."""
    diffs = tuple(_diff_one(c, live.get(c.resource)) for c in desired.controls)
    return Comparison(diffs, _externals(desired, live))


@dataclass(frozen=True)
class Operation:
    """One statement to run; ``control_id`` links it back to the desired control."""

    control_id: str
    resource: str
    sql: str


@dataclass(frozen=True)
class GovernancePlan:
    """Statements that bring managed resources to the desired state, plus what is only reported."""

    operations: tuple[Operation, ...]
    unavailable: tuple[tuple[str, str], ...]
    external: tuple[External, ...]

    @property
    def is_noop(self) -> bool:
        """True when nothing would run."""
        return not self.operations

    def to_dict(self) -> dict[str, Any]:
        """Stable JSON form."""
        return {
            "noop": self.is_noop,
            "operations": [{"control": o.control_id, "resource": o.resource, "sql": o.sql} for o in self.operations],
            "unavailable": [{"control": c, "reason": r} for c, r in self.unavailable],
            "external": [e.to_dict() for e in self.external],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GovernancePlan:
        """Rebuild a plan from its JSON form."""
        return cls(
            tuple(Operation(o["control"], o["resource"], o["sql"]) for o in data["operations"]),
            tuple((u["control"], u["reason"]) for u in data["unavailable"]),
            tuple(External(e["resource"], e["kind"], e["key"], e.get("value", "")) for e in data["external"]),
        )

    def to_json(self) -> str:
        """Serialise with sorted keys."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_text(self) -> str:
        """Human-readable summary."""
        lines = [f"run  {o.sql}" for o in self.operations] or ["no changes"]
        lines += [f"unavailable  {c}: {r}" for c, r in self.unavailable]
        lines += [f"external (kept)  {e.id} {e.value}".rstrip() for e in self.external]
        return "\n".join(lines)


def plan_governance(desired: DesiredState, live: Mapping[str, LiveResource]) -> GovernancePlan:
    """Plan statements for missing or different managed controls; externals are only reported."""
    kinds = {r.name: r.kind for r in desired.resources}
    comparison = compare(desired, live)
    todo = [d for d in comparison.diffs if d.status in ("missing", "different")]
    todo.sort(key=lambda d: (d.control.resource, _ORDER[d.control.kind], d.control.key))
    operations = tuple(Operation(d.control.id, d.control.resource, render(d.control, kinds[d.control.resource]))
                       for d in todo)  # fmt: skip
    unavailable = tuple((d.control.id, d.reason) for d in comparison.diffs if d.status == UNAVAILABLE)
    return GovernancePlan(operations, unavailable, comparison.external)


@dataclass(frozen=True)
class ControlAudit:
    """One control with exactly one of the four states."""

    control: Control
    state: str
    reason: str = ""
    live: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {**self.control.to_dict(), "state": self.state, "reason": self.reason, "live": self.live}


@dataclass(frozen=True)
class GovernanceAudit:
    """Per-control states, external controls, and the resources they belong to."""

    desired: DesiredState
    controls: tuple[ControlAudit, ...]
    external: tuple[External, ...]
    live: Mapping[str, LiveResource]


def audit_governance(
    desired: DesiredState,
    live: Mapping[str, LiveResource],
    *,
    applied: frozenset[str] = frozenset(),
    refused: Mapping[str, str] | None = None,
) -> GovernanceAudit:
    """Return the state of every control.

    ``verified`` only when live state matches; ``applied`` when a statement
    succeeded but live state does not show it; ``unavailable`` when it cannot be applied or read;
    otherwise ``desired``.
    """
    refusals = refused or {}
    comparison = compare(desired, live)
    rows: list[ControlAudit] = []
    for diff in comparison.diffs:
        cid = diff.control.id
        if diff.status == "ok":
            rows.append(ControlAudit(diff.control, VERIFIED, "", diff.live))
        elif diff.status == UNAVAILABLE:
            rows.append(ControlAudit(diff.control, UNAVAILABLE, diff.reason))
        elif cid in refusals:
            rows.append(ControlAudit(diff.control, UNAVAILABLE, f"platform refused: {refusals[cid]}", diff.live))
        elif cid in applied:
            rows.append(ControlAudit(diff.control, APPLIED, "applied but not visible in live state", diff.live))
        else:
            rows.append(ControlAudit(diff.control, DESIRED, "", diff.live))
    return GovernanceAudit(desired, tuple(rows), comparison.external, dict(live))
