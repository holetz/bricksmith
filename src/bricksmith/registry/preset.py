"""Naming and validation presets: neutral defaults that a project overrides with data."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bricksmith.registry.errors import fail

SCOPES = ("catalog", "schema", "table", "column")
CASES = ("any", "snake", "upper_snake", "kebab", "lower", "camel", "pascal")
SEVERITIES = ("error", "warning", "off")
DEFAULT_RESERVED = (
    "all", "alter", "and", "as", "by", "create", "delete", "drop", "from", "group", "insert",
    "join", "not", "null", "or", "order", "select", "table", "update", "where",
)  # fmt: skip
DEFAULT_TYPES = (
    "array", "bigint", "binary", "boolean", "date", "decimal", "double", "float", "int", "map",
    "smallint", "string", "struct", "timestamp", "tinyint", "variant",
)  # fmt: skip
DEFAULT_SENSITIVITY = ("public", "internal", "confidential", "restricted")
DEFAULT_SEVERITY: dict[str, str] = {
    "registry.description_missing": "warning",
    "registry.grain_missing": "warning",
}
_RULE_FIELDS = frozenset(
    {"case", "vocabulary", "allowed_tokens", "prefixes", "suffixes", "min_length", "max_length", "reserved"}
)


@dataclass(frozen=True)
class ScopeRule:
    """Naming rules for one scope; empty tuples mean unrestricted."""

    case: str = "snake"
    vocabulary: bool = False
    allowed_tokens: tuple[str, ...] = ()
    prefixes: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()
    min_length: int = 1
    max_length: int = 128
    reserved: tuple[str, ...] = DEFAULT_RESERVED


@dataclass(frozen=True)
class Exemption:
    """A reasoned exception for findings matching a glob, valid until ``expires`` (ISO date)."""

    finding: str
    reason: str
    expires: str


@dataclass(frozen=True)
class Preset:
    """Everything a project can tune without forking the library."""

    scopes: dict[str, ScopeRule] = field(default_factory=lambda: {name: ScopeRule() for name in SCOPES})
    vocabulary: tuple[str, ...] = ()
    logical_types: tuple[str, ...] = DEFAULT_TYPES
    sensitivity_levels: tuple[str, ...] = DEFAULT_SENSITIVITY
    severity: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SEVERITY))
    exemptions: tuple[Exemption, ...] = ()


def default_preset() -> Preset:
    """Return a fresh neutral preset with an empty project vocabulary."""
    return Preset()


def _strs(value: object, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        fail(f"{where}: expected a list of strings")
    return tuple(value)


def _scope(base: ScopeRule, data: dict[str, Any], where: str) -> ScopeRule:
    unknown = sorted(set(data) - _RULE_FIELDS)
    if unknown:
        fail(f"{where}: unknown keys {unknown}")
    updates: dict[str, Any] = {}
    for key, value in data.items():
        if key in {"allowed_tokens", "prefixes", "suffixes", "reserved"}:
            updates[key] = _strs(value, f"{where}.{key}")
        elif key == "case":
            if value not in CASES:
                fail(f"{where}.case: must be one of {list(CASES)}")
            updates[key] = value
        elif key == "vocabulary":
            if not isinstance(value, bool):
                fail(f"{where}.vocabulary: expected a boolean")
            updates[key] = value
        elif not isinstance(value, int) or isinstance(value, bool):
            fail(f"{where}.{key}: expected an integer")
        else:
            updates[key] = value
    return replace(base, **updates)


def parse_preset(data: dict[str, Any], source: str = "preset") -> Preset:
    """Layer project preset data over the neutral defaults."""
    allowed = {"version", "vocabulary", "logical_types", "sensitivity_levels", "scopes", "severity", "exceptions"}
    unknown = sorted(set(data) - allowed)
    if unknown:
        fail(f"{source}: unknown keys {unknown}")
    if data.get("version") != 1:
        fail(f"{source}: unsupported or missing version (expected 1)")
    preset = default_preset()
    scopes = dict(preset.scopes)
    for name, rule in data.get("scopes", {}).items():
        if name not in SCOPES:
            fail(f"{source}: unknown scope '{name}' (expected one of {list(SCOPES)})")
        scopes[name] = _scope(scopes[name], rule, f"{source}.scopes.{name}")
    severity = dict(DEFAULT_SEVERITY)
    for rule_id, level in data.get("severity", {}).items():
        if level not in SEVERITIES:
            fail(f"{source}.severity.{rule_id}: must be one of {list(SEVERITIES)}")
        severity[rule_id] = level
    exemptions: list[Exemption] = []
    for entry in data.get("exceptions", []):
        finding, reason, expires = (str(entry.get(key, "")).strip() for key in ("finding", "reason", "expires"))
        if not (finding and reason and expires):
            fail(f"{source}: every exception needs 'finding', 'reason' and 'expires'")
        exemptions.append(Exemption(finding, reason, expires))
    return Preset(
        scopes=scopes,
        vocabulary=_strs(data["vocabulary"], f"{source}.vocabulary") if "vocabulary" in data else (),
        logical_types=_strs(data["logical_types"], f"{source}.logical_types")
        if "logical_types" in data
        else preset.logical_types,
        sensitivity_levels=_strs(data["sensitivity_levels"], f"{source}.sensitivity_levels")
        if "sensitivity_levels" in data
        else preset.sensitivity_levels,
        severity=severity,
        exemptions=tuple(exemptions),
    )


def load_preset(path: Path | str) -> Preset:
    """Read a preset TOML file."""
    target = Path(path)
    try:
        data = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        fail(f"{target}: cannot read preset ({error})")
    return parse_preset(data, str(target))
