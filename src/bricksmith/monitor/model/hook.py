"""Model monitoring in the run: declared targets for the plan, and the post-write check the worker calls.

The plan builder calls :func:`model_monitoring_targets` so the model monitoring tables of a task appear as declared
``merge`` targets. After the task function returns, the worker calls :func:`run_model_monitoring`, which reads the
written predictions through the run's table I/O, measures them, writes the rows through the task context (only to
the declared targets, the run record last) and applies the gate.
"""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.adapter import is_rows
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.config import KEY, ModelMonitorConfig, parse_declarations
from bricksmith.monitor.model.engine import (
    ERROR,
    ModelMonitorGateError,
    Outcome,
    check,
    error_outcome,
    run_record,
)
from bricksmith.monitor.model.tables import KINDS, RUNS, merge_rows, table_name, to_frame, write_options

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from bricksmith.plan.model import PlanEntry, Target
    from bricksmith.runner.context import TaskContext

ATTEMPTS = 3
_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


@dataclass(frozen=True)
class Declaration:
    """A validated declaration bound to the output it watches and the physical model monitoring tables."""

    config: ModelMonitorConfig
    output: str
    physical: str
    tables: dict[str, str]
    stability_binnings: str


def resolve(value: object, outputs: Mapping[str, str], root: Path | None = None) -> tuple[Declaration, ...]:
    """Bind the ``model_monitoring`` metadata of a task to its outputs (``name -> physical``)."""
    found: list[Declaration] = []
    for config in parse_declarations(value, root):
        if config.output:
            names = [n for n, p in outputs.items() if config.output in (n, n.rsplit(".", 1)[-1], p)]
        else:
            names = list(outputs)
        if len(names) != 1:
            listed = ", ".join(sorted(outputs)) or "none"
            msg = (
                f"model_monitoring: output must name exactly one task output ({listed}); "
                f"got {config.output or '(none)'!r}"
            )
            raise MonitorError(msg)
        physical = outputs[names[0]]
        scope = config.scope or physical.rsplit(".", 1)[0]
        if scope == physical:
            msg = f"model_monitoring: {physical!r} has no schema; declare scope as catalog.schema"
            raise MonitorError(msg)
        tables = {kind: table_name(scope, config.prefix, kind) for kind in KINDS}
        shared = table_name(scope, config.stability_prefix, "binnings")
        found.append(Declaration(config, names[0], physical, tables, shared))
    return tuple(found)


def model_monitoring_targets(
    metadata: Mapping[str, object], outputs: Mapping[str, str], target_type: type[Target], root: Path | None = None
) -> tuple[Target, ...]:
    """Return the declared ``merge`` targets of the model monitoring tables (none without the metadata key)."""
    if KEY not in metadata:
        return ()
    seen: dict[str, Target] = {}
    for declaration in resolve(metadata[KEY], outputs, root):
        for kind, physical in declaration.tables.items():
            if physical in outputs.values():
                msg = f"model_monitoring: {physical!r} is also a task output; choose another prefix or scope"
                raise MonitorError(msg)
            seen.setdefault(
                physical, target_type(physical, physical, ("merge",), protected=False, options=write_options(kind))
            )
    return tuple(seen[p] for p in sorted(seen))


def declarations(entry: PlanEntry, root: Path | None = None) -> tuple[Declaration, ...]:
    """Return the declarations of a plan entry (read back from its output targets' options)."""
    outputs = {
        t.name: t.physical
        for t in entry.targets
        if "model_monitoring_table" not in t.options and "stability_table" not in t.options
    }
    for target in entry.targets:
        if KEY in target.options:
            return resolve(target.options[KEY], outputs, root)
    return ()


def _lock(physical: str) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(physical, threading.Lock())


class _IOStore:
    """Reads stored rows through the run's table I/O (Spark tables or in-memory lists)."""

    def __init__(self, ctx: TaskContext, declaration: Declaration, spark: Any) -> None:  # noqa: ANN401 - session
        self.ctx = ctx
        self.declaration = declaration
        self.spark = spark

    def _read(self, physical: str, column: str, value: str) -> list[dict[str, Any]]:
        if self.spark is not None:
            if not self.spark.catalog.tableExists(physical):
                return []
            from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

            return [r.asDict() for r in self.spark.table(physical).where(f.col(column) == f.lit(value)).collect()]
        return [r for r in self.memory(physical) if r.get(column) == value]

    def rows(self, kind: str, model: str) -> list[dict[str, Any]]:
        """Return the stored rows of ``model`` in table ``kind``."""
        return self._read(self.declaration.tables[kind], "model", model)

    def stability_binnings(self, dataset: str) -> list[dict[str, Any]]:
        """Return the stored stability binning rows of ``dataset`` in the declaration's scope."""
        return self._read(self.declaration.stability_binnings, "dataset", dataset)

    def exists(self, physical: str) -> bool:
        """Tell whether a table exists already."""
        if self.spark is not None:
            return bool(self.spark.catalog.tableExists(physical))
        try:
            self.ctx.io.read(physical)
        except Exception:  # noqa: BLE001 - any failure means the table is not there
            return False
        return True

    def memory(self, physical: str) -> list[dict[str, Any]]:
        """Return the in-memory rows of a table (empty when it does not exist)."""
        try:
            stored = self.ctx.io.read(physical)
        except Exception:  # noqa: BLE001 - a missing table is an empty history
            return []
        return [dict(r) for r in stored] if is_rows(stored) else []  # type: ignore[union-attr]


def _write(store: _IOStore, kind: str, rows: Sequence[Mapping[str, Any]]) -> None:
    physical = store.declaration.tables[kind]
    if not rows and store.exists(physical):
        return
    with _lock(physical):
        for attempt in range(1, ATTEMPTS + 1):
            try:
                data: object = (
                    to_frame(store.spark, kind, rows)
                    if store.spark is not None
                    else merge_rows(kind, store.memory(physical), rows)
                )
                store.ctx.write(physical, data, operation="merge")
            except Exception as error:
                concurrent = "concurrent" in type(error).__name__.lower() or "CONCURRENT" in str(error).upper()
                if not concurrent or attempt == ATTEMPTS:
                    raise
                time.sleep(attempt)
            else:
                return


def input_version(spark: Any, physical: str) -> str:  # noqa: ANN401 - session
    """Return the latest Delta version of ``physical`` (``""`` when it cannot be read, or without Spark)."""
    if spark is None:
        return ""
    try:
        rows = spark.sql(f"DESCRIBE HISTORY {physical} LIMIT 1").collect()
    except Exception:  # noqa: BLE001 - a table without history still gets monitored
        return ""
    return str(rows[0]["version"]) if rows else ""


def _check_one(ctx: TaskContext, declaration: Declaration, run_id: str) -> Outcome:
    data = ctx.io.read(declaration.physical)
    spark = None if is_rows(data) else getattr(ctx.io, "spark", None) or getattr(data, "sparkSession", None)
    store = _IOStore(ctx, declaration, spark)
    started_version = input_version(spark, declaration.physical)
    try:
        result = check(
            data, declaration.config, store, run_id=run_id, task_id=ctx.task_id, physical=declaration.physical,
            input_version=started_version,
        )  # fmt: skip
    except Exception as error:
        _record_failure(store, declaration, run_id, error, started_version)
        raise
    for kind in KINDS:  # the run record is the last kind: readers trust only finished runs
        _write(store, kind, result.tables[kind])
    return Outcome(**{**result.outcome.to_dict(), "output": declaration.output})


def _record_failure(store: _IOStore, declaration: Declaration, run_id: str, error: BaseException, version: str) -> None:
    """Write a ``failed`` run record so the attempt is visible; a failure to write it is not the task's error."""
    from datetime import UTC, datetime  # noqa: PLC0415 - tiny

    outcome = error_outcome(declaration.config.model, error, declaration.output)
    row = run_record(
        declaration.config, run_id=run_id, task_id=store.ctx.task_id, physical=declaration.physical,
        input_version=version, binning=None, outcome=outcome,
        started=datetime.now(UTC).isoformat(timespec="seconds"),
    )  # fmt: skip
    with contextlib.suppress(Exception):  # the original error is the one reported
        _write(store, RUNS, [row])


def run_model_monitoring(
    ctx: TaskContext, run_id: str, root: Path | None = None
) -> tuple[tuple[Outcome, ...], BaseException | None]:
    """Check every declared output of the task; return the outcomes and the error that fails the task, if any."""
    outcomes: list[Outcome] = []
    failure: BaseException | None = None
    for declaration in declarations(ctx.entry, root):
        try:
            outcome = _check_one(ctx, declaration, run_id)
        except Exception as error:  # noqa: BLE001 - the error policy decides
            outcomes.append(error_outcome(declaration.config.model, error, declaration.output))
            if declaration.config.gate.on_error == "fail" and failure is None:
                failure = error
            continue
        outcomes.append(outcome)
        if outcome.failed and failure is None:
            failure = ModelMonitorGateError(outcome.message)
    return tuple(outcomes), failure


def summary_line(outcome: Mapping[str, Any]) -> str:
    """Return the one-line text summary of an outcome for the run summary."""
    if outcome.get("status") == ERROR:
        return f"  model monitoring {outcome.get('model')}: error: {outcome.get('message')}"
    return f"  model monitoring {outcome.get('status')} ({outcome.get('run_status')}): {outcome.get('message')}"


__all__ = [
    "Declaration",
    "declarations",
    "input_version",
    "model_monitoring_targets",
    "resolve",
    "run_model_monitoring",
    "summary_line",
]
