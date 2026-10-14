"""The Spark/Delta ``TableIO``: validate, report the plan, then mutate; reads never write."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.storage.errors import EmptyInputError, UnsupportedError, WriteCheckError, WriteSpecError
from bricksmith.storage.history import current_version
from bricksmith.storage.plan import CHECK, GUARD, POST_CHECK, WritePlan, ensure_authorized, plan_write
from bricksmith.storage.registry_sync import apply_registry_metadata
from bricksmith.storage.spec import OBJECT_STRATEGIES, WriteSpec, parse_write_spec
from bricksmith.storage.sql import INPUT, quote

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from bricksmith.context import ExecutionContext
    from bricksmith.registry.model import Registry

LOG = logging.getLogger("bricksmith.storage")
_UNSUPPORTED_MARKERS = ("not supported", "unsupported", "not available", "is not enabled", "pipelines")


@dataclass(frozen=True)
class HookEvent:
    """Passed to pre and post hooks: the plan and the Spark session (hooks must not hide writes)."""

    plan: WritePlan
    spark: Any


@dataclass(frozen=True)
class WriteResult:
    """Outcome of one write; ``version_before`` is the restore point for the recovery path."""

    target: str
    strategy: str
    state: str
    reason: str = ""
    version_before: int | None = None
    version_after: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "reason": self.reason,
            "state": self.state,
            "strategy": self.strategy,
            "target": self.target,
            "version_after": self.version_after,
            "version_before": self.version_before,
        }


@dataclass(frozen=True)
class Prepared:
    """A validated write: the spec, the final plan and the data to register."""

    spec: WriteSpec
    plan: WritePlan
    data: Any
    empty: bool
    skip: str = ""


Hook = "Callable[[HookEvent], None]"


class SparkTableIO:
    """Table I/O over a Spark session (Databricks Connect, serverless) with the write strategies.

    ``spark`` is a session or a zero-argument factory, so building the object never connects.
    ``registry`` (with ``registry_tables`` mapping a target name or physical name to a registry table)
    applies comments and properties after each successful table write.
    """

    def __init__(  # noqa: PLR0913 - one keyword per optional collaborator
        self,
        spark: Any,  # noqa: ANN401 - session or factory
        *,
        registry: Registry | None = None,
        registry_tables: Mapping[str, str] | None = None,
        pre_hooks: Sequence[Callable[[HookEvent], None]] = (),
        post_hooks: Sequence[Callable[[HookEvent], None]] = (),
        reporter: Callable[[WritePlan], None] | None = None,
    ) -> None:
        """Keep collaborators; nothing connects here."""
        self._spark_or_factory = spark
        self._session: Any = None
        self.registry = registry
        self.registry_tables = dict(registry_tables or {})
        self.pre_hooks = tuple(pre_hooks)
        self.post_hooks = tuple(post_hooks)
        self.reporter = reporter
        self.plans: list[WritePlan] = []
        self.results: list[WriteResult] = []

    @classmethod
    def for_context(cls, context: ExecutionContext) -> SparkTableIO:
        """Build the backend for ``context``; the session is created on first use."""
        from bricksmith.clients import Clients  # noqa: PLC0415 - keep import light

        clients = Clients(context)
        return cls(clients.spark)

    @property
    def spark(self) -> Any:  # noqa: ANN401 - Spark session
        """The session, created on first use."""
        if self._session is None:
            source = self._spark_or_factory
            self._session = source() if callable(source) and not hasattr(source, "sql") else source
        return self._session

    def read(self, physical: str) -> object:
        """Return the table or view as a DataFrame; reading never writes."""
        return self.spark.table(physical)

    def explain(
        self, physical: str, data: object, *, operation: str, options: Mapping[str, object] | None = None
    ) -> WritePlan:
        """Return the plan for a write without running any check or mutation."""
        return self._prepare(physical, data, operation, dict(options or {})).plan

    def write(
        self, physical: str, data: object, *, operation: str, options: Mapping[str, object] | None = None
    ) -> None:
        """Apply the strategy: protection, validation and checks first, then one reported mutation."""
        opts = dict(options or {})
        prepared = self._prepare(physical, data, operation, opts)
        result = self._execute(physical, prepared, opts)
        self.results.append(result)

    def _prepare(self, physical: str, data: Any, operation: str, opts: dict[str, object]) -> Prepared:  # noqa: ANN401
        spec = parse_write_spec(opts, operation)
        protected, granted = opts.get("protected") is True, opts.get("granted") is True
        ensure_authorized(physical, spec.strategy, protected=protected, granted=granted)
        if spec.strategy in OBJECT_STRATEGIES:
            plan = plan_write(
                physical, spec, operation=spec.strategy, statement=data, protected=protected, granted=granted
            )
            return Prepared(spec, plan, None, empty=False)
        if not hasattr(data, "columns") or not hasattr(data, "createOrReplaceTempView"):
            msg = f"{spec.strategy} writes a Spark DataFrame, got {type(data).__name__}"
            raise WriteSpecError(msg)
        spark = self.spark
        exists = bool(spark.catalog.tableExists(physical))
        target_columns = list(spark.table(physical).columns) if exists else None
        empty = len(data.limit(1).collect()) == 0
        values: list[dict[str, object]] | None = None
        if spec.strategy == "replace_partitions" and spec.window is None and not spec.partitions:
            if empty:
                values = []
            else:
                rows = data.select(*spec.partition_by).distinct().collect()
                values = [{c: row[c] for c in spec.partition_by} for row in rows]
        skip = ""
        if empty:
            if spec.on_empty == "fail":
                msg = f"{physical}: the input is empty and on_empty=fail; nothing was written"
                raise EmptyInputError(msg)
            if spec.on_empty == "skip":
                skip = "empty input; on_empty=skip"
            elif values == []:
                msg = "allow_empty with replace_partitions needs explicit partitions or a window"
                raise WriteSpecError(msg)
        plan = plan_write(
            physical,
            spec,
            operation=spec.strategy,
            input_columns=list(data.columns),
            target_columns=target_columns,
            partition_values=values,
            protected=protected,
            granted=granted,
        )
        return Prepared(spec, plan, data, empty, skip)

    def _count(self, sql: str, view: str) -> int:
        return int(self.spark.sql(sql.replace(INPUT, quote(view))).collect()[0][0])

    def _check(self, step_sql: str, description: str, expect: int, view: str, *, post: bool) -> None:
        found = self._count(step_sql, view)
        if found != expect:
            tail = " (the write is already committed; see the recovery path)" if post else "; nothing was written"
            msg = f"check failed: {description}: found {found}, expected {expect}{tail}"
            raise WriteCheckError(msg)

    def _execute(self, physical: str, prepared: Prepared, opts: dict[str, object]) -> WriteResult:  # noqa: C901, PLR0912
        plan, spec = prepared.plan, prepared.spec
        if prepared.skip:
            self._report(plan)
            return WriteResult(physical, spec.strategy, "skipped", prepared.skip)
        spark = self.spark
        before = current_version(spark, physical)
        view = f"_bs_input_{uuid.uuid4().hex[:12]}"
        data = prepared.data
        if data is not None:
            if plan.input_filter:
                data = data.filter(plan.input_filter)
            data.createOrReplaceTempView(view)
        try:
            for step in plan.steps:
                if step.kind == GUARD and before is not None:
                    existing = int(spark.sql(f"SELECT count(*) FROM {quote(physical)}").collect()[0][0])
                    if existing:
                        self._report(plan)
                        return WriteResult(
                            physical, spec.strategy, "skipped", "static output already populated", before, before
                        )
                if step.kind == CHECK:
                    self._check(step.sql, step.description, step.expect, view, post=False)
            event = HookEvent(plan, spark)
            for hook in self.pre_hooks:
                hook(event)
            self._report(plan)
            for step in plan.steps:
                if step.mutates:
                    self._run(step.sql.replace(INPUT, quote(view)), spec.strategy)
                elif step.kind == POST_CHECK:
                    self._check(step.sql, step.description, step.expect, view, post=True)
            for hook in self.post_hooks:
                hook(event)
            if self.registry is not None and spec.strategy != "function":
                name = self.registry_tables.get(str(opts.get("target", ""))) or self.registry_tables.get(physical)
                if name:
                    apply_registry_metadata(spark, self.registry, name, physical)
        finally:
            if data is not None:
                spark.catalog.dropTempView(view)
        return WriteResult(physical, spec.strategy, "applied", "", before, current_version(spark, physical))

    def _run(self, sql: str, strategy: str) -> None:
        try:
            self.spark.sql(sql)
        except Exception as error:
            text = str(error).lower()
            if strategy == "materialized_view" and any(m in text for m in _UNSUPPORTED_MARKERS):
                first = str(error).strip().splitlines()[0][:200] if str(error).strip() else "refused"
                msg = f"materialized views are not supported here: {first}"
                raise UnsupportedError(msg) from error
            raise

    def _report(self, plan: WritePlan) -> None:
        self.plans.append(plan)
        LOG.info("write plan %s %s: %d mutating step(s)", plan.strategy, plan.target, len(plan.mutating_steps))
        if self.reporter is not None:
            self.reporter(plan)
