"""The ``model`` command group: train, register, promote, rollback, retire and card.

Commands that mutate (``register``, ``promote``, ``rollback``, ``retire``, and ``train`` which runs tasks) only
print their plan unless ``--apply`` is given. Remote reads and writes use the explicit context of the global options.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_RUN_FAILED, EXIT_VALIDATION, UsageError
from bricksmith.mlops.errors import ModelError, ModelNotFoundError
from bricksmith.mlops.meta import FILE, ModelMetadata, render_card
from bricksmith.mlops.tasks import EXPERIMENT_PARAMETER, MODEL_TASK_KEY, name_from_physical, parse_model_task

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable

    from bricksmith.context import ExecutionContext
    from bricksmith.graph import TaskGraph
    from bricksmith.mlops.store import ModelStore


@dataclass(frozen=True)
class Located:
    """A model found in the project: its tasks, graph, context and registered name."""

    graph: TaskGraph
    context: ExecutionContext
    model: str
    name: str
    train_task: str | None


def locate(args: argparse.Namespace) -> Located:
    """Find the model's tasks in the project and derive its Unity Catalog name from the explicit scope or outputs."""
    from bricksmith.graph import load_graph  # noqa: PLC0415 - keeps parser construction light
    from bricksmith.mlops.store import registered_name  # noqa: PLC0415

    context = context_from_args(args)
    if getattr(args, "experiment", None):
        context = dataclasses.replace(context, parameters={**context.parameters, EXPERIMENT_PARAMETER: args.experiment})
    graph = load_graph(args.project, context=context)
    nodes = [(n, parse_model_task(n.metadata)) for n in graph.tasks if MODEL_TASK_KEY in n.metadata]
    mine = [(n, d) for n, d in nodes if d is not None and d.model == args.model]
    if not mine:
        msg = f"no task declares model_task for model {args.model!r}"
        raise ModelNotFoundError(msg)
    train = next((n for n, d in mine if d.role == "train"), None)
    scoped = next((d.scope for _, d in mine if d.scope), None)
    if scoped:
        name = registered_name(context, scoped, args.model)
    else:
        anchor = train or mine[0][0]
        physical = {o.name: o.physical for o in graph.outputs}.get(anchor.outputs[0], anchor.outputs[0])
        name = name_from_physical(physical, args.model)
    return Located(graph, context, args.model, name, train.id if train else None)


def _store(located: Located) -> ModelStore:
    from bricksmith.clients import Clients  # noqa: PLC0415
    from bricksmith.mlops.store import ModelStore  # noqa: PLC0415

    return ModelStore.from_clients(Clients(located.context))


def _train(args: argparse.Namespace) -> int:
    from bricksmith.plan import build_plan  # noqa: PLC0415
    from bricksmith.runner import apply_plan, default_table_io  # noqa: PLC0415

    found = locate(args)
    if found.train_task is None:
        msg = f"model {args.model!r} has no train task"
        raise ModelNotFoundError(msg)
    plan = build_plan(found.graph, roots=[found.train_task])
    payload: dict[str, Any] = {
        "apply": args.apply,
        "model": found.model,
        "name": found.name,
        "tasks": [e.task_id for e in plan.entries],
        "train_task": found.train_task,
    }
    if not args.apply:
        emit(
            args,
            payload,
            "\n".join(
                [
                    f"train {found.model} -> {found.name} (plan only; add --apply to run)",
                    *[f"run  {t}" for t in payload["tasks"]],
                ]
            ),
        )
        return EXIT_OK
    if EXPERIMENT_PARAMETER not in found.context.parameters:
        msg = "--apply needs --experiment <tracking experiment path>"
        raise UsageError(msg)
    report = apply_plan(
        plan, found.graph, project_root=args.project, execution=found.context, table_io=default_table_io(found.context)
    )
    emit(args, report.to_dict(), "\n".join(f"{r}" for r in report.ids("succeeded")) + ("" if report.ok else "\nfailed"))
    return EXIT_OK if report.ok else EXIT_RUN_FAILED


def _register(args: argparse.Namespace) -> int:
    from bricksmith.mlops.store import TAG_MODEL  # noqa: PLC0415
    from bricksmith.mlops.training import register_run  # noqa: PLC0415

    found = locate(args)
    store = _store(found)
    tags = dict(store.client.get_run(args.run_id).data.tags or {})
    if tags.get(TAG_MODEL) != found.model:
        msg = f"run {args.run_id} belongs to {tags.get(TAG_MODEL) or 'no bricksmith model'}, not {found.model!r}"
        raise ModelError(msg)
    payload: dict[str, Any] = {"apply": args.apply, "model": found.model, "name": found.name, "run_id": args.run_id}
    if args.apply:
        payload["version"] = register_run(store, found.name, found.model, args.run_id)
        emit(args, payload, f"registered {found.name} v{payload['version']} from run {args.run_id}")
    else:
        emit(args, payload, f"register run {args.run_id} as a new version of {found.name} (plan only; add --apply)")
    return EXIT_OK


def _alias(args: argparse.Namespace) -> int:
    from bricksmith.mlops import lifecycle  # noqa: PLC0415

    found = locate(args)
    store = _store(found)
    kwargs = {"reviewer": args.reviewer, "reason": args.reason, "graph": found.graph}
    if args.command == "promote":
        plan = lifecycle.plan_promotion(store, found.name, found.model, args.alias, args.version, **kwargs)
    else:
        plan = lifecycle.plan_rollback(store, found.name, found.model, args.alias, **kwargs)
    if args.apply:
        lifecycle.apply_alias_plan(store, plan)
    payload = {**plan.to_dict(), "apply": args.apply}
    emit(args, payload, plan.to_text() + ("\napplied" if args.apply else "\n(plan only; add --apply)"))
    return EXIT_OK


def _retire(args: argparse.Namespace) -> int:
    from bricksmith.mlops import lifecycle  # noqa: PLC0415

    found = locate(args)
    store = _store(found)
    plan = lifecycle.plan_retirement(store, found.name, found.model, graph=found.graph, versions=args.version or None)
    if args.apply:
        lifecycle.apply_retirement(store, plan)
    emit(
        args,
        {**plan.to_dict(), "apply": args.apply},
        plan.to_text() + ("\nretired" if args.apply else "\n(plan only; add --apply)"),
    )
    return EXIT_VALIDATION if plan.blocked else EXIT_OK


def _card(args: argparse.Namespace) -> int:
    if args.file:
        meta = ModelMetadata.from_json(Path(args.file).read_text(encoding="utf-8"))
        text = render_card(meta)
    else:
        import tempfile  # noqa: PLC0415

        found = locate(args)
        store = _store(found)
        resolved = store.resolve(found.name, found.model, alias=args.alias, version=args.version)
        with tempfile.TemporaryDirectory(prefix="bricksmith-card-") as tmp:
            _, meta = store.fetch(resolved, Path(tmp))
        text = render_card(
            meta, name=found.name, version=resolved.version, run_id=resolved.run_id, aliases=resolved.aliases,
            state=resolved.state, history=tuple(store.events(found.name, resolved.version)),
        )  # fmt: skip
    emit(args, {"card": text, "file": FILE}, text)
    return EXIT_OK


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``model train|register|promote|rollback|retire|card``."""
    group = subparsers.add_parser("model", help="model training, registration and lifecycle", parents=[global_parent()])
    commands = group.add_subparsers(dest="command", required=True)

    def command(
        name: str, handler: Callable[[argparse.Namespace], int], text: str, *, model: bool = True, apply: bool = True
    ) -> argparse.ArgumentParser:
        parser = commands.add_parser(name, help=text, parents=[global_parent()])
        parser.add_argument("--project", default=".", help="project root (default: .)")
        if model:
            parser.add_argument("--model", required=True, help="explicit model identity")
        if apply:
            parser.add_argument("--apply", action="store_true", help="perform the change (default: print the plan)")
        parser.set_defaults(handler=handler)
        return parser

    command("train", _train, "plan or run the model's train task with its upstream tasks").add_argument(
        "--experiment", help="tracking experiment path (context parameter mlflow_experiment)"
    )
    command("register", _register, "register a training run as a new version").add_argument("--run-id", required=True)
    for name, text in (
        ("promote", "point an alias at a reviewed version"),
        ("rollback", "return an alias to its previous version"),
    ):
        parser = command(name, _alias, text)
        parser.add_argument("--alias", required=True)
        parser.add_argument("--reviewer", required=True)
        parser.add_argument("--reason", required=True)
        if name == "promote":
            parser.add_argument("--version", type=int, required=True)
    command("retire", _retire, "retire versions: remove aliases, refuse while consumers exist").add_argument(
        "--version", type=int, action="append", help="version to retire (repeatable; default: all active)"
    )
    card = command("card", _card, "render the model card of an explicit version or alias", model=False, apply=False)
    card.add_argument("--model")
    card.add_argument("--version", type=int)
    card.add_argument("--alias")
    card.add_argument("--file", help="render offline from a bricksmith_model.json file")
