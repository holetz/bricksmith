"""Bundle and job declarations: Python dataclasses or the ``[bundle]``/``[jobs]`` tables of ``bricksmith.toml``."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from bricksmith.errors import ConfigError
from bricksmith.params import validate_parameters
from bricksmith.tasks.project import PROJECT_FILE

if TYPE_CHECKING:
    from collections.abc import Mapping

NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
DEFAULT_ENVIRONMENT_VERSION = "3"
COMPUTE_SERVERLESS = "serverless"


@dataclass(frozen=True)
class ScopeOptions:
    """Per-catalog or per-schema choices; ``managed=None`` means the bundle-wide default."""

    managed: bool | None = None
    owner: str = ""
    comment: str = ""
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class VolumeSpec:
    """A volume declared in a schema scope (``catalog.schema``, logical names)."""

    key: str
    schema: str
    name: str = ""
    volume_type: str = "MANAGED"
    managed: bool = True
    owner: str = ""
    comment: str = ""
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepSpec:
    """A job task that is not a graph task: ``script``, ``notebook``, ``condition`` or ``run_job``.

    ``after`` lists task ids or step keys (``key:true``/``key:false`` selects a condition branch);
    ``before`` lists graph task ids that must wait for this step.
    """

    key: str
    kind: str
    path: str = ""
    parameters: tuple[str, ...] = ()
    after: tuple[str, ...] = ()
    before: tuple[str, ...] = ()
    op: str = "EQUAL_TO"
    left: str = ""
    right: str = ""
    job: str = ""
    timeout_seconds: int = 0
    max_retries: int = 0


@dataclass(frozen=True)
class JobSpec:
    """A job: which graph tasks it runs and how it is scheduled, notified and limited.

    ``contracts`` maps a producer task id outside this job to how its readiness is guaranteed:
    ``run_job:<job>`` (this job starts the producer's job first), ``trigger`` (this job's table
    trigger fires on the producer's output) or ``external`` (an acknowledged, validated input).
    """

    key: str
    select: tuple[str, ...]
    name: str = ""
    exclude: tuple[str, ...] = ()
    steps: tuple[StepSpec, ...] = ()
    contracts: Mapping[str, str] = field(default_factory=dict)
    schedule: str = ""
    timezone: str = "UTC"
    paused: bool = False
    trigger_tables: tuple[str, ...] = ()
    trigger_condition: str = "ANY_UPDATED"
    trigger_min_seconds: int = 0
    trigger_wait_seconds: int = 0
    notify_start: tuple[str, ...] = ()
    notify_success: tuple[str, ...] = ()
    notify_failure: tuple[str, ...] = ()
    run_as_user: str = ""
    run_as_service_principal: str = ""
    max_concurrent_runs: int = 1
    timeout_seconds: int = 0
    max_retries: int = 0
    parameters: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)
    tags: Mapping[str, str] = field(default_factory=dict)
    targets: tuple[str, ...] = ()
    environment: str = ""


@dataclass(frozen=True)
class BundleSettings:
    """Everything the bundle layer reads from the project file."""

    wheel: str = ""
    environment_version: str = DEFAULT_ENVIRONMENT_VERSION
    compute: str = COMPUTE_SERVERLESS
    manage_catalogs: bool = False
    manage_schemas: bool = True
    catalogs: Mapping[str, ScopeOptions] = field(default_factory=dict)
    schemas: Mapping[str, ScopeOptions] = field(default_factory=dict)
    volumes: tuple[VolumeSpec, ...] = ()
    jobs: tuple[JobSpec, ...] = ()
    preserved: tuple[str, ...] = ()


def _fail(where: str, message: str) -> ConfigError:
    return ConfigError(f"{PROJECT_FILE}: {where}: {message}")


def _table(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail(where, "must be a table")
    return cast("dict[str, Any]", value)


def _check_keys(table: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise _fail(where, f"unknown key(s) {', '.join(unknown)}")


def _text(table: Mapping[str, Any], key: str, where: str, default: str = "") -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise _fail(where, f"{key} must be a string")
    return value


def _flag(table: Mapping[str, Any], key: str, where: str, *, default: bool) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise _fail(where, f"{key} must be true or false")
    return value


def _count(table: Mapping[str, Any], key: str, where: str, default: int = 0) -> int:
    value = table.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise _fail(where, f"{key} must be a non-negative integer")
    return value


def _list(table: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    value = table.get(key, [])
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise _fail(where, f"{key} must be a list of non-empty strings")
    return tuple(value)


def _strings(table: Mapping[str, Any], key: str, where: str) -> dict[str, str]:
    value = _table(table.get(key, {}), f"{where}.{key}")
    if not all(isinstance(item, str) for item in value.values()):
        raise _fail(where, f"{key} values must be strings")
    return dict(sorted(value.items()))


_SCOPE_KEYS = {"managed", "owner", "comment", "targets"}


def _scope(raw: object, where: str) -> ScopeOptions:
    table = _table(raw, where)
    _check_keys(table, _SCOPE_KEYS, where)
    managed = table.get("managed")
    if managed is not None and not isinstance(managed, bool):
        raise _fail(where, "managed must be true or false")
    return ScopeOptions(
        managed, _text(table, "owner", where), _text(table, "comment", where), _list(table, "targets", where)
    )


def _volume(key: str, raw: object) -> VolumeSpec:
    where = f"[bundle.volumes.{key}]"
    table = _table(raw, where)
    _check_keys(table, _SCOPE_KEYS | {"schema", "name", "volume_type"}, where)
    if not _text(table, "schema", where):
        raise _fail(where, "schema (logical catalog.schema) is required")
    return VolumeSpec(
        key=key,
        schema=_text(table, "schema", where),
        name=_text(table, "name", where),
        volume_type=_text(table, "volume_type", where, "MANAGED"),
        managed=_flag(table, "managed", where, default=True),
        owner=_text(table, "owner", where),
        comment=_text(table, "comment", where),
        targets=_list(table, "targets", where),
    )


_STEP_KEYS = {"key", "kind", "path", "parameters", "after", "before", "op", "left", "right", "job"}
_STEP_KEYS |= {"timeout_seconds", "max_retries"}


def _step(raw: object, where: str) -> StepSpec:
    table = _table(raw, where)
    _check_keys(table, _STEP_KEYS, where)
    return StepSpec(
        key=_text(table, "key", where),
        kind=_text(table, "kind", where),
        path=_text(table, "path", where),
        parameters=_list(table, "parameters", where),
        after=_list(table, "after", where),
        before=_list(table, "before", where),
        op=_text(table, "op", where, "EQUAL_TO"),
        left=_text(table, "left", where),
        right=_text(table, "right", where),
        job=_text(table, "job", where),
        timeout_seconds=_count(table, "timeout_seconds", where),
        max_retries=_count(table, "max_retries", where),
    )


_JOB_KEYS = {"name", "select", "exclude", "steps", "contracts", "schedule", "timezone", "paused", "trigger_tables"}
_JOB_KEYS |= {"trigger_condition", "trigger_min_seconds", "trigger_wait_seconds", "notify_start", "notify_success"}
_JOB_KEYS |= {"notify_failure", "run_as_user", "run_as_service_principal", "max_concurrent_runs", "timeout_seconds"}
_JOB_KEYS |= {"max_retries", "parameters", "tags", "targets", "environment"}


def _job(key: str, raw: object) -> JobSpec:
    where = f"[jobs.{key}]"
    table = _table(raw, where)
    _check_keys(table, _JOB_KEYS, where)
    steps = table.get("steps", [])
    if not isinstance(steps, list):
        raise _fail(where, "steps must be an array of tables ([[jobs.<key>.steps]])")
    parameters = _table(table.get("parameters", {}), f"{where}.parameters")
    return JobSpec(
        key=key,
        name=_text(table, "name", where),
        select=_list(table, "select", where),
        exclude=_list(table, "exclude", where),
        steps=tuple(_step(item, f"{where}.steps[{i}]") for i, item in enumerate(steps)),
        contracts=_strings(table, "contracts", where),
        schedule=_text(table, "schedule", where),
        timezone=_text(table, "timezone", where, "UTC"),
        paused=_flag(table, "paused", where, default=False),
        trigger_tables=_list(table, "trigger_tables", where),
        trigger_condition=_text(table, "trigger_condition", where, "ANY_UPDATED"),
        trigger_min_seconds=_count(table, "trigger_min_seconds", where),
        trigger_wait_seconds=_count(table, "trigger_wait_seconds", where),
        notify_start=_list(table, "notify_start", where),
        notify_success=_list(table, "notify_success", where),
        notify_failure=_list(table, "notify_failure", where),
        run_as_user=_text(table, "run_as_user", where),
        run_as_service_principal=_text(table, "run_as_service_principal", where),
        max_concurrent_runs=_count(table, "max_concurrent_runs", where, 1),
        timeout_seconds=_count(table, "timeout_seconds", where),
        max_retries=_count(table, "max_retries", where),
        parameters=parameters,
        tags=_strings(table, "tags", where),
        targets=_list(table, "targets", where),
        environment=_text(table, "environment", where),
    )


_BUNDLE_KEYS = {"wheel", "environment_version", "compute", "manage_catalogs", "manage_schemas", "catalogs", "schemas"}
_BUNDLE_KEYS |= {"volumes", "preserved"}


def parse_settings(data: Mapping[str, Any]) -> BundleSettings:
    """Build settings from the parsed project file; raise :class:`ConfigError` on any malformed entry."""
    bundle = _table(data.get("bundle", {}), "[bundle]")
    _check_keys(bundle, _BUNDLE_KEYS, "[bundle]")
    jobs = _table(data.get("jobs", {}), "[jobs]")
    for key in jobs:
        if not NAME.fullmatch(key):
            where = "[jobs]"
            raise _fail(where, f"job key {key!r} must match {NAME.pattern}")
    for key in (*_table(bundle.get("volumes", {}), "[bundle.volumes]"),):
        if not NAME.fullmatch(key):
            where = "[bundle.volumes]"
            raise _fail(where, f"volume key {key!r} must match {NAME.pattern}")
    return BundleSettings(
        wheel=_text(bundle, "wheel", "[bundle]"),
        environment_version=_text(bundle, "environment_version", "[bundle]", DEFAULT_ENVIRONMENT_VERSION),
        compute=_text(bundle, "compute", "[bundle]", COMPUTE_SERVERLESS),
        manage_catalogs=_flag(bundle, "manage_catalogs", "[bundle]", default=False),
        manage_schemas=_flag(bundle, "manage_schemas", "[bundle]", default=True),
        catalogs={
            key: _scope(raw, f"[bundle.catalogs.{key}]")
            for key, raw in sorted(_table(bundle.get("catalogs", {}), "[bundle.catalogs]").items())
        },
        schemas={
            key: _scope(raw, f"[bundle.schemas.{key}]")
            for key, raw in sorted(_table(bundle.get("schemas", {}), "[bundle.schemas]").items())
        },
        volumes=tuple(
            _volume(key, raw) for key, raw in sorted(_table(bundle.get("volumes", {}), "[bundle.volumes]").items())
        ),
        jobs=tuple(_job(key, raw) for key, raw in sorted(jobs.items())),
        preserved=_list(bundle, "preserved", "[bundle]"),
    )


def load_settings(root: Path | str = ".") -> BundleSettings:
    """Read the ``[bundle]`` and ``[jobs]`` tables of ``bricksmith.toml`` under ``root`` (defaults when absent)."""
    path = Path(root) / PROJECT_FILE
    if not path.is_file():
        return BundleSettings()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        message = f"{PROJECT_FILE}: {error}"
        raise ConfigError(message) from error
    return parse_settings(data)


def checked_parameters(job: JobSpec) -> dict[str, str]:
    """Return the job parameters validated as scalars and rendered as text (job parameters are strings)."""
    values = validate_parameters(dict(job.parameters))
    return {
        key: ("" if value is None else str(value).lower() if isinstance(value, bool) else str(value))
        for key, value in values.items()
    }
