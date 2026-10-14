"""Coverage, drift and check findings built from an audit; the data structure catalog docs can render."""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, Any

from bricksmith.governance.config import UNCLASSIFIED
from bricksmith.governance.model import (
    APPLIED,
    DESIRED,
    MASK,
    NO_MUTATION_ADAPTER,
    ROW_FILTER,
    STATES,
    TAG,
    UNAVAILABLE,
)
from bricksmith.registry.checks import CheckReport, Finding

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.governance.planner import ControlAudit, GovernanceAudit

DEFAULT_SEVERITY: dict[str, str] = {
    "governance.unclassified": "warning",
    "governance.drift": "error",
    "governance.unavailable": "warning",
    "governance.external": "warning",
    "governance.metadata_only": "off",
}
MASKING_NOTE = (
    "Tags and grants are metadata and access control; they are not column masking or row filtering. "
    f"Masks and row filters: {NO_MUTATION_ADAPTER}."
)


def _by_resource(audit: GovernanceAudit) -> dict[str, list[ControlAudit]]:
    grouped: dict[str, list[ControlAudit]] = {r.name: [] for r in audit.desired.resources}
    for row in audit.controls:
        grouped[row.control.resource].append(row)
    return grouped


def governance_report(audit: GovernanceAudit) -> dict[str, Any]:
    """Coverage and drift as plain data: one entry per resource, plus totals. Stable and sorted."""
    grouped = _by_resource(audit)
    outside = {e.resource for e in audit.external}
    resources: list[dict[str, Any]] = []
    for resource in sorted(audit.desired.resources, key=lambda r: r.name):
        rows = grouped[resource.name]
        live = audit.live.get(resource.name)
        detected = bool(live and (live.masks or live.row_filters))
        resources.append(
            {
                "name": resource.name,
                "kind": resource.kind,
                "managed": resource.managed,
                "classification": resource.classification,
                "owner": resource.owner,
                "retention": resource.retention,
                "controls": [r.to_dict() for r in rows],
                "states": {s: sum(1 for r in rows if r.state == s) for s in STATES},
                "external": [e.to_dict() for e in audit.external if e.resource == resource.name],
                "enforcement": {
                    "classification": "metadata-only" if resource.classification != UNCLASSIFIED else "none",
                    "masking": "mask or row filter detected live (read-only; effectiveness not verified)"
                    if detected
                    else "none",
                    "effective_masking": False,
                },
                "has_external": resource.name in outside,
            }
        )
    managed = [r for r in resources if r["managed"]]
    totals = {s: sum(r["states"][s] for r in resources) for s in STATES}
    classes: dict[str, int] = {}
    for r in managed:
        classes[r["classification"]] = classes.get(r["classification"], 0) + 1
    return {
        "managed": len(managed),
        "unmanaged": len(resources) - len(managed),
        "classification": dict(sorted(classes.items())),
        "unclassified": sorted(r["name"] for r in managed if r["classification"] == UNCLASSIFIED),
        "states": totals,
        "external": len(audit.external),
        "note": MASKING_NOTE,
        "resources": resources,
    }


def _severity(rule: str, config: Mapping[str, str]) -> str:
    """Most specific matching project pattern wins; project patterns take precedence over defaults."""
    for table in (config, DEFAULT_SEVERITY):
        hits = [p for p in table if fnmatch.fnmatchcase(rule, p) or rule.startswith(p + ".")]
        if hits:
            return table[max(hits, key=len)]
    return "error"


def governance_findings(audit: GovernanceAudit, severity: Mapping[str, str] | None = None) -> CheckReport:
    """Turn coverage and drift into registry-style findings (``<rule>:<subject>``) with configurable severity."""
    raw: list[tuple[str, str, str, str]] = []  # rule, subject, message, location
    for resource in audit.desired.managed():
        if resource.classification == UNCLASSIFIED:
            raw.append(("governance.unclassified", resource.name, "managed resource is unclassified", resource.name))
        else:
            note = f"classified as {resource.classification}; metadata only, not masking"
            raw.append(("governance.metadata_only", resource.name, note, resource.name))
    for row in audit.controls:
        control = row.control
        if row.state == DESIRED and row.live is not None:
            raw.append(
                ("governance.drift.different", control.id, f"{control.key} is {row.live!r} live", control.resource)
            )
        elif row.state == DESIRED:
            raw.append(
                ("governance.drift.missing", control.id, f"{control.kind} {control.key} not applied", control.resource)
            )
        elif row.state == APPLIED:
            raw.append(("governance.drift.unverified", control.id, row.reason, control.resource))
        elif row.state == UNAVAILABLE:
            raw.append(("governance.unavailable", control.id, row.reason, control.resource))
    for ext in audit.external:
        rule = "governance.external." + ("policy" if ext.kind in (MASK, ROW_FILTER) else ext.kind)
        label = "tag" if ext.kind == TAG else ext.kind
        raw.append((rule, ext.id, f"unexpected external {label} {ext.key} (kept, not deleted)", ext.resource))
    levels = severity or {}
    findings = [
        Finding(f"{rule}:{subject}", rule, level, message, location)
        for rule, subject, message, location in raw
        if (level := _severity(rule, levels)) != "off"
    ]
    return CheckReport(tuple(sorted(findings, key=lambda f: f.id)), ())
