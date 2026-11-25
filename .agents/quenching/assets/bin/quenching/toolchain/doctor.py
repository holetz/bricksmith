"""Assemble the toolchain applicability result."""
from __future__ import annotations

from quenching.common.front import doctor_result, findings_envelope
from quenching.toolchain.checks import run_checks
from quenching.toolchain.inventory import build_inventory
from quenching.toolchain.probe import probe_toolchain


def inspect_toolchain(root: str) -> tuple[dict | None, dict]:
    """Return the read-only applicability envelope or a refusal payload."""
    applicability = probe_toolchain(root)
    inventory = None
    if applicability.state == "applicable":
        inventory, err = build_inventory(root)
        if err:
            return None, err
    findings = [finding.as_dict() for finding in run_checks(inventory)] if inventory else []
    payload = {
        "root": applicability.root,
        "applicability": applicability.as_dict(),
        "inventory": inventory.as_dict() if inventory is not None else None,
        **findings_envelope(findings),
    }
    return payload, {}


def doctor(root: str) -> tuple[dict | None, dict, int]:
    """Run the applicability probe; not-applicable is a valid result."""
    payload, err = inspect_toolchain(root)
    if err:
        return None, err, 2
    assert payload is not None
    return doctor_result(payload)
