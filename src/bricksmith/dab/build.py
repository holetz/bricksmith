"""Build the resource set from the metadata graph, the project mappings and the bundle settings."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bricksmith.dab.jobs import Findings, assemble_jobs
from bricksmith.dab.model import CatalogResource, ResourceSet, SchemaResource, VolumeResource
from bricksmith.dab.spec import ScopeOptions, load_settings
from bricksmith.graph import load_graph
from bricksmith.tasks.diagnostics import ERROR, Diagnostic
from bricksmith.tasks.project import load_project

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from bricksmith.context import ExecutionContext
    from bricksmith.dab.spec import BundleSettings
    from bricksmith.graph import TaskGraph
    from bricksmith.tasks.project import Project

_PRESERVED = re.compile(r"[a-z_]+\.[A-Za-z0-9_]+")
_VOLUME_TYPES = ("MANAGED", "EXTERNAL")
_SCOPE_PARTS = 2


@dataclass(frozen=True)
class Assembly:
    """The outcome of building resources: the set (complete even when invalid) and every finding."""

    resources: ResourceSet
    diagnostics: tuple[Diagnostic, ...]

    @property
    def errors(self) -> tuple[Diagnostic, ...]:
        """Return the findings that block deployment."""
        return tuple(item for item in self.diagnostics if item.severity == ERROR)

    @property
    def ok(self) -> bool:
        """Tell whether the set can be deployed."""
        return not self.errors


def resource_key(*parts: str) -> str:
    """Return a bundle resource key from physical name parts (non-word characters become ``_``)."""
    return re.sub(r"\W+", "_", "_".join(parts)).strip("_")


def _scoped(options: ScopeOptions | None, *, default: bool) -> tuple[bool, str, str, tuple[str, ...]]:
    if options is None:
        return default, "", "", ()
    return (default if options.managed is None else options.managed), options.owner, options.comment, options.targets


def _structure(  # noqa: C901 - catalogs, schemas, volumes and their collisions in one pass
    graph: TaskGraph,
    settings: BundleSettings,
    catalog_names: Mapping[str, str],
    schema_names: Mapping[str, str],
    findings: Findings,
) -> tuple[list[CatalogResource], list[SchemaResource], list[VolumeResource]]:
    scopes = {node.scope for node in graph.tasks} | set(settings.schemas)
    catalogs: dict[str, CatalogResource] = {}
    schemas: dict[str, SchemaResource] = {}
    physical_scope: dict[tuple[str, str], str] = {}
    for scope in sorted(scopes):
        parts = scope.split(".")
        if len(parts) != _SCOPE_PARTS:
            findings.error("unknown_scope", scope, "a schema scope must be 'catalog.schema'")
            continue
        logical_catalog, logical_schema = parts
        catalog = catalog_names.get(logical_catalog, logical_catalog)
        schema = schema_names.get(logical_schema, logical_schema)
        if (catalog, schema) in physical_scope:
            message = f"scopes {physical_scope[catalog, schema]} and {scope} map to {catalog}.{schema}"
            findings.error("resource_collision", scope, message)
        physical_scope[catalog, schema] = scope
        if logical_catalog not in catalogs:
            managed, owner, comment, targets = _scoped(
                settings.catalogs.get(logical_catalog), default=settings.manage_catalogs
            )
            catalogs[logical_catalog] = CatalogResource(
                resource_key(catalog), catalog, managed, owner, comment, targets
            )
        managed, owner, comment, targets = _scoped(settings.schemas.get(scope), default=settings.manage_schemas)
        key = resource_key(catalog, schema)
        schemas[scope] = SchemaResource(key, scope, catalog, schema, managed, owner, comment, targets)
    for name in sorted(set(settings.catalogs) - set(catalogs)):
        findings.error("unknown_scope", name, "[bundle.catalogs] names a catalog that no scope uses")
    volumes: list[VolumeResource] = []
    for spec in settings.volumes:
        schema = schemas.get(spec.schema)
        if schema is None:
            findings.error("unknown_scope", spec.key, f"volume schema {spec.schema!r} is not a known scope")
            continue
        if spec.volume_type not in _VOLUME_TYPES:
            findings.error("unsupported_option", spec.key, f"volume_type must be one of {', '.join(_VOLUME_TYPES)}")
        volumes.append(
            VolumeResource(
                key=resource_key(schema.catalog, schema.name, spec.name or spec.key),
                schema_scope=spec.schema,
                catalog=schema.catalog,
                schema=schema.name,
                name=spec.name or spec.key,
                volume_type=spec.volume_type,
                managed=spec.managed,
                owner=spec.owner,
                comment=spec.comment,
                targets=spec.targets,
            )
        )
    for kind, items in (("schemas", list(schemas.values())), ("volumes", volumes)):
        seen: dict[str, str] = {}
        for item in items:
            label = getattr(item, "scope", None) or item.name
            if item.key in seen and seen[item.key] != label:
                findings.error(
                    "key_collision", item.key, f"{kind} {seen[item.key]} and {label} share the key {item.key!r}"
                )
            seen[item.key] = label
    return list(catalogs.values()), list(schemas.values()), volumes


def _visible(targets: tuple[str, ...], target: str | None) -> bool:
    return target is None or not targets or target in targets


def assemble(
    graph: TaskGraph,
    project: Project,
    settings: BundleSettings,
    *,
    target: str | None = None,
    context: ExecutionContext | None = None,
) -> Assembly:
    """Build and validate the resource set; ``target`` keeps only the resources scoped to it.

    Validation runs over every target first, so a target filter can never hide an error.
    """
    findings = Findings()
    if not graph.ok:
        for item in graph.errors:
            findings.error("invalid_graph", item.subject, f"{item.code}: {item.message}")
    catalog_names = {**project.catalogs, **(context.catalogs if context else {})}
    schema_names = {**project.schemas, **(context.schemas if context else {})}
    catalogs, schemas, volumes = _structure(graph, settings, catalog_names, schema_names, findings)
    jobs = assemble_jobs(graph, settings.jobs, settings, findings)
    for entry in settings.preserved:
        if not _PRESERVED.fullmatch(entry):
            findings.error("invalid_preserved", entry, "preserved entries look like 'jobs.legacy_export'")
    python_defined = {
        *(f"schemas.{s.key}" for s in schemas if s.managed),
        *(f"volumes.{v.key}" for v in volumes if v.managed),
        *(f"catalogs.{c.key}" for c in catalogs if c.managed),
        *(f"jobs.{j.key}" for j in jobs),
    }
    for entry in sorted(set(settings.preserved) & python_defined):
        findings.error("preserved_collision", entry, "a hand-written resource has the same key as a Python-defined one")
    resources = ResourceSet(
        catalogs=tuple(c for c in catalogs if _visible(c.targets, target)),
        schemas=tuple(s for s in schemas if _visible(s.targets, target)),
        volumes=tuple(v for v in volumes if _visible(v.targets, target)),
        jobs=tuple(j for j in jobs if _visible(j.targets, target)),
        preserved=tuple(sorted(set(settings.preserved))),
        target=target or "",
    )
    return Assembly(resources, tuple(sorted(findings.items)))


def build_resources(
    root: Path | str = ".", *, target: str | None = None, context: ExecutionContext | None = None
) -> Assembly:
    """Load the project under ``root`` offline (no Spark, no ``databricks-bundles``) and assemble its resources."""
    project = load_project(root)
    graph = load_graph(root, context=context)
    return assemble(graph, project, load_settings(root), target=target, context=context)
