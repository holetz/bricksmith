"""Run a revision in its isolated destination through the common runner, and compare it with the baseline."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bricksmith.experiments.errors import ExperimentError
from bricksmith.experiments.isolation import IsolationPlan, plan_isolation
from bricksmith.experiments.store import ExperimentIO
from bricksmith.plan import Authorization
from bricksmith.quality import GatePolicy, RegressionReport, build_regression_report
from bricksmith.runner import RetryPolicy, RunReport, run_plan

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.context import ExecutionContext
    from bricksmith.experiments.evidence import Evidence
    from bricksmith.experiments.store import ExperimentStore
    from bricksmith.runner.io import TableIO


@dataclass(frozen=True)
class ExperimentRun:
    """Outcome of running a revision: the runner report plus what the run created."""

    report: RunReport
    plan: IsolationPlan
    created_schemas: tuple[str, ...] = ()
    seeded: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether every task succeeded or was skipped."""
        return self.report.ok


def isolated_context(context: ExecutionContext, plan: IsolationPlan) -> ExecutionContext:
    """Return ``context`` with the destination schemas and the revision's parameters applied."""
    return dataclasses.replace(
        context,
        schemas={**context.schemas, **plan.schemas},
        parameters={**context.parameters, **plan.revision.parameters},
    )


def replan(evidence: Evidence, root: Path | str, context: ExecutionContext, store: ExperimentStore) -> IsolationPlan:
    """Re-plan the frozen revision with its frozen pins and require that nothing changed since freezing."""
    plan = plan_isolation(root, evidence.revision, context=context, store=store, resolved=evidence.pins)
    evidence.verify_current(plan)
    return plan


def run_revision(  # noqa: PLR0913 - one keyword per documented run setting
    evidence: Evidence,
    *,
    project_root: Path | str,
    context: ExecutionContext,
    table_io: TableIO,
    store: ExperimentStore,
    dry_run: bool = False,
    retry: RetryPolicy | None = None,
    concurrency: int | None = None,
) -> ExperimentRun:
    """Create the destination, seed state, then run the revision's plan with frozen reads and guarded writes.

    The evidence must still match the sources (stale evidence is refused), every collision and unseeded-state
    finding is an error raised before anything is created, and writes outside the destination are refused.
    """
    plan = replan(evidence, project_root, context, store)
    if not plan.ok or plan.runner_plan is None:
        codes = ", ".join(f"{i.code}({i.subject})" for i in plan.errors)
        msg = f"the revision cannot run: {codes}"
        raise ExperimentError(msg)
    runner = plan.runner_plan
    grants = [Authorization(t.physical, t.operations) for e in runner.to_run for t in e.targets if t.protected]
    reviewed = runner.authorize(*grants) if grants else runner
    guard = ExperimentIO(
        table_io,
        store,
        pins=plan.pins(),
        allowed=frozenset(plan.targets.values()),
        baseline=frozenset(plan.targets) | frozenset(i.physical for i in plan.inputs),
    )
    execution = isolated_context(context, plan)
    if dry_run:
        report = run_plan(reviewed, project_root=project_root, execution=execution, table_io=guard, dry_run=True)
        return ExperimentRun(report, plan)
    created = []
    for catalog, schema in plan.isolated_schemas:
        if not store.schema_exists(catalog, schema):
            store.create_schema(catalog, schema)
            created.append(f"{catalog}.{schema}")
    for base, iso, version in plan.seeds:
        store.copy(base, iso, version=version)
    report = run_plan(
        reviewed,
        project_root=project_root,
        execution=execution,
        table_io=guard,
        retry=retry,
        concurrency=concurrency,
    )
    return ExperimentRun(report, plan, tuple(created), tuple(iso for _, iso, _ in plan.seeds))


def compare_revision(
    evidence: Evidence, store: ExperimentStore, *, policy: GatePolicy | None = None
) -> RegressionReport:
    """Compare every declared output of the candidate with its baseline and apply the gate."""
    revision = evidence.revision
    targets = evidence.targets
    if not revision.compare:
        msg = "the revision declares no compare entries; add [[compare]] with an output and keys"
        raise ExperimentError(msg)
    comparisons = {}
    for spec in revision.compare:
        found = [p for p in targets if p == spec.output or p.endswith("." + spec.output)]
        if len(found) != 1:
            msg = f"compare output {spec.output!r} does not name exactly one output rebuilt by this revision"
            raise ExperimentError(msg)
        base = found[0]
        if not store.exists(targets[base]):
            msg = f"the candidate {targets[base]} does not exist; run the revision first"
            raise ExperimentError(msg)
        comparisons[base] = store.compare(base, targets[base], spec)
    return build_regression_report(report_name(evidence), comparisons=comparisons, policy=policy)


def report_name(evidence: Evidence) -> str:
    """Return the name that ties a regression report to one frozen revision."""
    return f"experiment {evidence.name} {evidence.fingerprint[:12]}"
