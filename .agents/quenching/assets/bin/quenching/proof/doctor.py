"""Assemble the proof inventory and static findings."""
from __future__ import annotations

from quenching.common.front import doctor as front_doctor, inspect as front_inspect
from quenching.proof.checks import CHECK_REGISTRY, conditional_status
from quenching.proof.inventory import build_inventory


def inspect_proof(root: str) -> tuple[dict | None, dict]:
    """Return one inventory envelope with all findings, or a refusal payload."""
    return front_inspect(root, build_inventory, CHECK_REGISTRY,
                         enrich=lambda inventory: {
                             "scanned": len(inventory.test_modules),
                             "suiteRun": False,
                             "conditional": conditional_status(inventory),
                         })


def doctor(root: str) -> tuple[dict | None, dict, int]:
    """Run static proof checks; exit 1 means findings and 2 means refusal."""
    return front_doctor(root, build_inventory, CHECK_REGISTRY,
                        enrich=lambda inventory: {
                            "scanned": len(inventory.test_modules),
                            "suiteRun": False,
                            "conditional": conditional_status(inventory),
                        })
