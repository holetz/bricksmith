"""Opt-in materialization, checkpoints and exports of small tables as volume artifacts.

Everything is off unless the policy sets ``enabled=True``. Artifacts live under
``<prefix>/<run_id>/`` in a volume the caller names, so cleanup is exact and never touches other
paths. This is deliberately not Spark ``cache``/``persist``/``checkpoint``: those are unavailable
on serverless compute and are not wrapped here.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass

from bricksmith.errors import AuthorizationError, ValidationError
from bricksmith.io.contracts import Table
from bricksmith.io.errors import LimitExceededError
from bricksmith.io.volumes import ArtifactNotFoundError, ArtifactStore

_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\-]{0,99}")
FORMATS = ("csv", "jsonl")


@dataclass(frozen=True)
class MaterializationPolicy:
    """Limits and opt-in flag for checkpoints and exports."""

    enabled: bool = False
    prefix: str = "_bricksmith"
    max_rows: int = 100_000
    max_files: int = 50


def _name(value: str) -> str:
    if not _NAME.fullmatch(value) or value.strip(".") == "":
        msg = f"{value!r} is not a valid run id or artifact name"
        raise ValidationError(msg)
    return value


class Materializer:
    """Checkpoints (per run, restorable) and exports (per name) with row/file limits and cleanup."""

    def __init__(self, store: ArtifactStore, policy: MaterializationPolicy | None = None) -> None:
        """Bind the artifact store and the policy (disabled by default)."""
        self.store = store
        self.policy = policy or MaterializationPolicy()

    def _require(self, table: Table | None = None) -> None:
        if not self.policy.enabled:
            msg = "materialization is disabled; set MaterializationPolicy(enabled=True) to opt in"
            raise AuthorizationError(msg)
        if table is not None and len(table) > self.policy.max_rows:
            msg = f"{len(table)} rows exceed the policy limit of {self.policy.max_rows}"
            raise LimitExceededError(msg)

    def _directory(self, run_id: str) -> str:
        return f"{self.policy.prefix}/{_name(run_id)}"

    def _room(self, directory: str) -> None:
        if len(self.store.list(directory)) >= self.policy.max_files:
            msg = f"{directory} already holds {self.policy.max_files} artifacts (policy max_files)"
            raise LimitExceededError(msg)

    def checkpoint(self, run_id: str, name: str, table: Table) -> str:
        """Save ``table`` as the checkpoint ``name`` of ``run_id`` and return its absolute path."""
        self._require(table)
        directory = self._directory(run_id)
        relative = f"{directory}/{_name(name)}.json"
        if not self.store.exists(relative):
            self._room(directory)
        try:
            payload = {"columns": list(table.columns), "rows": [list(row) for row in table.rows]}
            return self.store.write_json(relative, payload)
        except TypeError:
            msg = "checkpoint values must be JSON serializable"
            raise ValidationError(msg) from None

    def restore(self, run_id: str, name: str) -> Table | None:
        """Return the checkpoint or ``None`` when it does not exist."""
        self._require()
        try:
            data = self.store.read_json(f"{self._directory(run_id)}/{_name(name)}.json")
        except ArtifactNotFoundError:
            return None
        if not isinstance(data, dict):
            msg = "the checkpoint is not a bricksmith checkpoint"
            raise ValidationError(msg)
        return Table(tuple(data["columns"]), tuple(tuple(row) for row in data["rows"]))

    def export(self, run_id: str, name: str, table: Table, *, fmt: str = "csv") -> str:
        """Write ``table`` as ``csv`` or ``jsonl`` under the run's directory and return its absolute path."""
        self._require(table)
        if fmt not in FORMATS:
            msg = f"export format must be one of {FORMATS}"
            raise ValidationError(msg)
        directory = self._directory(run_id)
        relative = f"{directory}/{_name(name)}.{fmt}"
        if not self.store.exists(relative):
            self._room(directory)
        if fmt == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(table.columns)
            writer.writerows(table.rows)
            text = buffer.getvalue()
        else:
            text = "".join(json.dumps(record, sort_keys=True, default=str) + "\n" for record in table.records())
        return self.store.write(relative, text)

    def cleanup(self, run_id: str) -> int:
        """Delete every artifact of ``run_id`` (only that directory) and return how many were removed."""
        self._require()
        directory = self._directory(run_id)
        removed = 0
        for entry in self.store.list(directory):
            if not entry.is_dir:
                self.store.delete(f"{directory}/{entry.name}")
                removed += 1
        return removed
