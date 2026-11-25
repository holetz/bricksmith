"""Assemble the ops checks and expose the three-step doctor contract."""
from __future__ import annotations

from quenching.common.front import doctor as front_doctor, inspect as front_inspect
from quenching.ops.checks import CHECK_REGISTRY
from quenching.ops.inventory import build_inventory


def inspect_ops(root: str) -> tuple[dict | None, dict]:
    """Return the inventory envelope with its findings, or a refusal payload."""
    return front_inspect(root, build_inventory, CHECK_REGISTRY,
                         enrich=lambda inventory: {"scanned": len(inventory.entry_points)})


def doctor(root: str) -> tuple[dict | None, dict, int]:
    """Run the full read-only doctor and return ``(payload, refusal, exit_code)``.

    Unlike the generic report helper, this contract treats warnings as findings too: the ops
    probe is the align's gate, so a non-empty report must return 1 even while a later policy may
    downgrade a heuristic's severity for display.
    """
    return front_doctor(root, build_inventory, CHECK_REGISTRY,
                        enrich=lambda inventory: {"scanned": len(inventory.entry_points)})
