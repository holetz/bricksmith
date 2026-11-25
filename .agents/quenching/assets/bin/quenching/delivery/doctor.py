"""Assemble the delivery applicability and inventory result."""
from __future__ import annotations

from quenching.common.front import doctor_result, findings_envelope
from quenching.delivery.checks import run_checks
from quenching.delivery.inventory import build_inventory
from quenching.delivery.probe import probe_delivery


def inspect_delivery(root: str) -> tuple[dict | None, dict]:
    """Return the delivery read model or a refusal payload."""
    applicability = probe_delivery(root)
    if applicability.state == "refused":
        return None, applicability.config_error
    inventory, error = build_inventory(root)
    if error:
        return None, error
    findings = [finding.as_dict() for finding in run_checks(inventory)] if inventory else []
    payload = {
        "root": applicability.root,
        "applicability": applicability.as_dict(),
        "inventory": inventory.as_dict() if inventory is not None else None,
        **findings_envelope(findings),
    }
    return payload, {}


def doctor(root: str) -> tuple[dict | None, dict, int]:
    """Run the read-only delivery doctor; absent delivery is a valid result."""
    payload, error = inspect_delivery(root)
    if error:
        return None, error, 2
    assert payload is not None
    return doctor_result(payload)
