"""The thin adapter from the resource model to ``databricks-bundles`` objects.

``databricks-bundles`` (the ``bundle`` extra) is imported inside the functions that need it, so the
rest of :mod:`bricksmith.dab` works without it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from bricksmith.dab.build import build_resources
from bricksmith.dab.jobs import ENTRY_POINT, PACKAGE
from bricksmith.dab.model import KIND_BRICKSMITH, KIND_CONDITION, KIND_NOTEBOOK, KIND_RUN_JOB, KIND_SCRIPT
from bricksmith.errors import MissingExtraError, ValidationError
from bricksmith.tasks.project import PROJECT_FILE

if TYPE_CHECKING:
    from databricks.bundles.core import Bundle, Resources  # pyright: ignore[reportMissingImports]

    from bricksmith.dab.model import JobResource, JobTask, ResourceSet

ENVIRONMENT_KEY = "bricksmith"


def _require() -> None:
    try:
        import databricks.bundles  # noqa: F401, PLC0415 - presence check only  # pyright: ignore[reportMissingImports]
    except ImportError as error:
        msg = "bundle resources need databricks-bundles; install it with: pip install 'bricksmith[bundle]'"
        raise MissingExtraError(msg) from error


def _task(task: JobTask) -> dict[str, Any]:
    """Return the dictionary form of one job task (the shape ``Task.from_dict`` reads)."""
    fields: dict[str, Any] = {"task_key": task.key}
    if task.depends_on:
        fields["depends_on"] = [
            {"task_key": d.key, **({"outcome": d.outcome} if d.outcome else {})} for d in task.depends_on
        ]
    if task.kind not in {KIND_CONDITION, KIND_RUN_JOB}:
        fields["environment_key"] = ENVIRONMENT_KEY
    if task.timeout_seconds:
        fields["timeout_seconds"] = task.timeout_seconds
    if task.max_retries:
        fields["max_retries"] = task.max_retries
    if task.kind == KIND_BRICKSMITH:
        fields["python_wheel_task"] = {
            "package_name": PACKAGE,
            "entry_point": ENTRY_POINT,
            "parameters": list(task.parameters),
        }
    elif task.kind == KIND_SCRIPT:
        fields["spark_python_task"] = {"python_file": task.path, "parameters": list(task.parameters)}
    elif task.kind == KIND_NOTEBOOK:
        base = dict(item.split("=", 1) for item in task.parameters if "=" in item)
        fields["notebook_task"] = {"notebook_path": task.path, "base_parameters": base}
    elif task.kind == KIND_CONDITION:
        fields["condition_task"] = {"op": task.op, "left": task.left, "right": task.right}
    elif task.kind == KIND_RUN_JOB:
        fields["run_job_task"] = {"job_id": f"${{resources.jobs.{task.job}.id}}"}
    else:
        msg = f"unsupported task kind {task.kind!r}"
        raise ValidationError(msg)
    return fields


def _pause(paused: bool) -> str:  # noqa: FBT001 - a plain flag from the model
    return "PAUSED" if paused else "UNPAUSED"


def job_dict(job: JobResource) -> dict[str, Any]:
    """Return the dictionary form of a job (the shape ``Job.from_dict`` reads)."""
    data: dict[str, Any] = {
        "name": job.name,
        "max_concurrent_runs": job.max_concurrent_runs,
        "tasks": [_task(task) for task in job.tasks],
        "environments": [
            {
                "environment_key": ENVIRONMENT_KEY,
                "spec": {
                    "environment_version": job.environment.version,
                    "dependencies": list(job.environment.dependencies),
                },
            }
        ],
    }
    if job.timeout_seconds:
        data["timeout_seconds"] = job.timeout_seconds
    if job.schedule:
        data["schedule"] = {
            "quartz_cron_expression": job.schedule.cron,
            "timezone_id": job.schedule.timezone,
            "pause_status": _pause(job.schedule.paused),
        }
    if job.trigger:
        table_update: dict[str, Any] = {"table_names": list(job.trigger.tables), "condition": job.trigger.condition}
        if job.trigger.min_seconds:
            table_update["min_time_between_triggers_seconds"] = job.trigger.min_seconds
        if job.trigger.wait_seconds:
            table_update["wait_after_last_change_seconds"] = job.trigger.wait_seconds
        data["trigger"] = {"pause_status": _pause(job.trigger.paused), "table_update": table_update}
    if job.notifications:
        events = {
            "on_start": job.notifications.on_start,
            "on_success": job.notifications.on_success,
            "on_failure": job.notifications.on_failure,
        }
        data["email_notifications"] = {name: list(value) for name, value in events.items() if value}
    if job.run_as:
        data["run_as"] = (
            {"user_name": job.run_as.user}
            if job.run_as.user
            else {"service_principal_name": job.run_as.service_principal}
        )
    if job.parameters:
        data["parameters"] = [{"name": k, "default": v} for k, v in sorted(job.parameters.items())]
    if job.tags:
        data["tags"] = dict(job.tags)
    return data


def _grants(owner: str) -> list[dict[str, Any]]:
    return [{"principal": owner, "privileges": ["ALL_PRIVILEGES"]}] if owner else []


def to_resources(resources: ResourceSet, project: Path | str = ".") -> Resources:
    """Convert a resource set to native bundle ``Resources``; unmanaged entries are referenced only.

    Relative paths in tasks resolve against ``project`` (the bundle root), not against this module.
    """
    _require()
    from databricks.bundles.catalogs import Catalog  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
    from databricks.bundles.core import Location, Resources  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
    from databricks.bundles.jobs import Job  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
    from databricks.bundles.schemas import Schema  # noqa: PLC0415  # pyright: ignore[reportMissingImports]
    from databricks.bundles.volumes import Volume  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    native = Resources()
    where = Location(file=str(Path(project).resolve() / PROJECT_FILE), line=1, column=1)
    for catalog in resources.catalogs:
        if catalog.managed:
            data = {"name": catalog.name, "comment": catalog.comment or None, "grants": _grants(catalog.owner)}
            native.add_catalog(catalog.key, Catalog.from_dict(cast("Any", _present(data))), location=where)
    for schema in resources.schemas:
        if schema.managed:
            data = {
                "catalog_name": schema.catalog,
                "name": schema.name,
                "comment": schema.comment or None,
                "grants": _grants(schema.owner),
            }
            native.add_schema(schema.key, Schema.from_dict(cast("Any", _present(data))), location=where)
    for volume in resources.volumes:
        if volume.managed:
            data = {
                "catalog_name": volume.catalog,
                "schema_name": volume.schema,
                "name": volume.name,
                "volume_type": volume.volume_type,
                "comment": volume.comment or None,
                "grants": _grants(volume.owner),
            }
            native.add_volume(volume.key, Volume.from_dict(cast("Any", _present(data))), location=where)
    for job in resources.jobs:
        native.add_job(job.key, Job.from_dict(cast("Any", job_dict(job))), location=where)
    return native


def _present(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value not in (None, [], "")}


def load_resources(bundle: Bundle, project: Path | str = ".") -> Resources:
    """Return the project's native bundle resources for ``bundle.target``.

    Reference this from ``databricks.yml`` as ``python: resources: - "bricksmith.dab:load_resources"``.
    The project is the bundle root (the working directory the CLI runs in). An invalid project raises
    :class:`ValidationError` listing every error, so nothing is deployed. Only resources are added;
    hand-written resources are never read, changed or removed.
    """
    _require()
    assembly = build_resources(project, target=bundle.target)
    if not assembly.ok:
        details = "; ".join(item.text() for item in assembly.errors)
        msg = f"bundle resources are invalid ({len(assembly.errors)} error(s)): {details}"
        raise ValidationError(msg)
    return to_resources(assembly.resources, project)
