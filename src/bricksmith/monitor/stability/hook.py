"""Stability in the run: declared targets for the plan, and the post-write check the worker calls.

The plan builder calls :func:`stability_targets` so the stability tables of a task appear as declared
``merge`` targets (plan review and dry runs list them). After the task function returns, the worker calls
:func:`run_stability`, which reads the written output through the run's table I/O, checks it, writes the
rows through the task context (only to the declared targets) and applies the gate.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.adapter import is_rows
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import NotifyPolicy, StabilityConfig, parse_declarations
from bricksmith.monitor.stability.engine import (
    ERROR,
    MemoryStore,
    Outcome,
    StabilityGateError,
    check,
    error_outcome,
)
from bricksmith.monitor.stability.tables import (
    ALL_KINDS,
    KINDS,
    SCHEMAS,
    merge_rows,
    table_name,
    to_frame,
    write_options,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from pathlib import Path

    from bricksmith.ops.notify import AlertNotifier
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


def resolve(value: object, outputs: Mapping[str, str], root: Path | None = None) -> tuple[Declaration, ...]:
    """Bind the ``stability`` metadata of a task to its outputs (``name -> physical``)."""
    found: list[Declaration] = []
    for config in parse_declarations(value, root):
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
        tables = {kind: table_name(scope, config.prefix, kind) for kind in (ALL_KINDS if config.revision else KINDS)}
        found.append(Declaration(config, names[0], physical, tables))
    return tuple(found)


def stability_targets(
    metadata: Mapping[str, object], outputs: Mapping[str, str], target_type: type[Target], root: Path | None = None
) -> tuple[Target, ...]:
    """Return the declared ``merge`` targets of the stability tables (none without a ``stability`` key)."""
    if KEY not in metadata:
        return ()
    seen: dict[str, Target] = {}
    for declaration in resolve(metadata[KEY], outputs, root):
        for kind, physical in declaration.tables.items():
            if physical in outputs.values():
                msg = f"stability: {physical!r} is also a task output; choose another prefix or scope"
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
        if "stability_table" not in t.options and "model_monitoring_table" not in t.options
    }
    for target in entry.targets:
        if KEY in target.options:
            return resolve(target.options[KEY], outputs, root)
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

    def rows(
        self,
        kind: str,
        dataset: str,
        *,
        version: int | None = None,
        periods: Collection[str] | None = None,
        states: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        physical = self.tables[kind]
        if self.spark is not None:
            if not self.spark.catalog.tableExists(physical):
                return []
            from pyspark.sql import functions as f  # noqa: PLC0415 - heavy integration  # pyright: ignore[reportMissingImports]

            frame = self.spark.table(physical).where(f.col("dataset") == f.lit(dataset))
            if version is not None:
                frame = frame.where(f.col("version") == f.lit(version))
            if periods is not None:
                frame = frame.where(f.col("period").isin(list(periods)))
            if states is not None:
                frame = frame.where(f.col("state").isin(list(states)))
            return [r.asDict() for r in frame.collect()]
        return MemoryStore({kind: self.memory(physical)}).rows(
            kind, dataset, version=version, periods=periods, states=states
        )

    def exists(self, kind: str) -> bool:
        physical = self.tables[kind]
        if self.spark is not None:
            return bool(self.spark.catalog.tableExists(physical))
        try:
            self.ctx.io.read(physical)
        except Exception:  # noqa: BLE001 - any failure means the table is not there
            return False
        return True

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


def _check_one(
    ctx: TaskContext,
    declaration: Declaration,
    run_id: str,
    notifier: AlertNotifier | None,
    default: NotifyPolicy | None,
) -> Outcome:
    data = ctx.io.read(declaration.physical)
    spark = None if is_rows(data) else getattr(ctx.io, "spark", None) or getattr(data, "sparkSession", None)
    store = _IOStore(ctx, declaration.tables, spark)
    config = declaration.config
    if notifier is None:
        config = replace(config, notify=None)
    elif config.notify is None:
        config = replace(config, notify=default)
    result = check(
        data, config, store, dataset=declaration.dataset, run_id=run_id, task_id=ctx.task_id,
        physical=declaration.physical, reprocess=ctx.reprocess, skip_unknown=True,
    )  # fmt: skip
    for kind in declaration.tables:
        if result.tables[kind] or not store.exists(kind):
            _write(ctx, store, kind, result.tables[kind])  # an empty write creates the table for dashboards
    if notifier is not None:
        for event in result.notifications:
            notifier.notify(event)  # never raises: delivery cannot fail the task, and runs after the rows are stored
    outcome = result.outcome
    return Outcome(**{**outcome.to_dict(), "output": declaration.output})


def run_stability(
    ctx: TaskContext,
    run_id: str,
    notifier: AlertNotifier | None = None,
    notify: NotifyPolicy | None = None,
    root: Path | None = None,
) -> tuple[tuple[Outcome, ...], BaseException | None]:
    """Check every declared output of the task; return the outcomes and the error that fails the task, if any.

    With a ``notifier``, alerts that opened, escalated or recurred are delivered after the rows are written, under
    the declaration's ``notify`` policy or, when it declares none, the project's ``notify`` default. A relative rules
    file path resolves against the project ``root``.
    """
    outcomes: list[Outcome] = []
    failure: BaseException | None = None
    for declaration in declarations(ctx.entry, root):
        try:
            outcome = _check_one(ctx, declaration, run_id, notifier, notify)
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
