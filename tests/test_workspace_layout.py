"""Workspace contracts that hold from day one: one shared namespace and one-way dependencies."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

PACKAGES = Path(__file__).resolve().parents[1] / "packages"

# The subpackage each distribution contributes to the ``bricksmith`` namespace.
OWNED = {
    "bricksmith-registry": "registry",
    "bricksmith-monitor": "monitor",
    "bricksmith-mlops": "mlops",
}

# Which ``bricksmith.<subpackage>`` imports each distribution must never make.
# registry and monitor stand alone; the core never reaches up into mlops or monitor.
FORBIDDEN: dict[str, set[str] | None] = {
    "bricksmith-registry": None,  # None: only its own subpackage is allowed
    "bricksmith-monitor": None,
    "bricksmith": {"mlops", "monitor"},
    "bricksmith-mlops": set(),
}


def bricksmith_imports(path: Path) -> set[str]:
    """Return the first-level ``bricksmith.<x>`` subpackages a module imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        found.update(name.split(".")[1] for name in names if name.startswith("bricksmith."))
    return found


@pytest.mark.parametrize("dist", sorted(FORBIDDEN))
def test_namespace_root_has_no_init(dist: str) -> None:
    assert not (PACKAGES / dist / "src" / "bricksmith" / "__init__.py").exists()


def test_namespace_resolves_every_distribution() -> None:
    namespace = importlib.import_module("bricksmith")
    assert namespace.__file__ is None, "bricksmith must stay a PEP 420 namespace"
    for subpackage in ("dab", *OWNED.values()):
        importlib.import_module(f"bricksmith.{subpackage}")


@pytest.mark.parametrize(("dist", "forbidden"), sorted(FORBIDDEN.items()))
def test_dependency_direction(dist: str, forbidden: set[str] | None) -> None:
    for module in (PACKAGES / dist / "src").rglob("*.py"):
        imported = bricksmith_imports(module)
        offending = imported - {OWNED[dist]} if forbidden is None else imported & forbidden
        assert not offending, f"{module.relative_to(PACKAGES)} imports bricksmith.{sorted(offending)}"
