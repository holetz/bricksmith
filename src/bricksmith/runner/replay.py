"""Failed-subgraph replay: derive a new plan from a plan and the report of its run."""

from __future__ import annotations

import glob
from typing import TYPE_CHECKING

from bricksmith.errors import PlanError
from bricksmith.plan import build_plan
from bricksmith.runner.report import BLOCKED, FAILED, NOT_RUN, SUCCEEDED

if TYPE_CHECKING:
    from bricksmith.graph import TaskGraph
    from bricksmith.plan import Plan
    from bricksmith.runner.report import RunReport


def replay_plan(plan: Plan, report: RunReport, graph: TaskGraph) -> Plan:
    """Plan only the work a run left undone.

    Roots are the failed, blocked and not-run tasks of ``report``; tasks that succeeded are marked
    done (listed as skipped, so their dependents are satisfied); the filters, static handling and
    direction of the original selection are kept. The result is a fresh plan: it needs its own
    review and its own authorizations.
    """
    if report.plan_fingerprint != plan.fingerprint:
        msg = "the report belongs to a different plan; replay needs the plan that produced it"
        raise PlanError(msg)
    redo = report.ids(FAILED, BLOCKED, NOT_RUN)
    if not redo:
        msg = "nothing to replay: the run left no failed, blocked or not-run task"
        raise PlanError(msg)
    s = plan.selection
    return build_plan(
        graph,
        roots=tuple(glob.escape(item) for item in redo),
        include=s.include,
        exclude=s.exclude,
        upstream=s.upstream,
        downstream=False,
        include_static=s.include_static,
        partial=s.partial,
        done=(*s.done, *report.ids(SUCCEEDED)),
    )
