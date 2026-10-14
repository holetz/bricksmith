"""Run reports for humans and machines, failure diagnosis with upstream context, and recovery guidance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from bricksmith.ops.events import classify_error, describe_error
from bricksmith.runner.report import BLOCKED, FAILED, NOT_RUN, SKIPPED, SUCCEEDED, RunReport, TaskResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.plan import Plan

OPS_REPORT_VERSION = 1
UNAVAILABLE = "unavailable"
REPORTED = "reported"
_NON_IDEMPOTENT = frozenset({"append", "insert", "merge", "scd2"})


class UsageProvider(Protocol):
    """Supplies platform-reported usage for a task (for example from billing system tables)."""

    def usage(self, task_id: str) -> Mapping[str, Any] | None:
        """Return ``{"value": ..., "unit": ..., "source": ...}`` or ``None`` when nothing is reported."""
        ...


@dataclass(frozen=True)
class UpstreamLink:
    """A task on the path from a diagnosed task to a root cause."""

    task_id: str
    state: str
    reason: str
    error_class: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"error_class": self.error_class, "reason": self.reason, "state": self.state, "task_id": self.task_id}


@dataclass(frozen=True)
class Diagnosis:
    """Why one task did not succeed, with its upstream chain and the way to replay it."""

    task_id: str
    state: str
    cause: str
    error_class: str | None
    root_causes: tuple[str, ...]
    upstream: tuple[UpstreamLink, ...]
    replay_command: str
    partial_writes: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "cause": self.cause,
            "error_class": self.error_class,
            "partial_writes": [{"object": o, "operation": p} for o, p in self.partial_writes],
            "replay_command": self.replay_command,
            "root_causes": list(self.root_causes),
            "state": self.state,
            "task_id": self.task_id,
            "upstream": [u.to_dict() for u in self.upstream],
        }

    def to_text(self) -> str:
        """Return a short human explanation."""
        lines = [f"{self.task_id}: {self.state}", f"  cause: {self.cause}"]
        if self.error_class:
            lines.append(f"  class: {self.error_class}")
        if self.root_causes:
            lines.append("  root cause: " + ", ".join(self.root_causes))
        lines.extend(f"  upstream: {u.task_id} [{u.state}] {u.reason}".rstrip() for u in self.upstream)
        lines.extend(f"  possibly partial write: {o} ({p})" for o, p in self.partial_writes)
        if self.state != SUCCEEDED:
            lines.append(f"  replay: {self.replay_command}")
        return "\n".join(lines)


def _by_id(report: RunReport) -> dict[str, TaskResult]:
    return {r.task_id: r for r in report.results}


def _link(result: TaskResult) -> UpstreamLink:
    error = None if not result.error else classify_error(result.error)
    return UpstreamLink(result.task_id, result.state, result.reason, error)


def replay_command(plan_file: str = "plan.json", report_file: str = "report.json") -> str:
    """Return the review-first replay command for a report (each replay needs its own review and grants)."""
    return f"bricksmith run replay --plan {plan_file} --report-in {report_file} --out replay-plan.json"


def _chain(
    result: TaskResult, results: Mapping[str, TaskResult], depends: Mapping[str, tuple[str, ...]]
) -> tuple[list[UpstreamLink], list[str]]:
    """Walk upstream breadth-first through ``blocked_by`` (and plan dependencies); collect root causes."""
    seen: set[str] = {result.task_id}
    links: list[UpstreamLink] = []
    roots: list[str] = []
    queue = list(result.blocked_by or depends.get(result.task_id, ()))
    while queue:
        task_id = queue.pop(0)
        if task_id in seen or task_id not in results:
            continue
        seen.add(task_id)
        upstream = results[task_id]
        links.append(_link(upstream))
        if upstream.state in (FAILED, NOT_RUN) and not upstream.blocked_by:
            roots.append(task_id)
        queue.extend(upstream.blocked_by or ())
    return links, roots


def diagnose(
    report: RunReport,
    task_id: str,
    plan: Plan | None = None,
    *,
    plan_file: str = "plan.json",
    report_file: str = "report.json",
) -> Diagnosis:
    """Explain a failed, blocked, skipped or not-run task from the report (and plan dependencies when given)."""
    results = _by_id(report)
    result = results.get(task_id)
    if result is None:
        msg = f"task {task_id!r} is not in the report"
        raise KeyError(msg)
    depends = {e.task_id: e.depends_on for e in plan.entries} if plan else {}
    links, roots = _chain(result, results, depends) if result.state in (FAILED, BLOCKED, NOT_RUN) else ([], [])
    error_class: str | None = None
    if result.state == FAILED:
        record = describe_error(result.error or {"code": "unknown", "message": result.reason})
        error_class = record["class"]
        cause = f"{record['code']} ({record['type']}): {record['message']} after {result.attempts} attempt(s)"
        roots = [task_id]
    elif result.state == BLOCKED:
        cause = "dependency did not succeed: " + ", ".join(result.blocked_by)
    elif result.state == NOT_RUN:
        failed = report.ids(FAILED)
        cause = "dry run; nothing executed" if report.dry_run else "run stopped after a failure of " + ", ".join(failed)
        roots = [] if report.dry_run else list(failed)
    elif result.state == SKIPPED:
        cause = f"skipped by the plan: {result.reason}; its dependents assume its outputs exist"
    else:
        cause = "succeeded; nothing to diagnose"
    return Diagnosis(
        task_id, result.state, cause, error_class, tuple(roots), tuple(links),
        replay_command(plan_file, report_file),
        result.writes if result.state == FAILED else (),
    )  # fmt: skip


@dataclass(frozen=True)
class Recovery:
    """Partial-run recovery guidance derived from a report."""

    done: tuple[str, ...]
    redo: tuple[str, ...]
    at_risk: tuple[tuple[str, str, str], ...]
    steps: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "at_risk": [{"object": o, "operation": p, "task_id": t} for t, o, p in self.at_risk],
            "done": list(self.done),
            "redo": list(self.redo),
            "steps": list(self.steps),
        }


def recovery_guidance(report: RunReport, *, plan_file: str = "plan.json", report_file: str = "report.json") -> Recovery:
    """List what is done, what a replay will redo, writes whose state is uncertain and the ordered steps."""
    done = report.ids(SUCCEEDED, SKIPPED)
    redo = report.ids(FAILED, BLOCKED, NOT_RUN)
    risk = tuple((r.task_id, o, p) for r in report.results if r.state == FAILED for o, p in r.writes)
    steps: list[str] = []
    if not redo:
        steps.append("nothing to recover: no task failed, was blocked or was left unrun")
    else:
        steps.append("fix the root cause named in the diagnosis of each failed task")
        if risk:
            steps.append(
                "inspect the objects a failed task had already written; "
                + (
                    "append/merge writes may be applied twice on replay"
                    if any(p in _NON_IDEMPOTENT for _, _, p in risk)
                    else "overwrite writes are safe to repeat"
                )
            )
        steps.append(f"review the replay plan: {replay_command(plan_file, report_file)}")
        steps.append(
            "authorize protected targets again for the replay plan, then run `bricksmith run replay ... --apply`"
        )
        steps.append("succeeded tasks are marked done in the replay plan and are not recomputed")
    return Recovery(done, redo, risk, tuple(steps))


def resource_usage(report: RunReport, provider: UsageProvider | None = None) -> dict[str, object]:
    """Report measured duration and attempts per task; cost is ``unavailable`` unless a provider reports it.

    Nothing is estimated: without a provider (or when it returns nothing) the cost entry carries
    ``status: unavailable`` and a reason.
    """
    tasks: list[dict[str, object]] = []
    for r in report.results:
        reported = provider.usage(r.task_id) if provider else None
        if reported is None:
            reason = (
                "no usage provider configured" if provider is None else "the provider reported nothing for this task"
            )
            cost: dict[str, object] = {"reason": reason, "status": UNAVAILABLE}
        else:
            cost = {"status": REPORTED, **dict(reported)}
        tasks.append({"attempts": r.attempts, "cost": cost, "seconds": r.seconds, "task_id": r.task_id})
    return {"tasks": tasks, "total_seconds": round(sum(r.seconds for r in report.results), 3)}


def build_ops_report(
    report: RunReport,
    plan: Plan | None = None,
    *,
    provider: UsageProvider | None = None,
    plan_file: str = "plan.json",
    report_file: str = "report.json",
) -> dict[str, Any]:
    """Return the machine-readable operations report: summary, diagnoses of every problem, recovery and usage."""
    problems = report.ids(FAILED, BLOCKED)
    diagnoses = [diagnose(report, t, plan, plan_file=plan_file, report_file=report_file) for t in problems]
    counts = report.to_dict()["counts"]
    return {
        "counts": counts,
        "diagnoses": [d.to_dict() for d in diagnoses],
        "dry_run": report.dry_run,
        "ok": report.ok,
        "plan_fingerprint": report.plan_fingerprint,
        "recovery": recovery_guidance(report, plan_file=plan_file, report_file=report_file).to_dict(),
        "usage": resource_usage(report, provider),
        "version": OPS_REPORT_VERSION,
    }


def render_ops_report(data: Mapping[str, Any]) -> str:
    """Render :func:`build_ops_report` output as text for people."""
    counts = ", ".join(f"{k}={v}" for k, v in data["counts"].items() if v)
    lines = [f"run {'OK' if data['ok'] else 'FAILED'}{' (dry run)' if data['dry_run'] else ''}: {counts}",
             f"plan {data['plan_fingerprint']}"]  # fmt: skip
    for d in data["diagnoses"]:
        lines.append("")
        lines.append(f"{d['task_id']}: {d['state']}" + (f" [{d['error_class']}]" if d["error_class"] else ""))
        lines.append(f"  cause: {d['cause']}")
        if d["root_causes"]:
            lines.append("  root cause: " + ", ".join(d["root_causes"]))
        lines.extend(f"  upstream: {u['task_id']} [{u['state']}] {u['reason']}".rstrip() for u in d["upstream"])
        lines.extend(f"  possibly partial write: {w['object']} ({w['operation']})" for w in d["partial_writes"])
    recovery = data["recovery"]
    if recovery["redo"]:
        lines.append("")
        lines.append("recovery:")
        lines.extend(f"  {i}. {s}" for i, s in enumerate(recovery["steps"], 1))
    unavailable = sum(1 for t in data["usage"]["tasks"] if t["cost"]["status"] == UNAVAILABLE)
    lines.append("")
    lines.append(f"usage: {data['usage']['total_seconds']}s measured; cost unavailable for {unavailable} task(s)")
    return "\n".join(lines)


def to_json(data: Mapping[str, Any]) -> str:
    """Return sorted JSON for an operations report."""
    return json.dumps(data, indent=2, sort_keys=True) + "\n"
