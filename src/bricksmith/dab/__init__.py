"""Native Databricks bundle integration: a Spark-free resource model and a thin ``databricks-bundles`` adapter.

Nothing is generated to YAML. A project's ``databricks.yml`` references ``bricksmith.dab:load_resources``
under ``python: resources:``; the offline entry points are :func:`build_resources` and the
``bricksmith bundle`` command group.
"""

from bricksmith.dab.adapter import job_dict, load_resources, to_resources
from bricksmith.dab.build import Assembly, assemble, build_resources
from bricksmith.dab.jobs import task_key
from bricksmith.dab.model import (
    CatalogResource,
    Dependency,
    Environment,
    JobResource,
    JobTask,
    Notifications,
    ResourceSet,
    RunIdentity,
    Schedule,
    SchemaResource,
    TableTrigger,
    VolumeResource,
)
from bricksmith.dab.spec import BundleSettings, JobSpec, ScopeOptions, StepSpec, VolumeSpec, load_settings

__all__ = [
    "Assembly",
    "BundleSettings",
    "CatalogResource",
    "Dependency",
    "Environment",
    "JobResource",
    "JobSpec",
    "JobTask",
    "Notifications",
    "ResourceSet",
    "RunIdentity",
    "Schedule",
    "SchemaResource",
    "ScopeOptions",
    "StepSpec",
    "TableTrigger",
    "VolumeResource",
    "VolumeSpec",
    "assemble",
    "build_resources",
    "job_dict",
    "load_resources",
    "load_settings",
    "task_key",
    "to_resources",
]
