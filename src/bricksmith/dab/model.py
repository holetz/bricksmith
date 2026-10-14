"""The resource model: plain dataclasses with a stable, serializable snapshot.

Nothing here imports Spark or ``databricks-bundles``; :mod:`bricksmith.dab.adapter` converts a
:class:`ResourceSet` to native bundle objects. ``to_dict``/``from_dict`` are exact inverses, and
the JSON form is sorted so a snapshot can be committed and diffed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import types
import typing
from dataclasses import dataclass, field
from typing import Any, Self

from bricksmith.errors import ValidationError

SNAPSHOT_VERSION = 1

#: Task kinds a job can contain. ``bricksmith`` runs one graph task through the common entrypoint.
KIND_BRICKSMITH = "bricksmith"
KIND_SCRIPT = "script"
KIND_NOTEBOOK = "notebook"
KIND_CONDITION = "condition"
KIND_RUN_JOB = "run_job"
JOB_TASK_KINDS = (KIND_BRICKSMITH, KIND_SCRIPT, KIND_NOTEBOOK, KIND_CONDITION, KIND_RUN_JOB)


def _plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in sorted(value.items())}
    return value


def _build(hint: object, value: object) -> object:
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin in (typing.Union, types.UnionType):
        if value is None:
            return None
        inner = next(arg for arg in args if arg is not type(None))
        return _build(inner, value)
    if origin is tuple:
        assert isinstance(value, list)  # noqa: S101 - narrowing for the type checker
        return tuple(_build(args[0], item) for item in value)
    if origin is dict:
        assert isinstance(value, dict)  # noqa: S101
        return {key: _build(args[1], item) for key, item in value.items()}
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        assert isinstance(value, dict)  # noqa: S101
        hints = typing.get_type_hints(hint)
        known = {f.name for f in dataclasses.fields(hint)}
        return hint(**{key: _build(hints[key], item) for key, item in value.items() if key in known})
    return value


class _Record:
    """Mixin giving a frozen dataclass ``to_dict`` and ``from_dict``."""

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        result = _plain(self)
        assert isinstance(result, dict)  # noqa: S101 - narrowing for the type checker
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Rebuild a record from :meth:`to_dict` output."""
        built = _build(cls, data)
        assert isinstance(built, cls)  # noqa: S101
        return built


@dataclass(frozen=True)
class CatalogResource(_Record):
    """A catalog. ``managed=False`` means bricksmith only references it and emits no bundle resource."""

    key: str
    name: str
    managed: bool = False
    owner: str = ""
    comment: str = ""
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class SchemaResource(_Record):
    """A schema for the logical scope ``scope`` (``catalog.schema``)."""

    key: str
    scope: str
    catalog: str
    name: str
    managed: bool = True
    owner: str = ""
    comment: str = ""
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class VolumeResource(_Record):
    """A volume inside a schema; ``schema_scope`` is the logical ``catalog.schema`` it belongs to."""

    key: str
    schema_scope: str
    catalog: str
    schema: str
    name: str
    volume_type: str = "MANAGED"
    managed: bool = True
    owner: str = ""
    comment: str = ""
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Dependency(_Record):
    """An ordering edge to another task of the same job; ``outcome`` selects a condition branch."""

    key: str
    outcome: str = ""


@dataclass(frozen=True)
class JobTask(_Record):
    """One task of a job.

    ``task_id`` names the graph task for ``bricksmith`` tasks. ``path`` is the script or notebook,
    ``parameters`` the command-line arguments (or notebook base parameters as ``name=value``),
    ``op``/``left``/``right`` a condition and ``job`` the bricksmith job key a run-job task starts.
    """

    key: str
    kind: str
    task_id: str = ""
    depends_on: tuple[Dependency, ...] = ()
    path: str = ""
    parameters: tuple[str, ...] = ()
    op: str = ""
    left: str = ""
    right: str = ""
    job: str = ""
    timeout_seconds: int = 0
    max_retries: int = 0


@dataclass(frozen=True)
class Schedule(_Record):
    """A cron schedule in Quartz syntax."""

    cron: str
    timezone: str = "UTC"
    paused: bool = False


@dataclass(frozen=True)
class TableTrigger(_Record):
    """Run when any/all of the physical ``tables`` are updated."""

    tables: tuple[str, ...]
    condition: str = "ANY_UPDATED"
    min_seconds: int = 0
    wait_seconds: int = 0
    paused: bool = False


@dataclass(frozen=True)
class Notifications(_Record):
    """E-mail recipients per event."""

    on_start: tuple[str, ...] = ()
    on_success: tuple[str, ...] = ()
    on_failure: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunIdentity(_Record):
    """The identity a job runs as; at most one field is set."""

    user: str = ""
    service_principal: str = ""


@dataclass(frozen=True)
class Environment(_Record):
    """The serverless environment: version and pip-style dependencies (the bricksmith wheel)."""

    version: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class JobResource(_Record):
    """A job assembled from graph tasks and extra steps."""

    key: str
    name: str
    tasks: tuple[JobTask, ...]
    environment: Environment
    schedule: Schedule | None = None
    trigger: TableTrigger | None = None
    notifications: Notifications | None = None
    run_as: RunIdentity | None = None
    max_concurrent_runs: int = 1
    timeout_seconds: int = 0
    parameters: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResourceSet(_Record):
    """Everything bricksmith defines natively, plus hand-written resources it must not touch."""

    catalogs: tuple[CatalogResource, ...] = ()
    schemas: tuple[SchemaResource, ...] = ()
    volumes: tuple[VolumeResource, ...] = ()
    jobs: tuple[JobResource, ...] = ()
    preserved: tuple[str, ...] = ()
    target: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the serializable snapshot, with its version."""
        return {"version": SNAPSHOT_VERSION, **super().to_dict()}

    @property
    def fingerprint(self) -> str:
        """Return a short hash of the snapshot."""
        return hashlib.sha256(self.to_json().encode()).hexdigest()[:16]

    def to_json(self) -> str:
        """Return the stable JSON snapshot."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Rebuild a set from :meth:`to_dict` output, rejecting an unknown snapshot version."""
        if data.get("version") != SNAPSHOT_VERSION:
            msg = f"resource snapshot version {data.get('version')!r} is not supported"
            raise ValidationError(msg)
        return super().from_dict(data)

    @classmethod
    def from_json(cls, text: str) -> Self:
        """Rebuild a set from :meth:`to_json` output."""
        return cls.from_dict(json.loads(text))

    def job(self, key: str) -> JobResource:
        """Return the job with this key."""
        for item in self.jobs:
            if item.key == key:
                return item
        msg = f"no job {key!r} in the resource set"
        raise ValidationError(msg)
