"""Stability in the run: declared targets for the plan, and the post-write check the worker calls.

The plan builder calls :func:`stability_targets` so the stability tables of a task appear as declared
``merge`` targets (plan review and dry runs list them). After the task function returns, the worker calls
:func:`run_stability`, which reads the written output through the run's table I/O, checks it, writes the
rows through the task context (only to the declared targets) and applies the gate.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.adapter import is_rows
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import StabilityConfig, parse_declarations
from bricksmith.monitor.stability.engine import (
    ERROR,
    Outcome,
    StabilityGateError,
    check,
    error_outcome,
)
from bricksmith.monitor.stability.tables import KINDS, SCHEMAS, merge_rows, table_name, to_frame, write_options

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.plan.model import PlanEntry, Target
    from bricksmith.runner.context import TaskContext

KEY = "stability"
ATTEMPTS = 3
_LOCKS: dict[str, threading.Lock] = {}
_GUARD = threading.Lock()


@dataclass(frozen=True)
class Declaration:
    """A validated declaration bound to the output it watches and the physical stability tables."""

    config: StabilityConfig
    output: str
    physical: str
    tables: dict[str, str]

    @property
    def dataset(self) -> str:
        """Return the dataset name: the declared one, else the output's physical name."""
        return self.config.dataset or self.physical


def resolve(value: object, outputs: Mapping[str, str]) -> tuple[Declaration, ...]:
    """Bind the ``stability`` metadata of a task to its outputs (``name -> physical``)."""
    found: list[Declaration] = []
    for config in parse_declarations(value):
        if config.output:
            names = [n for n, p in outputs.items() if config.output in (n, n.rsplit(".", 1)[-1], p)]
        else:
            names = list(outputs)
        if len(names) != 1:
            listed = ", ".join(sorted(outputs)) or "none"
            msg = f"stability: output must name exactly one task output ({listed}); got {config.output or '(none)'!r}"
            raise MonitorError(msg)
        physical = outputs[names[0]]
        scope = config.scope or physical.rsplit(".", 1)[0]
        if scope == physical:
            msg = f"stability: {physical!r} has no schema; declare scope as catalog.schema"
            raise MonitorError(msg)
        tables = {kind: table_name(scope, config.prefix, kind) for kind in KINDS}
        found.append(Declaration(config, names[0], physical, tables))
    return tuple(found)


def stability_targets(
    metadata: Mapping[str, object], outputs: Mapping[str, str], target_type: type[Target]
) -> tuple[Target, ...]:
    """Return the declared ``merge`` targets of the stability tables (none without a ``stability`` key)."""
    if KEY not in metadata:
        return ()
    seen: dict[str, Target] = {}
    for declaration in resolve(metadata[KEY], outputs):
        for kind, physical in declaration.tables.items():
            if physical in outputs.values():
                msg = f"stability: {physical!r} is also a task output; choose another prefix or scope"
                raise MonitorError(msg)
            seen.setdefault(
                physical, target_type(physical, physical, ("merge",), protected=False, options=write_options(kind))
            )
    return tuple(seen[p] for p in sorted(seen))


def declarations(entry: PlanEntry) -> tuple[Declaration, ...]:
    """Return the declarations of a plan entry (read back from its output targets' options)."""
    outputs = {t.name: t.physical for t in entry.targets if "stability_table" not in t.options}
    for target in entry.targets:
        if KEY in target.options:
            return resolve(target.options[KEY], outputs)
    return ()


def _lock(physical: str) -> threading.Lock:
    with _GUARD:
        return _LOCKS.setdefault(physical, threading.Lock())


class _IOStore:
    """Reads stored rows of a dataset through the run's table I/O (Spark tables or in-memory lists)."""

    def __init__(self, ctx: TaskContext, tables: Mapping[str, str], spark: Any) -> None:  # noqa: ANN401 - session
        self.ctx = ctx
        self.tables = tables
        self.spark = spark

    def rows(self, kind: str, dataset: str) -> list[dict[str, Any]]:
        physical = self.tables[kind]
        if self.spark is not None:
            if not self.spark.catalog.tableExists(physical):
                return []
            from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

            frame = self.spark.table(physical).where(f.col("dataset") == f.lit(dataset))
            return [r.asDict() for r in frame.collect()]
        return [dict(r) for r in self.memory(physical) if r.get("dataset") == dataset]

    def memory(self, physical: str) -> list[dict[str, Any]]:
        try:
            stored = self.ctx.io.read(physical)
        except Exception:  # noqa: BLE001 - a missing table is an empty history
            return []
        return [dict(r) for r in stored] if is_rows(stored) else []  # type: ignore[union-attr]


def _write(ctx: TaskContext, store: _IOStore, kind: str, rows: Sequence[Mapping[str, Any]]) -> None:
    physical = store.tables[kind]
    with _lock(physical):
        for attempt in range(1, ATTEMPTS + 1):
            try:
                if store.spark is not None:
                    data: object = to_frame(store.spark, kind, rows)
                else:
                    data = merge_rows(kind, store.memory(physical), rows)
                ctx.write(physical, data, operation="merge")
            except Exception as error:
                concurrent = "concurrent" in type(error).__name__.lower() or "CONCURRENT" in str(error).upper()
                if not concurrent or attempt == ATTEMPTS:
                    raise
                time.sleep(attempt)
            else:
                return


def _check_one(ctx: TaskContext, declaration: Declaration, run_id: str) -> Outcome:
    data = ctx.io.read(declaration.physical)
    spark = None if is_rows(data) else getattr(ctx.io, "spark", None) or getattr(data, "sparkSession", None)
    store = _IOStore(ctx, declaration.tables, spark)
    result = check(
        data, declaration.config, store, dataset=declaration.dataset, run_id=run_id, task_id=ctx.task_id,
        physical=declaration.physical,
    )  # fmt: skip
    for kind in KINDS:
        if result.tables[kind]:
            _write(ctx, store, kind, result.tables[kind])
    outcome = result.outcome
    return Outcome(**{**outcome.to_dict(), "output": declaration.output})


def run_stability(ctx: TaskContext, run_id: str) -> tuple[tuple[Outcome, ...], BaseException | None]:
    """Check every declared output of the task; return the outcomes and the error that fails the task, if any."""
    outcomes: list[Outcome] = []
    failure: BaseException | None = None
    for declaration in declarations(ctx.entry):
        try:
            outcome = _check_one(ctx, declaration, run_id)
        except Exception as error:  # noqa: BLE001 - the error policy decides
            outcomes.append(error_outcome(declaration.dataset, error, declaration.output))
            if declaration.config.gate.on_error == "fail" and failure is None:
                failure = error
            continue
        outcomes.append(outcome)
        if outcome.failed and failure is None:
            failure = StabilityGateError(outcome.message)
    return tuple(outcomes), failure


def summary_line(outcome: Mapping[str, Any]) -> str:
    """Return the one-line text summary of an outcome for the run summary."""
    if outcome.get("status") == ERROR:
        return f"  stability {outcome.get('dataset')}: error: {outcome.get('message')}"
    return f"  stability {outcome.get('status')}: {outcome.get('message')}"


__all__ = [
    "KEY",
    "SCHEMAS",
    "Declaration",
    "declarations",
    "resolve",
    "run_stability",
    "stability_targets",
    "summary_line",
]
