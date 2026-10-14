"""The ``bundle`` command group: offline ``resources`` and ``validate``."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bricksmith.cli.common import emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION

if TYPE_CHECKING:
    import argparse

    from bricksmith.dab.build import Assembly
    from bricksmith.dab.model import JobResource, ResourceSet


def _assemble(args: argparse.Namespace) -> Assembly:
    from bricksmith.dab.build import build_resources  # noqa: PLC0415 - keeps parser construction light

    return build_resources(args.project, target=args.target)


def _job_lines(job: JobResource) -> list[str]:
    head = f"job {job.key} ({job.name})"
    if job.schedule:
        head += f"  schedule={job.schedule.cron} {job.schedule.timezone}{' paused' if job.schedule.paused else ''}"
    if job.trigger:
        head += f"  trigger={job.trigger.condition}:{','.join(job.trigger.tables)}"
    lines = [head]
    for task in job.tasks:
        needs = ", ".join(f"{d.key}{':' + d.outcome if d.outcome else ''}" for d in task.depends_on) or "-"
        lines.append(f"  {task.kind:10} {task.key}  <- {needs}")
    return lines


def render(resources: ResourceSet) -> str:
    """Return the human-readable plan of a resource set."""
    lines = [f"target: {resources.target or '(all)'}", f"fingerprint: {resources.fingerprint}"]
    lines += [f"catalog {c.key} = {c.name}  {'managed' if c.managed else 'referenced'}" for c in resources.catalogs]
    lines += [
        f"schema {s.key} = {s.catalog}.{s.name}  {'managed' if s.managed else 'referenced'}" for s in resources.schemas
    ]
    lines += [
        f"volume {v.key} = {v.catalog}.{v.schema}.{v.name}  {'managed' if v.managed else 'referenced'}"
        for v in resources.volumes
    ]
    for job in resources.jobs:
        lines += _job_lines(job)
    lines += [f"preserved {entry}  (hand-written; untouched)" for entry in resources.preserved]
    return "\n".join(lines)


def _resources(args: argparse.Namespace) -> int:
    assembly = _assemble(args)
    payload = assembly.resources.to_dict()
    payload["diagnostics"] = [item.to_dict() for item in assembly.diagnostics]
    emit(args, payload, render(assembly.resources) + "".join(f"\n{item.text()}" for item in assembly.diagnostics))
    return EXIT_OK if assembly.ok else EXIT_VALIDATION


def _validate(args: argparse.Namespace) -> int:
    assembly = _assemble(args)
    resources = assembly.resources
    counts = {
        "catalogs": len(resources.catalogs),
        "errors": len(assembly.errors),
        "jobs": len(resources.jobs),
        "schemas": len(resources.schemas),
        "volumes": len(resources.volumes),
    }
    payload: dict[str, object] = {"counts": counts, "diagnostics": [i.to_dict() for i in assembly.diagnostics]}
    payload["ok"] = assembly.ok
    summary = ", ".join(f"{value} {key}" for key, value in counts.items())
    emit(args, payload, "\n".join([*(item.text() for item in assembly.diagnostics), summary]))
    return EXIT_OK if assembly.ok else EXIT_VALIDATION


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``bundle resources`` and ``bundle validate``; neither makes a remote call."""
    group = subparsers.add_parser(
        "bundle", help="inspect and validate native bundle resources", parents=[global_parent()]
    )
    commands = group.add_subparsers(dest="command", required=True)
    for name, handler, text in (
        ("resources", _resources, "print the resource snapshot/plan (JSON with --json)"),
        ("validate", _validate, "validate resources and jobs; exit 4 on errors"),
    ):
        command = commands.add_parser(name, help=text, parents=[global_parent()])
        command.add_argument("--project", default=".", help="project root holding bricksmith.toml (default: .)")
        command.set_defaults(handler=handler)
