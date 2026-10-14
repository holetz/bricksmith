"""Declaration files (JSON or TOML): the desired schema plus optional evolution and maintenance sections."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bricksmith.evolution.errors import SchemaSpecError
from bricksmith.evolution.maintenance import MaintenanceSpec, parse_maintenance
from bricksmith.evolution.model import TableSchema
from bricksmith.evolution.planner import EvolutionPolicy, parse_policy


@dataclass(frozen=True)
class Declaration:
    """A parsed declaration file."""

    schema: TableSchema
    policy: EvolutionPolicy = field(default_factory=EvolutionPolicy)
    maintenance: MaintenanceSpec = field(default_factory=MaintenanceSpec)


def parse_declaration(data: dict[str, Any]) -> Declaration:
    """Validate a declaration mapping."""
    body = dict(data)
    policy = parse_policy(body.pop("evolution", None))
    maintenance = parse_maintenance(body.pop("maintenance", None))
    return Declaration(TableSchema.from_dict(body), policy, maintenance)


def _read(path: Path | str) -> dict[str, Any]:
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
        return tomllib.loads(text) if file.suffix == ".toml" else json.loads(text)
    except (OSError, ValueError) as error:
        msg = f"cannot read {file.name}: {error}"
        raise SchemaSpecError(msg) from error


def load_declaration(path: Path | str) -> Declaration:
    """Load a ``.toml`` or ``.json`` declaration file."""
    return parse_declaration(_read(path))


def load_live_snapshot(path: Path | str) -> TableSchema:
    """Load a live-state snapshot (``TableSchema.to_dict`` output) so diff and plan run with no workspace."""
    return TableSchema.from_dict(_read(path))
