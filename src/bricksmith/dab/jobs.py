"""Job assembly and validation: graph tasks plus extra steps become ordered, checked job tasks."""

from __future__ import annotations

import fnmatch
import hashlib
import heapq
import re
from typing import TYPE_CHECKING

from bricksmith.dab.model import (
    JOB_TASK_KINDS,
    KIND_BRICKSMITH,
    KIND_CONDITION,
    KIND_NOTEBOOK,
    KIND_RUN_JOB,
    KIND_SCRIPT,
    Dependency,
    Environment,
    JobResource,
    JobTask,
    Notifications,
    RunIdentity,
    Schedule,
    TableTrigger,
)
from bricksmith.dab.spec import COMPUTE_SERVERLESS, JobSpec, StepSpec, checked_parameters
from bricksmith.errors import ValidationError
from bricksmith.tasks.diagnostics import ERROR, WARNING, Diagnostic

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from bricksmith.dab.spec import BundleSettings
    from bricksmith.graph import TaskGraph, TaskNode

#: The wheel's console script that runs ``bricksmith.runner.entrypoint:run_entrypoint``.
ENTRY_POINT = "bricksmith-run"
PACKAGE = "bricksmith"
PROJECT_ROOT_REF = "${workspace.file_path}"
TARGET_REF = "${bundle.target}"
CONDITION_OPS = ("EQUAL_TO", "GREATER_THAN", "GREATER_THAN_OR_EQUAL", "LESS_THAN", "LESS_THAN_OR_EQUAL", "NOT_EQUAL")
TRIGGER_CONDITIONS = ("ANY_UPDATED", "ALL_UPDATED")
STEP_KINDS = (KIND_SCRIPT, KIND_NOTEBOOK, KIND_CONDITION, KIND_RUN_JOB)
_TASK_KEY = re.compile(r"[A-Za-z0-9_-]{1,100}")
_STEP_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,99}")
_TABLE = re.compile(r"[^.\s]+\.[^.\s]+\.[^.\s]+")
_QUARTZ_FIELDS = (6, 7)
_TASK_KEY_LIMIT = 100


def task_key(task_id: str) -> str:
    """Return the job task key for a graph task id (stable; hashed when it has to be shortened)."""
    key = re.sub(r"[^A-Za-z0-9_-]+", "_", task_id).strip("_")
    if len(key) > _TASK_KEY_LIMIT:
        key = key[:90] + "_" + hashlib.sha256(task_id.encode()).hexdigest()[:8]
    return key


def literal_pattern(task_id: str) -> str:
    """Escape ``fnmatch`` metacharacters so a task id selects exactly itself."""
    return re.sub(r"([*?\[])", r"[\1]", task_id)


def _matches(task_id: str, patterns: Iterable[str]) -> bool:
    bare = task_id.split("[", 1)[0]
    return any(fnmatch.fnmatchcase(task_id, p) or fnmatch.fnmatchcase(bare, p) for p in patterns)


class Findings:
    """Collects diagnostics for one assembly."""

    def __init__(self) -> None:
        """Start empty."""
        self.items: list[Diagnostic] = []

    def error(self, code: str, subject: str, message: str) -> None:
        """Record an error."""
        self.items.append(Diagnostic(code, ERROR, subject, message))

    def warning(self, code: str, subject: str, message: str) -> None:
        """Record a warning."""
        self.items.append(Diagnostic(code, WARNING, subject, message))


class _Selection:
    """The graph tasks each job runs, and which job owns each task."""

    def __init__(self, graph: TaskGraph, specs: tuple[JobSpec, ...], findings: Findings) -> None:
        self.members: dict[str, list[TaskNode]] = {}
        self.owner: dict[str, str] = {}
        enabled = [node for node in graph.tasks if node.enabled]
        for spec in specs:
            chosen: list[TaskNode] = []
            for pattern in spec.select:
                found = [node for node in enabled if _matches(node.id, [pattern])]
                if not found:
                    findings.error("unknown_task", spec.key, f"select pattern {pattern!r} matches no enabled task")
                chosen.extend(found)
            kept = [node for node in {n.id: n for n in chosen}.values() if not _matches(node.id, spec.exclude)]
            self.members[spec.key] = sorted(kept, key=lambda node: node.id)
            for node in kept:
                if node.id in self.owner:
                    message = f"task {node.id} is selected by jobs {self.owner[node.id]} and {spec.key}"
                    findings.error("duplicate_task", node.id, message + "; share it with a run_job contract instead")
                else:
                    self.owner[node.id] = spec.key


def _contract_kind(value: str) -> tuple[str, str]:
    kind, _, argument = value.partition(":")
    return kind, argument


def _check_options(spec: JobSpec, settings: BundleSettings, findings: Findings) -> None:  # noqa: C901, PLR0912
    key = spec.key
    if settings.compute != COMPUTE_SERVERLESS:
        message = f"compute {settings.compute!r} is not supported; jobs run on serverless compute"
        findings.error("unsupported_compute", key, message)
    if not re.fullmatch(r"\d+", settings.environment_version):
        findings.error(
            "unsupported_option", key, f"environment_version {settings.environment_version!r} is not a number"
        )
    if spec.schedule and spec.trigger_tables:
        findings.error("incompatible_trigger", key, "a job has either a schedule or a table-update trigger, not both")
    if spec.schedule and len(spec.schedule.split()) not in _QUARTZ_FIELDS:
        findings.error("invalid_schedule", key, f"schedule {spec.schedule!r} must be a Quartz cron expression")
    if spec.trigger_tables:
        for table in spec.trigger_tables:
            if not _TABLE.fullmatch(table):
                findings.error("invalid_trigger", key, f"trigger table {table!r} must be catalog.schema.table")
        if spec.trigger_condition not in TRIGGER_CONDITIONS:
            findings.error("invalid_trigger", key, f"trigger_condition must be one of {', '.join(TRIGGER_CONDITIONS)}")
    if spec.run_as_user and spec.run_as_service_principal:
        findings.error("incompatible_identity", key, "run_as_user and run_as_service_principal are exclusive")
    if spec.max_concurrent_runs < 1:
        findings.error("invalid_limit", key, "max_concurrent_runs must be at least 1")
    for address in (*spec.notify_start, *spec.notify_success, *spec.notify_failure):
        if "@" not in address:
            findings.error("invalid_notification", key, f"notification recipient {address!r} is not an address")
    try:
        checked_parameters(spec)
    except ValidationError as error:
        findings.error("invalid_parameter", key, error.message)


def _check_step(spec: JobSpec, step: StepSpec, jobs: Mapping[str, JobSpec], findings: Findings) -> None:
    subject = f"{spec.key}.{step.key}"
    if step.kind not in STEP_KINDS:
        known = ", ".join(JOB_TASK_KINDS)
        findings.error(
            "unsupported_task_kind", subject, f"task kind {step.kind!r} is not supported; use one of {known}"
        )
        return
    if not _STEP_KEY.fullmatch(step.key):
        findings.error("invalid_step", subject, f"step key {step.key!r} must match {_STEP_KEY.pattern}")
    if step.kind in {KIND_SCRIPT, KIND_NOTEBOOK} and not step.path:
        findings.error("invalid_step", subject, f"{step.kind} step needs a path")
    if step.kind == KIND_CONDITION:
        if step.op not in CONDITION_OPS:
            findings.error(
                "invalid_step", subject, f"condition op {step.op!r} must be one of {', '.join(CONDITION_OPS)}"
            )
        if not step.left or not step.right:
            findings.error("invalid_step", subject, "condition step needs left and right")
    if step.kind == KIND_RUN_JOB:
        if step.job not in jobs:
            findings.error("invalid_step", subject, f"run_job step references unknown job {step.job!r}")
        elif step.job == spec.key:
            findings.error("job_cycle", subject, "a job cannot run itself")


class _JobBuilder:
    """Assembles and checks one job."""

    def __init__(  # noqa: PLR0913, PLR0917 - the collaborators of one assembly
        self,
        spec: JobSpec,
        graph: TaskGraph,
        selection: _Selection,
        specs: Mapping[str, JobSpec],
        settings: BundleSettings,
        findings: Findings,
    ) -> None:
        self.spec = spec
        self.graph = graph
        self.selection = selection
        self.specs = specs
        self.settings = settings
        self.findings = findings
        self.members = selection.members[spec.key]
        self.ids = {node.id for node in self.members}
        self.keys: dict[str, str] = {}
        self.deps: dict[str, list[Dependency]] = {}
        self.extra: dict[str, JobTask] = {}

    # -- keys ---------------------------------------------------------------------------------
    def assign_keys(self) -> None:
        taken: dict[str, str] = {}
        for node in self.members:
            key = task_key(node.id)
            if not _TASK_KEY.fullmatch(key):
                self.findings.error("invalid_step", node.id, f"cannot derive a job task key from {node.id!r}")
            if key in taken:
                self.findings.error("key_collision", node.id, f"tasks {taken[key]} and {node.id} map to key {key!r}")
            taken[key] = node.id
            self.keys[node.id] = key
        for step in self.spec.steps:
            if step.key in taken:
                self.findings.error("key_collision", f"{self.spec.key}.{step.key}", f"step key {step.key!r} is taken")
            taken[step.key] = step.key

    def reference(self, item: str, subject: str) -> Dependency | None:
        name, _, outcome = item.partition(":")
        if name in self.keys:
            return Dependency(self.keys[name], outcome)
        if any(step.key == name for step in self.spec.steps):
            return Dependency(name, outcome)
        self.findings.error("unknown_reference", subject, f"{item!r} is neither a task of this job nor a step")
        return None

    def add_dep(self, key: str, dependency: Dependency | None) -> None:
        if dependency and dependency not in self.deps.setdefault(key, []):
            self.deps[key].append(dependency)

    # -- edges --------------------------------------------------------------------------------
    def contract_for(self, producer: str) -> str:
        contracts = self.spec.contracts
        return contracts.get(producer) or next((v for k, v in contracts.items() if _matches(producer, [k])), "")

    def link(self, node: TaskNode, producer_id: str) -> None:
        subject = f"{self.spec.key}:{node.id}"
        if producer_id in self.ids:
            self.add_dep(node.id, Dependency(self.keys[producer_id]))
            return
        producer = self.graph.task(producer_id)
        owner = self.selection.owner.get(producer_id)
        contract = self.contract_for(producer_id)
        if not producer.enabled:
            self.findings.error("disabled_producer", subject, f"{node.id} needs {producer_id}, which is disabled")
            return
        if not contract:
            if producer.static:
                return
            if owner:
                message = (
                    f"{node.id} reads {producer_id}, which runs in job {owner}; declare a run_job or trigger contract"
                )
                self.findings.error("cross_job_edge", subject, message)
            else:
                message = (
                    f"{node.id} needs {producer_id}, which no job selected; "
                    "add it to the selection or declare an external contract"
                )
                self.findings.error("missing_producer", subject, message)
            return
        kind, argument = _contract_kind(contract)
        if kind == "run_job":
            self.run_job_contract(node, producer_id, owner, argument, subject)
        elif kind == "trigger":
            self.trigger_contract(producer_id, subject)
        elif kind == "external":
            if owner:
                message = f"{producer_id} runs in job {owner}; an external contract cannot cover another bricksmith job"
                self.findings.error("cross_job_edge", subject, message)
        else:
            self.findings.error(
                "contract_invalid", subject, f"contract {contract!r} must be run_job:<job>, trigger or external"
            )

    def run_job_contract(self, node: TaskNode, producer_id: str, owner: str | None, job: str, subject: str) -> None:
        target = self.specs.get(job)
        if target is None or job == self.spec.key:
            self.findings.error(
                "contract_invalid", subject, f"run_job contract names {job!r}, which is not another job"
            )
            return
        if owner != job:
            where = f"runs in job {owner}" if owner else "no job selected"
            message = f"run_job:{job} does not cover {producer_id}, which {where}"
            self.findings.error("contract_invalid", subject, message)
            return
        if target.targets and not set(self.spec.targets or target.targets) <= set(target.targets):
            self.findings.error(
                "target_mismatch", subject, f"job {job} is not deployed to every target of {self.spec.key}"
            )
        existing = next((s.key for s in self.spec.steps if s.kind == KIND_RUN_JOB and s.job == job), "")
        step_key = existing or f"run_{job}"
        if not existing and step_key not in self.extra:
            self.extra[step_key] = JobTask(step_key, KIND_RUN_JOB, job=job)
        self.add_dep(node.id, Dependency(step_key))

    def trigger_contract(self, producer_id: str, subject: str) -> None:
        produced = {output.physical.casefold() for output in self.graph.outputs if producer_id in output.producers}
        watched = {table.casefold() for table in self.spec.trigger_tables}
        if not produced & watched:
            listed = ", ".join(sorted(produced)) or "none"
            message = f"trigger contract for {producer_id} needs a trigger table among its outputs ({listed})"
            self.findings.error("contract_invalid", subject, message)

    # -- assembly -----------------------------------------------------------------------------
    def check_contracts(self) -> None:
        for producer_id, contract in self.spec.contracts.items():
            if producer_id in self.ids:
                self.findings.warning("contract_unused", self.spec.key, f"contract for {producer_id} is inside the job")
            elif not any(n.id == producer_id or _matches(n.id, [producer_id]) for n in self.graph.tasks):
                message = f"contract names unknown task {producer_id!r} ({contract})"
                self.findings.error("contract_invalid", self.spec.key, message)

    def step_edges(self) -> dict[str, list[Dependency]]:
        step_deps: dict[str, list[Dependency]] = {}
        for step in self.spec.steps:
            subject = f"{self.spec.key}.{step.key}"
            for item in step.after:
                dependency = self.reference(item, subject)
                if dependency:
                    step_deps.setdefault(step.key, []).append(dependency)
            for target_id in step.before:
                if target_id in self.keys:
                    self.add_dep(target_id, Dependency(step.key))
                else:
                    message = f"before names {target_id!r}, which is not in this job"
                    self.findings.error("unknown_reference", subject, message)
        return step_deps

    def tasks(self) -> tuple[JobTask, ...]:
        for node in self.members:
            for producer_id in self.graph.dependencies(node.id):
                self.link(node, producer_id)
        self.check_contracts()
        step_deps = self.step_edges()
        built = {self.keys[node.id]: self.bricksmith_task(node) for node in self.members}
        for step in self.spec.steps:
            built[step.key] = self.step_task(step, step_deps.get(step.key, []))
        built.update(self.extra)
        return self.order(built)

    def bricksmith_task(self, node: TaskNode) -> JobTask:
        arguments = ["--project", PROJECT_ROOT_REF, "--target", TARGET_REF]
        if self.spec.environment:
            arguments += ["--env", self.spec.environment]
        arguments += ["--task", literal_pattern(node.id), "--no-upstream", "--partial"]
        return JobTask(
            key=self.keys[node.id],
            kind=KIND_BRICKSMITH,
            task_id=node.id,
            depends_on=tuple(self.deps.get(node.id, ())),
            parameters=tuple(arguments),
            max_retries=self.spec.max_retries,
        )

    def step_task(self, step: StepSpec, dependencies: list[Dependency]) -> JobTask:
        return JobTask(
            key=step.key,
            kind=step.kind,
            depends_on=tuple(dependencies),
            path=step.path,
            parameters=step.parameters,
            op=step.op if step.kind == KIND_CONDITION else "",
            left=step.left,
            right=step.right,
            job=step.job,
            timeout_seconds=step.timeout_seconds,
            max_retries=step.max_retries,
        )

    def order(self, built: dict[str, JobTask]) -> tuple[JobTask, ...]:
        """Return the tasks in a deterministic topological order; a cycle is an error."""
        waiting = {key: {d.key for d in task.depends_on if d.key in built} for key, task in built.items()}
        ready = [key for key, needs in waiting.items() if not needs]
        heapq.heapify(ready)
        ordered: list[JobTask] = []
        while ready:
            current = heapq.heappop(ready)
            ordered.append(built[current])
            del waiting[current]
            for key, needs in waiting.items():
                if current in needs:
                    needs.discard(current)
                    if not needs:
                        heapq.heappush(ready, key)
        if waiting:
            self.findings.error(
                "step_cycle", self.spec.key, f"tasks depend on each other: {', '.join(sorted(waiting))}"
            )
            ordered.extend(built[key] for key in sorted(waiting))
        return tuple(ordered)

    def resource(self, tasks: tuple[JobTask, ...]) -> JobResource:
        spec = self.spec
        notifications = Notifications(spec.notify_start, spec.notify_success, spec.notify_failure)
        has_notice = any((notifications.on_start, notifications.on_success, notifications.on_failure))
        try:
            parameters = checked_parameters(spec)
        except ValidationError:
            parameters = {}
        wheel = (self.settings.wheel,) if self.settings.wheel else ()
        return JobResource(
            key=spec.key,
            name=spec.name or spec.key,
            tasks=tasks,
            environment=Environment(self.settings.environment_version, wheel),
            schedule=Schedule(spec.schedule, spec.timezone, spec.paused) if spec.schedule else None,
            trigger=(
                TableTrigger(
                    tuple(sorted(spec.trigger_tables)),
                    spec.trigger_condition,
                    spec.trigger_min_seconds,
                    spec.trigger_wait_seconds,
                    spec.paused,
                )
                if spec.trigger_tables
                else None
            ),
            notifications=notifications if has_notice else None,
            run_as=(
                RunIdentity(spec.run_as_user, spec.run_as_service_principal)
                if spec.run_as_user or spec.run_as_service_principal
                else None
            ),
            max_concurrent_runs=spec.max_concurrent_runs,
            timeout_seconds=spec.timeout_seconds,
            parameters=parameters,
            tags=dict(spec.tags),
            targets=spec.targets,
        )


def _job_cycles(specs: Mapping[str, JobSpec], jobs: list[JobResource], findings: Findings) -> None:
    edges = {job.key: {t.job for t in job.tasks if t.kind == KIND_RUN_JOB and t.job in specs} for job in jobs}
    state: dict[str, int] = {}

    def visit(key: str, path: tuple[str, ...]) -> None:
        if state.get(key) == 1:
            findings.error("job_cycle", key, f"jobs start each other: {' -> '.join((*path, key))}")
            return
        if state.get(key) == 2:  # noqa: PLR2004 - 1 visiting, 2 done
            return
        state[key] = 1
        for nxt in sorted(edges.get(key, ())):
            visit(nxt, (*path, key))
        state[key] = 2

    for key in sorted(edges):
        visit(key, ())


def assemble_jobs(
    graph: TaskGraph, specs: tuple[JobSpec, ...], settings: BundleSettings, findings: Findings
) -> tuple[JobResource, ...]:
    """Assemble and validate every declared job; problems go to ``findings`` (nothing is raised)."""
    by_key = {spec.key: spec for spec in specs}
    selection = _Selection(graph, specs, findings)
    jobs: list[JobResource] = []
    for spec in specs:
        _check_options(spec, settings, findings)
        for step in spec.steps:
            _check_step(spec, step, by_key, findings)
        builder = _JobBuilder(spec, graph, selection, by_key, settings, findings)
        builder.assign_keys()
        tasks = builder.tasks()
        if not tasks:
            findings.error("empty_job", spec.key, "the job selects no task and declares no step")
        if any(t.kind == KIND_BRICKSMITH for t in tasks) and not settings.wheel:
            findings.error(
                "missing_wheel", spec.key, "bricksmith tasks need [bundle] wheel (the wheel is the dependency)"
            )
        jobs.append(builder.resource(tasks))
    names: dict[str, str] = {}
    for job in jobs:
        if job.name in names:
            findings.error(
                "name_collision", job.key, f"jobs {names[job.name]} and {job.key} share the name {job.name!r}"
            )
        names[job.name] = job.key
    _job_cycles(by_key, jobs, findings)
    return tuple(jobs)
