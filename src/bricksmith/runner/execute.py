"""Plan execution: bounded concurrency, retries, dependent blocking and dry-run."""

from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from bricksmith.errors import BricksmithError, PlanError, ValidationError
from bricksmith.plan import PlanEntry, check_authorization, validate_plan
from bricksmith.plan.model import RUN
from bricksmith.runner.context import TaskContext
from bricksmith.runner.loader import load_task_function
from bricksmith.runner.report import BLOCKED, FAILED, NOT_RUN, SKIPPED, SUCCEEDED, RunReport, TaskResult

if TYPE_CHECKING:
    from collections.abc import Callable

    from bricksmith.context import ExecutionContext
    from bricksmith.graph import TaskGraph
    from bricksmith.plan import Plan
    from bricksmith.runner.io import ExternalProvider, TableIO

    type Loader = Callable[[Path, str, str], Callable[..., object]]


@dataclass(frozen=True)
class RetryPolicy:
    """Attempts per task and the wait before each retry; ``BricksmithError`` failures are never retried."""

    max_attempts: int = 1
    delay: float = 0.0
    backoff: float = 2.0

    def __post_init__(self) -> None:
        """Reject a policy that would never run a task."""
        if self.max_attempts < 1 or self.delay < 0 or self.backoff < 1:
            msg = "retry policy needs max_attempts >= 1, delay >= 0 and backoff >= 1"
            raise ValidationError(msg)


def _error(error: BaseException) -> dict[str, str]:
    code = error.code if isinstance(error, BricksmithError) else type(error).__name__
    return {"code": code, "message": str(error), "type": type(error).__name__}


@dataclass(frozen=True)
class _Worker:
    """Run-wide settings of the task worker; calling it executes one entry with retries."""

    root: Path
    execution: ExecutionContext
    table_io: TableIO
    granted: frozenset[tuple[str, str]]
    retry: RetryPolicy
    loader: Loader
    sleep: Callable[[float], None]
    externals: ExternalProvider | None = None

    def __call__(self, entry: PlanEntry) -> TaskResult:
        started = time.monotonic()
        try:
            function = self.loader(self.root, entry.path, entry.function)
        except Exception as error:  # noqa: BLE001 - any import failure is a task failure
            return TaskResult(
                entry.task_id, FAILED, "import failed", 0, _error(error), seconds=time.monotonic() - started
            )
        attempts = 0
        wait_for = self.retry.delay
        while True:
            attempts += 1
            ctx = TaskContext(entry, self.execution, self.table_io, self.granted, self.externals)
            try:
                function(ctx)
            except Exception as error:  # noqa: BLE001 - task code may raise anything
                if attempts >= self.retry.max_attempts or isinstance(error, BricksmithError):
                    return TaskResult(
                        entry.task_id, FAILED, "task raised", attempts, _error(error), (), tuple(ctx.writes),
                        time.monotonic() - started,
                    )  # fmt: skip
                self.sleep(wait_for)
                wait_for *= self.retry.backoff
            else:
                return TaskResult(
                    entry.task_id, SUCCEEDED, "", attempts, None, (), tuple(ctx.writes), time.monotonic() - started
                )  # fmt: skip


def _block(pending: list[PlanEntry], results: dict[str, TaskResult]) -> None:
    """Move pending entries whose dependency did not succeed to ``blocked`` (plan order covers chains)."""
    for entry in list(pending):
        bad = tuple(d for d in entry.depends_on if d in results and results[d].state in (FAILED, BLOCKED, NOT_RUN))
        if bad:
            results[entry.task_id] = TaskResult(entry.task_id, BLOCKED, "dependency did not succeed", blocked_by=bad)
            pending.remove(entry)


def _ready(entry: PlanEntry, results: dict[str, TaskResult], in_plan: set[str]) -> bool:
    ok = (SUCCEEDED, SKIPPED)
    return all(d not in in_plan or (d in results and results[d].state in ok) for d in entry.depends_on)


def run_plan(  # noqa: PLR0913 - one keyword per documented run setting
    plan: Plan,
    *,
    project_root: Path | str,
    execution: ExecutionContext,
    table_io: TableIO,
    concurrency: int | None = None,
    retry: RetryPolicy | None = None,
    dry_run: bool = False,
    fail_fast: bool = False,
    loader: Loader = load_task_function,
    sleep: Callable[[float], None] = time.sleep,
    externals: ExternalProvider | None = None,
) -> RunReport:
    """Execute a plan that passed :func:`validate_plan`; it is re-checked here for errors and grants.

    Entries run in plan order with at most ``concurrency`` threads (default: the context's). A failed
    task blocks every dependent, transitively; independent branches keep running unless ``fail_fast``
    stops new work (remaining tasks become ``not-run``). With ``dry_run`` no module is imported, no task
    is called and nothing is written: run entries are ``not-run`` and list their declared effects.
    """
    if plan.errors:
        msg = "the plan has errors and cannot start: " + "; ".join(f"{i.code}: {i.message}" for i in plan.errors)
        raise PlanError(msg)
    check_authorization(plan)
    limit = concurrency or execution.concurrency
    if limit < 1:
        msg = "concurrency must be at least 1"
        raise ValidationError(msg)
    results: dict[str, TaskResult] = {}
    pending = [e for e in plan.entries if e.action == RUN]
    for entry in plan.entries:
        if entry.action != RUN:
            results[entry.task_id] = TaskResult(entry.task_id, SKIPPED, entry.reason)
    if dry_run:
        for entry in pending:
            effects = tuple((t.physical, op) for t in entry.targets for op in t.operations)
            results[entry.task_id] = TaskResult(entry.task_id, NOT_RUN, "dry run", writes=effects)
        return _report(plan, results, dry_run=True, limit=limit)

    granted = frozenset((g.object, op) for g in plan.grants for op in g.operations)
    worker = _Worker(Path(project_root), execution, table_io, granted, retry or RetryPolicy(), loader, sleep, externals)
    _drive(worker, pending, results, {e.task_id for e in plan.entries}, limit, fail_fast=fail_fast)
    for entry in pending:
        results[entry.task_id] = TaskResult(entry.task_id, NOT_RUN, "run stopped after a failure")
    return _report(plan, results, dry_run=False, limit=limit)


def _report(plan: Plan, results: dict[str, TaskResult], *, dry_run: bool, limit: int) -> RunReport:
    ordered = tuple(results[e.task_id] for e in plan.entries)
    return RunReport(plan.fingerprint, dry_run, limit, ordered)


def apply_plan(
    plan: Plan,
    graph: TaskGraph,
    *,
    project_root: Path | str,
    execution: ExecutionContext,
    table_io: TableIO,
    **options: object,
) -> RunReport:
    """Validate ``plan`` against the current ``graph`` (stale, scope, grants) and run it.

    ``options`` are the keyword settings of :func:`run_plan`.
    """
    validate_plan(plan, graph)
    return run_plan(plan, project_root=project_root, execution=execution, table_io=table_io, **options)  # type: ignore[arg-type]


def _drive(  # noqa: PLR0913 - the scheduler's whole state
    worker: _Worker,
    pending: list[PlanEntry],
    results: dict[str, TaskResult],
    in_plan: set[str],
    limit: int,
    *,
    fail_fast: bool,
) -> None:
    """Submit ready entries in plan order, at most ``limit`` at once, until nothing can start."""
    running: set[Future[TaskResult]] = set()
    stop = False
    with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="bricksmith-task") as pool:
        while pending or running:
            _block(pending, results)
            for entry in [e for e in pending if not stop and _ready(e, results, in_plan)]:
                if len(running) >= limit:
                    break
                pending.remove(entry)
                running.add(pool.submit(worker, entry))
            if not running:
                break
            finished, running = wait(running, return_when=FIRST_COMPLETED)
            for future in finished:
                result = future.result()
                results[result.task_id] = result
                stop = stop or (fail_fast and result.state == FAILED)
