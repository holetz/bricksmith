"""The ``governance`` command group: ``plan``, ``apply`` and ``audit``."""
# ruff: noqa: EM101, TRY003 - short usage messages

from __future__ import annotations

import datetime as dt
import importlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.cli.common import context_from_args, emit, global_parent
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, UsageError
from bricksmith.governance.adapter import apply_plan, read_live, spark_runner
from bricksmith.governance.config import desired_state, load_config
from bricksmith.governance.model import LiveResource
from bricksmith.governance.planner import audit_governance, plan_governance
from bricksmith.governance.report import governance_findings, governance_report

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Mapping, Sequence

    from bricksmith.governance.config import GovernanceConfig
    from bricksmith.governance.model import DesiredState


def _hooks(args: argparse.Namespace) -> list[Any]:
    hooks: list[Any] = []
    for spec in args.hook or ():
        module, _, attr = spec.partition(":")
        if not module or not attr:
            raise UsageError("--hook expects module:callable")
        hooks.append(getattr(importlib.import_module(module), attr))
    return hooks


def _variables(args: argparse.Namespace, default_catalog: str | None) -> dict[str, str]:
    values = {"catalog": default_catalog or ""} if default_catalog else {}
    for item in args.var or ():
        key, sep, value = item.partition("=")
        if not sep:
            raise UsageError("--var expects KEY=VALUE")
        values[key] = value
    return values


def _prepare(args: argparse.Namespace) -> tuple[GovernanceConfig, DesiredState, str | None]:
    config = load_config(args.config)
    context = context_from_args(args)
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.UTC).date()
    desired = desired_state(
        config,
        environment=context.environment,
        today=today,
        variables=_variables(args, context.catalog),
        hooks=_hooks(args),
    )
    return config, desired, context.environment


def _runner(args: argparse.Namespace) -> Callable[[str], Sequence[Mapping[str, Any]]]:
    from bricksmith.clients import Clients  # noqa: PLC0415 - Spark only when talking to the platform

    return spark_runner(Clients(context_from_args(args)).spark())


def _live(args: argparse.Namespace, desired: DesiredState) -> dict[str, LiveResource]:
    if args.live:
        data = json.loads(Path(args.live).read_text(encoding="utf-8"))
        return {name: LiveResource.from_dict(item) for name, item in data.items()}
    return read_live(_runner(args), desired.resources)


def _plan(args: argparse.Namespace) -> int:
    _, desired, _ = _prepare(args)
    plan = plan_governance(desired, _live(args, desired))
    emit(args, plan.to_dict(), plan.to_text())
    return EXIT_OK


def _audit(args: argparse.Namespace) -> int:
    config, desired, _ = _prepare(args)
    audit = audit_governance(desired, _live(args, desired))
    report = governance_report(audit)
    checks = governance_findings(audit, config.severity)
    text = "\n".join([*(f"{c['state']:11} {c['id']}" for r in report["resources"] for c in r["controls"]),
                      checks.to_text(), report["note"]])  # fmt: skip
    emit(args, {"report": report, "checks": checks.to_dict()}, text)
    return EXIT_OK if checks.ok else EXIT_VALIDATION


def _apply(args: argparse.Namespace) -> int:
    config, desired, _ = _prepare(args)
    run = _runner(args)
    live = read_live(run, desired.resources)
    plan = plan_governance(desired, live)
    if not args.confirm:
        emit(args, {**plan.to_dict(), "applied": False}, plan.to_text() + "\n(plan only; add --confirm to apply)")
        return EXIT_OK
    result = apply_plan(run, plan)
    audit = audit_governance(
        desired, read_live(run, desired.resources), applied=frozenset(result.applied), refused=result.refused
    )
    checks = governance_findings(audit, config.severity)
    payload = {"plan": plan.to_dict(), "result": result.to_dict(), "report": governance_report(audit),
               "checks": checks.to_dict(), "applied": True}  # fmt: skip
    emit(
        args,
        payload,
        plan.to_text() + f"\napplied {len(result.applied)}, refused {len(result.refused)}\n" + checks.to_text(),
    )
    return EXIT_OK if not result.refused else EXIT_VALIDATION


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="governance.toml", help="governance TOML file")
    parser.add_argument("--var", action="append", help="KEY=VALUE for {placeholders} in resource objects")
    parser.add_argument("--hook", action="append", help="policy hook as module:callable (repeatable)")
    parser.add_argument("--today", help="ISO date for principal-mapping expiry (default: today, UTC)")


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register ``governance plan|apply|audit``."""
    group = subparsers.add_parser(
        "governance", help="tags, grants and verifiable governance state", parents=[global_parent()]
    )
    commands = group.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="plan statements for managed resources", parents=[global_parent()])
    _common(plan)
    plan.add_argument("--live", help="JSON file of live state instead of reading the workspace")
    plan.set_defaults(handler=_plan)
    audit = commands.add_parser("audit", help="report desired/applied/verified/unavailable", parents=[global_parent()])
    _common(audit)
    audit.add_argument("--live", help="JSON file of live state instead of reading the workspace")
    audit.set_defaults(handler=_audit)
    apply = commands.add_parser("apply", help="apply the plan (needs --confirm)", parents=[global_parent()])
    _common(apply)
    apply.add_argument("--confirm", action="store_true", help="actually run the statements")
    apply.set_defaults(handler=_apply)
