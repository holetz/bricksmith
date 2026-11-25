"""The static shape-and-gate checks for the proof front.

Each function consumes the frozen inventory model and returns findings without opening a second
interpretation of the target. The checks deliberately grade declarations and structure only; the
suite itself remains outside this pillar's execution boundary.
"""
from __future__ import annotations

import os

from quenching.ops.config import load_ops_config
from quenching.common.front import register_checks
from quenching.ops.inventory import build_inventory as build_ops_inventory
from quenching.proof.model import Finding, ProofInventory
from quenching.proof.readme import readme_path, render_readme_document


def _error(code: str, message: str, *, path: str | None = None,
           layer: str | None = None) -> Finding:
    return Finding(code, "error", message, path=path, layer=layer)


def check_unlayered(inventory: ProofInventory) -> list[Finding]:
    return [_error("pf-unlayered", "test module is not inside a declared layer",
                    path=item.path)
            for item in inventory.test_modules if item.layer is None]


def check_unmarked(inventory: ProofInventory) -> list[Finding]:
    findings: list[Finding] = []
    registered = set(inventory.gate.markers)
    if inventory.test_modules and not inventory.gate.strict_markers:
        findings.append(_error("pf-unmarked", "pytest strict markers are not enabled",
                               path=inventory.gate.path))
    for item in inventory.test_modules:
        if item.layer and item.marker != item.layer:
            findings.append(_error("pf-unmarked", "derived marker disagrees with its layer",
                                   path=item.path, layer=item.layer))
        if item.marker and item.marker not in registered:
            findings.append(_error("pf-unmarked", f"derived marker `{item.marker}` is not registered",
                                   path=item.path, layer=item.layer))
    return findings


def check_loose_fixture(inventory: ProofInventory) -> list[Finding]:
    locations: dict[str, list[str]] = {}
    for fixture in inventory.fixtures:
        if not fixture.library:
            locations.setdefault(fixture.name, []).append(fixture.path)
    findings: list[Finding] = []
    for name, paths in sorted(locations.items()):
        if len(paths) > 1:
            findings.append(_error("pf-loose-fixture",
                                   f"fixture `{name}` is copied across {len(paths)} test modules",
                                   path=paths[0]))
    return findings


def check_fat_conftest(inventory: ProofInventory) -> list[Finding]:
    findings: list[Finding] = []
    for item in inventory.conftests:
        if not item.library and item.fixture_names:
            findings.append(_error("pf-fat-conftest",
                                   "root conftest defines domain fixtures; move them to fixtures/",
                                   path=item.path))
    return findings


def check_unmeasured_surface(inventory: ProofInventory) -> list[Finding]:
    measured = {item.rstrip("/").split("/")[0] for item in inventory.measured_roots}
    excluded = {item.rstrip("/").split("/")[0] for item in inventory.exclusions}
    return [_error("pf-unmeasured-surface",
                   f"source surface `{surface}` is not in measuredRoots or proofExclusions",
                   path=surface)
            for surface in inventory.source_surfaces
            if surface not in measured and surface not in excluded]


def check_no_floor(inventory: ProofInventory) -> list[Finding]:
    if not inventory.measured_roots:
        return []
    if inventory.gate.coverage_floor is not None or inventory.gate.ratchet_exists:
        return []
    return [_error("pf-no-floor", "measured roots have no coverage floor or ratchet file",
                   path=inventory.gate.ratchet_path)]


def check_stop_first(inventory: ProofInventory) -> list[Finding]:
    return [_error("pf-stop-first", "pytest addopts stops after the first failure",
                   path=inventory.gate.path)
            for option in inventory.gate.addopts if option.startswith("--maxfail")]


def check_empty_layer(inventory: ProofInventory) -> list[Finding]:
    present = {item.layer for item in inventory.test_modules if item.layer}
    return [_error("pf-empty-layer", "required layer has no collected test module",
                   path=item.path, layer=item.name)
            for item in inventory.layers if item.required and item.name not in present]


def check_no_ci(inventory: ProofInventory) -> list[Finding]:
    if any(item.runs_gate for item in inventory.ci):
        return []
    return [_error("pf-no-ci", "no CI definition invokes the proof gate",
                   path=inventory.ci[0].path if inventory.ci else None)]


def _ops_root_module(repo_root: str, operations_root: str) -> str | None:
    relative = os.path.relpath(operations_root, repo_root)
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return None
    parts = relative.replace(os.sep, "/").split("/")
    return ".".join(part for part in parts if part not in {"", "."}) or None


def check_order_unproven(inventory: ProofInventory) -> list[Finding]:
    if any(item.randomizes_order for item in inventory.ci if item.runs_gate):
        return []
    return [_error("pf-order-unproven",
                   "no proof-gate invocation declares randomized test order",
                   path=inventory.ci[0].path if inventory.ci else None)]


def check_untested_entrypoint(inventory: ProofInventory) -> list[Finding]:
    """Compare test imports with the ops inventory, or stay silent when ops is not configured."""
    operations, err = build_ops_inventory(inventory.repo_root)
    if err:
        if err.get("code") == "op-config-missing":
            return []
        return [_error("pf-untested-entrypoint",
                       f"ops inventory could not be read: {err.get('message', 'unknown error')}")]
    assert operations is not None
    imported = {name for module in inventory.test_modules for name in module.imports}
    root_module = _ops_root_module(inventory.repo_root, operations.root)
    findings: list[Finding] = []
    for entry in operations.entry_points:
        if not entry.module:
            continue
        candidates = {entry.module}
        if root_module:
            candidates.add(f"{root_module}.{entry.module}")
        if any(name == candidate or name.startswith(candidate + ".")
               for candidate in candidates for name in imported):
            continue
        findings.append(_error("pf-untested-entrypoint",
                               f"operations entry point `{entry.module}` is not imported by a test",
                               path=entry.path))
    return findings


COST_REQUIRED = ("tempRoot", "workers", "scopes.task", "scopes.close", "scopes.ci")


def _cost_missing(inventory: ProofInventory) -> list[str]:
    """Name the keys the cost contract still needs, in the order the contract reads them."""
    cost = inventory.cost
    if cost is None:
        return list(COST_REQUIRED)
    missing: list[str] = []
    if cost.temp_root is None:
        missing.append("tempRoot")
    if cost.workers is None:
        missing.append("workers")
    for name in ("task", "close", "ci"):
        if name not in cost.scopes:
            missing.append(f"scopes.{name}")
    return missing


def check_cost_undeclared(inventory: ProofInventory) -> list[Finding]:
    """Ask a target that declared layers what a run costs, like the generated README check."""
    if not inventory.layers:
        return []
    missing = _cost_missing(inventory)
    if not missing:
        return []
    return [_error("pf-cost-undeclared",
                   f"the cost contract is undeclared; missing: {', '.join(missing)}",
                   path=inventory.config_path)]


def check_temp_shared(inventory: ProofInventory) -> list[Finding]:
    cost = inventory.cost
    if cost is None or cost.temp_root != "system":
        return []
    return [_error("pf-temp-shared",
                   "`cost.tempRoot` is `system`: every run writes the machine's disk",
                   path=inventory.config_path)]


def check_scope_wide(inventory: ProofInventory) -> list[Finding]:
    cost = inventory.cost
    if cost is None:
        return []
    close = cost.scopes.get("close")
    ci = cost.scopes.get("ci")
    if not close or not ci or close != ci:
        return []
    return [_error("pf-scope-wide",
                   "the close scope is the CI scope: every local run pays the whole suite",
                   path=inventory.config_path)]


def check_task_wide(inventory: ProofInventory) -> list[Finding]:
    cost = inventory.cost
    if cost is None:
        return []
    task = cost.scopes.get("task")
    close = cost.scopes.get("close")
    if not task or not close or task != close:
        return []
    return [_error("pf-task-wide",
                   "the task scope is the close scope: every task pays the close's selection",
                   path=inventory.config_path)]


def check_readme_stale(inventory: ProofInventory) -> list[Finding]:
    """Check the generated README only when the target declares layers to describe."""
    if not inventory.layers:
        return []
    path = readme_path(inventory)
    try:
        current = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = ""
    if current == render_readme_document(current, inventory):
        return []
    return [_error("pf-readme-stale", "generated proof README is absent or stale",
                   path=str(path.relative_to(inventory.repo_root)).replace("\\", "/"))]


def conditional_status(inventory: ProofInventory) -> dict[str, str]:
    """State why the cross-front entry-point check did not run when ops is absent."""
    _, err = load_ops_config(inventory.repo_root)
    if err.get("code") == "op-config-missing":
        return {"pf-untested-entrypoint": "not-applicable — ops is not configured"}
    return {}


CHECK_REGISTRY = register_checks(
    ("unlayered", check_unlayered),
    ("unmarked", check_unmarked),
    ("loose-fixture", check_loose_fixture),
    ("fat-conftest", check_fat_conftest),
    ("unmeasured-surface", check_unmeasured_surface),
    ("no-floor", check_no_floor),
    ("stop-first", check_stop_first),
    ("empty-layer", check_empty_layer),
    ("no-ci", check_no_ci),
    ("order-unproven", check_order_unproven),
    ("untested-entrypoint", check_untested_entrypoint),
    ("cost-undeclared", check_cost_undeclared),
    ("temp-shared", check_temp_shared),
    ("scope-wide", check_scope_wide),
    ("task-wide", check_task_wide),
    ("readme-stale", check_readme_stale),
)


def run_checks(inventory: ProofInventory) -> list[Finding]:
    findings = CHECK_REGISTRY.run(inventory)
    return sorted(findings, key=lambda item: (item.code, item.path or "", item.message))
