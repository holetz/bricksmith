"""Stable read-model shapes for the proof inventory and its reports.

Scanners populate these dataclasses once. The doctor, status and golden fixtures consume their
serialised form instead of walking the target again. Paths inside the model are relative to the
declared proof root; the envelope keeps the absolute repository and proof roots separately.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from quenching.common.front import Finding


def _relative(path: str, root: str) -> str:
    value = os.path.relpath(path, root).replace(os.sep, "/")
    if value == ".":
        return "."
    if value.startswith("../") or os.path.isabs(value):
        return value
    return value


def _sorted_strings(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


@dataclass(frozen=True)
class Layer:
    name: str
    path: str
    reach: str | None = None
    budget: float | None = None
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "reach": self.reach,
                "budget": self.budget, "required": self.required}


@dataclass(frozen=True)
class TestModule:
    path: str
    layer: str | None
    marker: str | None
    imports: tuple[str, ...] = ()
    test_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "imports", _sorted_strings(self.imports))

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "layer": self.layer, "marker": self.marker,
                "imports": list(self.imports), "testCount": self.test_count}


@dataclass(frozen=True)
class Fixture:
    name: str
    path: str
    library: bool
    reach: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "library": self.library,
                "reach": self.reach}


@dataclass(frozen=True)
class Conftest:
    path: str
    library: bool
    fixture_names: tuple[str, ...] = ()
    imports: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixture_names", _sorted_strings(self.fixture_names))
        object.__setattr__(self, "imports", _sorted_strings(self.imports))

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "library": self.library,
                "fixtureNames": list(self.fixture_names), "imports": list(self.imports)}


@dataclass(frozen=True)
class GateConfig:
    path: str | None = None
    addopts: tuple[str, ...] = ()
    markers: tuple[str, ...] = ()
    strict_markers: bool = False
    coverage_sources: tuple[str, ...] = ()
    coverage_floor: float | None = None
    invocations: tuple[str, ...] = ()
    ratchet_path: str | None = None
    ratchet_exists: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "addopts", _sorted_strings(self.addopts))
        object.__setattr__(self, "markers", _sorted_strings(self.markers))
        object.__setattr__(self, "coverage_sources", _sorted_strings(self.coverage_sources))
        object.__setattr__(self, "invocations", _sorted_strings(self.invocations))

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "addopts": list(self.addopts),
                "markers": list(self.markers), "strictMarkers": self.strict_markers,
                "coverageSources": list(self.coverage_sources),
                "coverageFloor": self.coverage_floor, "invocations": list(self.invocations),
                "ratchetPath": self.ratchet_path, "ratchetExists": self.ratchet_exists}


@dataclass(frozen=True)
class CIInvocation:
    path: str
    provider: str
    command: str
    runs_gate: bool
    randomizes_order: bool

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "provider": self.provider, "command": self.command,
                "runsGate": self.runs_gate, "randomizesOrder": self.randomizes_order}


@dataclass(frozen=True)
class CostContract:
    """What the target declares about the cost of a run: temp root, workers and tier scopes."""

    temp_root: str | None = None
    workers: str | int | None = None
    scopes: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scopes", dict(sorted(self.scopes.items())))

    def as_dict(self) -> dict[str, Any]:
        return {"tempRoot": self.temp_root, "workers": self.workers,
                "scopes": dict(self.scopes)}


@dataclass(frozen=True)
class ProofInventory:
    repo_root: str
    proof_root: str
    layers: tuple[Layer, ...] = ()
    test_modules: tuple[TestModule, ...] = ()
    fixtures: tuple[Fixture, ...] = ()
    conftests: tuple[Conftest, ...] = ()
    gate: GateConfig = field(default_factory=GateConfig)
    ci: tuple[CIInvocation, ...] = ()
    cost: CostContract | None = None
    config_path: str | None = None
    measured_roots: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    source_surfaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "layers", tuple(sorted(self.layers, key=lambda item: item.name)))
        object.__setattr__(self, "test_modules",
                           tuple(sorted(self.test_modules, key=lambda item: item.path)))
        object.__setattr__(self, "fixtures", tuple(sorted(self.fixtures,
                                                            key=lambda item: (item.path, item.name))))
        object.__setattr__(self, "conftests", tuple(sorted(self.conftests,
                                                             key=lambda item: item.path)))
        object.__setattr__(self, "ci", tuple(sorted(self.ci, key=lambda item: item.path)))
        object.__setattr__(self, "measured_roots", _sorted_strings(self.measured_roots))
        object.__setattr__(self, "exclusions", _sorted_strings(self.exclusions))
        object.__setattr__(self, "source_surfaces", _sorted_strings(self.source_surfaces))

    def as_dict(self) -> dict[str, Any]:
        return {
            "repoRoot": self.repo_root,
            "root": self.proof_root,
            "layers": [item.as_dict() for item in self.layers],
            "testModules": [item.as_dict() for item in self.test_modules],
            "fixtures": [item.as_dict() for item in self.fixtures],
            "conftests": [item.as_dict() for item in self.conftests],
            "gate": self.gate.as_dict(),
            "ci": [item.as_dict() for item in self.ci],
            "cost": self.cost.as_dict() if self.cost else None,
            "config": self.config_path,
            "measuredRoots": list(self.measured_roots),
            "exclusions": list(self.exclusions),
            "sourceSurfaces": list(self.source_surfaces),
        }
