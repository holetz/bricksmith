"""Column registry, naming checks and semantic metadata; stdlib only, no Spark."""

from __future__ import annotations

from bricksmith.registry.checks import CheckReport, Finding, load_baseline, run_check, write_baseline
from bricksmith.registry.errors import RegistryFormatError
from bricksmith.registry.metadata import (
    SensitivityCoverage,
    TableMetadata,
    merge_comments,
    merge_properties,
    sensitivity_coverage,
    table_metadata,
)
from bricksmith.registry.model import ColumnSpec, Registry, TableSpec, load_registry, parse_registry
from bricksmith.registry.preset import Exemption, Preset, ScopeRule, default_preset, load_preset, parse_preset

__all__ = [
    "CheckReport",
    "ColumnSpec",
    "Exemption",
    "Finding",
    "Preset",
    "Registry",
    "RegistryFormatError",
    "ScopeRule",
    "SensitivityCoverage",
    "TableMetadata",
    "TableSpec",
    "default_preset",
    "load_baseline",
    "load_preset",
    "load_registry",
    "merge_comments",
    "merge_properties",
    "parse_preset",
    "parse_registry",
    "run_check",
    "sensitivity_coverage",
    "table_metadata",
    "write_baseline",
]
