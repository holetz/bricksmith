"""Retention (vacuum), clustering and optimize as maintenance plans, separate from ordinary writes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from bricksmith.evolution.errors import SchemaSpecError
from bricksmith.storage.plan import MUTATE, Step
from bricksmith.storage.sql import quote

OPERATIONS = ("cluster", "optimize", "vacuum")
MIN_RETENTION_HOURS = 168
_KEYS = frozenset({"cluster_by", "optimize", "vacuum", "schedule"})


@dataclass(frozen=True)
class MaintenanceSpec:
    """Declared maintenance. Absent sections are never planned; ``schedule`` is carried for the job that runs it.

    ``cluster_by`` is a column list (``[]`` removes clustering); ``optimize`` takes ``where`` and ``zorder_by``;
    ``vacuum`` takes ``retain_hours`` (at least one week unless ``allow_short_retention``).
    """

    cluster_by: tuple[str, ...] | None = None
    optimize: Mapping[str, object] | None = None
    vacuum: Mapping[str, object] | None = None
    schedule: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the stable serializable form."""
        return {
            "cluster_by": None if self.cluster_by is None else list(self.cluster_by),
            "optimize": None if self.optimize is None else dict(self.optimize),
            "schedule": self.schedule,
            "vacuum": None if self.vacuum is None else dict(self.vacuum),
        }


def parse_maintenance(data: Mapping[str, Any] | None) -> MaintenanceSpec:  # noqa: C901 - one rule per section
    """Validate a task's ``maintenance`` metadata; ordinary writes never read or run it."""
    if data is None:
        return MaintenanceSpec()
    extra = set(data) - _KEYS
    if extra:
        msg = f"unknown maintenance keys: {sorted(extra)}"
        raise SchemaSpecError(msg)
    cluster = data.get("cluster_by")
    if cluster is not None and (not isinstance(cluster, list | tuple) or not all(isinstance(c, str) for c in cluster)):
        msg = "cluster_by is a list of column names"
        raise SchemaSpecError(msg)
    optimize, vacuum = data.get("optimize"), data.get("vacuum")
    if optimize is not None:
        if not isinstance(optimize, Mapping) or set(optimize) - {"where", "zorder_by"}:
            msg = "optimize takes only where and zorder_by"
            raise SchemaSpecError(msg)
        if optimize.get("zorder_by") and cluster:
            msg = "zorder_by and cluster_by cannot be combined"
            raise SchemaSpecError(msg)
    if vacuum is not None:
        if not isinstance(vacuum, Mapping) or set(vacuum) - {"retain_hours", "allow_short_retention"}:
            msg = "vacuum takes only retain_hours and allow_short_retention"
            raise SchemaSpecError(msg)
        hours = vacuum.get("retain_hours", MIN_RETENTION_HOURS)
        if not isinstance(hours, int) or isinstance(hours, bool) or hours < 0:
            msg = "vacuum retain_hours is a whole number of hours"
            raise SchemaSpecError(msg)
        if hours < MIN_RETENTION_HOURS and vacuum.get("allow_short_retention") is not True:
            msg = f"vacuum retain_hours below {MIN_RETENTION_HOURS} needs allow_short_retention: true"
            raise SchemaSpecError(msg)
    schedule = data.get("schedule")
    if schedule is not None and not isinstance(schedule, str):
        msg = "schedule is a cron expression text"
        raise SchemaSpecError(msg)
    return MaintenanceSpec(
        None if cluster is None else tuple(cluster),
        None if optimize is None else dict(optimize),
        None if vacuum is None else dict(vacuum),
        schedule,
    )


@dataclass(frozen=True)
class MaintenancePlan:
    """The maintenance operations to run now, in order, with the schedule they belong to."""

    target: str
    operations: tuple[str, ...]
    steps: tuple[Step, ...]
    schedule: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the stable serializable form."""
        return {
            "operations": list(self.operations),
            "schedule": self.schedule,
            "steps": [s.to_dict() for s in self.steps],
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MaintenancePlan:
        """Rebuild a plan from :meth:`to_dict` output."""
        return cls(
            data["target"],
            tuple(data["operations"]),
            tuple(Step.from_dict(s) for s in data["steps"]),
            data.get("schedule"),
        )

    def to_json(self) -> str:
        """Return stable, sorted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_text(self) -> str:
        """Return a readable summary."""
        head = f"{self.target}: {len(self.steps)} maintenance step(s)" + (
            f", schedule {self.schedule}" if self.schedule else ""
        )
        return "\n".join([head, *(f"  - {s.description}" for s in self.steps)])


def plan_maintenance(target: str, spec: MaintenanceSpec, only: tuple[str, ...] | None = None) -> MaintenancePlan:
    """Plan the declared operations (or the ``only`` subset); nothing runs here and nothing is implied by writes."""
    unknown = set(only or ()) - set(OPERATIONS)
    if unknown:
        msg = f"unknown maintenance operations: {sorted(unknown)}"
        raise SchemaSpecError(msg)
    wanted = tuple(o for o in OPERATIONS if only is None or o in only)
    t, steps, names = quote(target), [], []
    if "cluster" in wanted and spec.cluster_by is not None:
        cols = ", ".join(quote(c) for c in spec.cluster_by)
        sql = f"ALTER TABLE {t} CLUSTER BY ({cols})" if spec.cluster_by else f"ALTER TABLE {t} CLUSTER BY NONE"
        steps.append(Step(MUTATE, f"set clustering to ({', '.join(spec.cluster_by) or 'none'})", sql))
        names.append("cluster")
    if "optimize" in wanted and spec.optimize is not None:
        sql = f"OPTIMIZE {t}"
        if spec.optimize.get("where"):
            sql += f" WHERE {spec.optimize['where']}"
        zorder = spec.optimize.get("zorder_by") or []
        if zorder:
            sql += " ZORDER BY (" + ", ".join(quote(str(c)) for c in zorder) + ")"  # type: ignore[attr-defined]
        steps.append(Step(MUTATE, "compact files" + (" and z-order" if zorder else ""), sql))
        names.append("optimize")
    if "vacuum" in wanted and spec.vacuum is not None:
        hours = int(spec.vacuum.get("retain_hours", MIN_RETENTION_HOURS))  # type: ignore[call-overload]
        steps.append(
            Step(MUTATE, f"remove unreferenced files older than {hours} hours", f"VACUUM {t} RETAIN {hours} HOURS")
        )
        names.append("vacuum")
    return MaintenancePlan(target, tuple(names), tuple(steps), spec.schedule)


def run_maintenance(spark: Any, plan: MaintenancePlan) -> None:  # noqa: ANN401 - Spark session
    """Run the plan's steps in order. Only this call (or the CLI ``--run``) performs maintenance."""
    for step in plan.steps:
        spark.sql(step.sql)


__all__ = [
    "OPERATIONS",
    "MaintenancePlan",
    "MaintenanceSpec",
    "parse_maintenance",
    "plan_maintenance",
    "run_maintenance",
]
