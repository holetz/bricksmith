"""The small, domain-neutral kernel shared by local fronts.

Front adapters own their inventory and domain rules. This module owns only the repeated outer
shape: a finding that can carry each front's optional evidence, an ordered check registry, the
doctor envelope and the ``--root``/``--json`` route parser. Keeping the adapter boundary explicit
means a shared helper cannot quietly derive a front-specific payload.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from quenching.common.output import FINDINGS, OK, REFUSAL, NoAbbrevParser


@dataclass(frozen=True)
class Finding:
    """One cross-front finding with optional domain evidence.

    ``band`` belongs to delivery and toolchain, ``entry_point`` to ops, and ``layer`` to proof.
    They stay optional so the common serializer preserves each existing payload instead of
    forcing every front to emit empty fields it never owned.
    """

    code: str
    severity: str
    message: str
    path: str | None = None
    entry_point: str | None = None
    layer: str | None = None
    band: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
        }
        if self.band is not None:
            result["band"] = self.band
        if self.path is not None:
            result["path"] = self.path
        if self.entry_point is not None:
            result["entryPoint"] = self.entry_point
        if self.layer is not None:
            result["layer"] = self.layer
        return result


Check = Callable[[Any], Iterable[Finding | dict[str, Any]]]


class CheckRegistry:
    """An explicitly ordered collection of front checks.

    A name is part of the declaration, not derived from a result. Duplicate names are refused
    at construction so adding the same check twice cannot silently duplicate findings in a
    doctor's payload.
    """

    def __init__(self, checks: Iterable[tuple[str, Check] | Check] = ()) -> None:
        self._checks: list[tuple[str, Check]] = []
        for item in checks:
            if isinstance(item, tuple):
                self.add(item[0], item[1])
            else:
                self.add(item.__name__, item)

    def add(self, name: str, check: Check) -> None:
        if any(existing == name for existing, _ in self._checks):
            raise ValueError(f"check already registered: {name}")
        self._checks.append((name, check))

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._checks)

    def run(self, inventory: Any) -> list[Finding | dict[str, Any]]:
        return [finding for _, check in self._checks for finding in check(inventory)]


def register_checks(*checks: tuple[str, Check] | Check) -> CheckRegistry:
    """Declare a registry in source order, ready for an adapter's doctor."""
    return CheckRegistry(checks)


def _finding_dict(item: Finding | dict[str, Any] | Any) -> dict[str, Any]:
    if hasattr(item, "as_dict"):
        return item.as_dict()
    if isinstance(item, dict):
        return dict(item)
    raise TypeError("a front check must return Finding objects or dictionaries")


def findings_envelope(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """The one findings/errors/warnings/ok block every front payload ends with."""
    errors = sum(item["severity"] == "error" for item in findings)
    return {
        "findings": findings,
        "errors": errors,
        "warnings": len(findings) - errors,
        "ok": not findings,
    }


def doctor_result(payload: dict[str, Any]) -> tuple[dict | None, dict, int]:
    """Map a finished envelope to the shared ``(payload, error, exit)`` contract."""
    return payload, {}, OK if payload["ok"] else FINDINGS


def inspect(root: str, build_inventory: Callable[[str], tuple[Any, dict]],
            checks: CheckRegistry | Iterable[tuple[str, Check] | Check], *,
            enrich: Callable[[Any], dict[str, Any]] | None = None) -> tuple[dict | None, dict]:
    """Build one inventory envelope and run its declared checks.

    ``enrich`` is the adapter's escape hatch for payload fields such as ``scanned`` or
    ``conditional``. It receives the same inventory, so the shared layer never performs a
    second traversal or invents domain fields.
    """
    inventory, error = build_inventory(root)
    if error:
        return None, error
    if inventory is None:
        return None, {"code": "front-no-inventory", "message": "inventory was not returned"}
    registry = checks if isinstance(checks, CheckRegistry) else CheckRegistry(checks)
    findings = [_finding_dict(item) for item in registry.run(inventory)]
    payload = inventory.as_dict()
    if enrich:
        payload.update(enrich(inventory))
    payload.update(findings_envelope(findings))
    return payload, {}


def doctor(root: str, build_inventory: Callable[[str], tuple[Any, dict]],
           checks: CheckRegistry | Iterable[tuple[str, Check] | Check], *,
           enrich: Callable[[Any], dict[str, Any]] | None = None) -> tuple[dict | None, dict, int]:
    """Run a front's static checks with the shared ``(payload, error, exit)`` contract."""
    payload, error = inspect(root, build_inventory, checks, enrich=enrich)
    if error:
        return None, error, REFUSAL
    assert payload is not None
    return doctor_result(payload)


def resolve_root(value: str | None) -> str:
    """Resolve the one CLI root override in the same way for every front."""
    return os.path.abspath(value or os.getcwd())


def build_parser(prog: str, description: str,
                 commands: Iterable[str | tuple[str, str]] = ("doctor", "status")) \
        -> argparse.ArgumentParser:
    """Build the common route parser; front adapters add only their extra verbs and flags."""
    parser = NoAbbrevParser(prog=prog, description=description)
    parser.add_argument("--root", help="repository root (default: current directory)")
    sub = parser.add_subparsers(dest="cmd")
    for command in commands:
        if isinstance(command, tuple):
            name, help_text = command
        else:
            name, help_text = command, f"run the {command} report"
        child = sub.add_parser(name, help=help_text)
        child.add_argument("--json", action="store_true", help="print machine-readable output")
    return parser
