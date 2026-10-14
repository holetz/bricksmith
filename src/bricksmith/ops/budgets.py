"""Operational budgets from ``bricksmith.toml`` and the dry-run explanation of expensive operations."""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.errors import ConfigError
from bricksmith.ops.errors import BudgetExceededError, OpsConfigError
from bricksmith.ops.readiness import InputSpec
from bricksmith.plan.model import RUN

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.plan import Plan

PROJECT_FILE = "bricksmith.toml"
MAINTENANCE_OPERATIONS = frozenset({"cluster", "optimize", "vacuum"})
_REWRITES = frozenset({"overwrite", "static_refresh", "replace_partitions"})
_MERGES = frozenset({"merge", "scd2"})
_BUDGET_KEYS = ("max_concurrency", "max_transfer_bytes", "max_scan_bytes", "max_maintenance_ops")


@dataclass(frozen=True)
class Budgets:
    """Limits for one run; ``None`` means no limit declared."""

    max_concurrency: int | None = None
    max_transfer_bytes: int | None = None
    max_scan_bytes: int | None = None
    max_maintenance_ops: int | None = None


@dataclass(frozen=True)
class TaskEstimate:
    """Sizes a project *declares* for a task (never measured or guessed by bricksmith)."""

    transfer_bytes: int | None = None
    scan_bytes: int | None = None


@dataclass(frozen=True)
class OpsConfig:
    """The ``[ops]`` tables of ``bricksmith.toml``."""

    budgets: Budgets = field(default_factory=Budgets)
    estimates: Mapping[str, TaskEstimate] = field(default_factory=dict)
    inputs: tuple[InputSpec, ...] = ()


def _positive(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        msg = f"{PROJECT_FILE}: {where} must be a positive integer"
        raise OpsConfigError(msg)
    return value


def _estimate(data: Mapping[str, Any], where: str) -> TaskEstimate:
    return TaskEstimate(
        _positive(data["transfer_bytes"], where) if "transfer_bytes" in data else None,
        _positive(data["scan_bytes"], where) if "scan_bytes" in data else None,
    )


def parse_ops_config(data: Mapping[str, Any]) -> OpsConfig:
    """Validate the ``ops`` table of a parsed project file."""
    ops = data.get("ops", {})
    if not isinstance(ops, dict):
        msg = f"{PROJECT_FILE}: [ops] must be a table"
        raise OpsConfigError(msg)
    raw = ops.get("budgets", {})
    unknown = sorted(set(raw) - set(_BUDGET_KEYS)) if isinstance(raw, dict) else []
    if not isinstance(raw, dict) or unknown:
        msg = f"{PROJECT_FILE}: [ops.budgets] accepts only {list(_BUDGET_KEYS)}" + (
            f"; got {unknown}" if unknown else ""
        )
        raise OpsConfigError(msg)
    budgets = Budgets(**{k: _positive(v, f"ops.budgets.{k}") for k, v in raw.items()})
    estimates = {str(task): _estimate(v, f"ops.estimates.{task}") for task, v in dict(ops.get("estimates", {})).items()}
    try:
        inputs = tuple(InputSpec.from_dict(item) for item in ops.get("inputs", []))
    except (KeyError, TypeError, ValueError) as error:
        msg = f"{PROJECT_FILE}: invalid [[ops.inputs]] entry ({type(error).__name__}: {error})"
        raise OpsConfigError(msg) from None
    return OpsConfig(budgets, estimates, inputs)


def load_ops_config(root: Path | str = ".") -> OpsConfig:
    """Read the ``[ops]`` tables under ``root``; a missing file yields no budgets."""
    path = Path(root) / PROJECT_FILE
    if not path.is_file():
        return OpsConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        msg = f"{PROJECT_FILE} is not valid TOML: {error}"
        raise ConfigError(msg) from None
    return parse_ops_config(data)


@dataclass(frozen=True)
class ExpensiveOperation:
    """One operation of the plan that costs more than a plain append."""

    task_id: str
    target: str
    operation: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """Return the serializable form."""
        return {"operation": self.operation, "reason": self.reason, "target": self.target, "task_id": self.task_id}


@dataclass(frozen=True)
class BudgetViolation:
    """A declared budget the plan would exceed."""

    budget: str
    limit: int
    observed: int
    message: str

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"budget": self.budget, "limit": self.limit, "message": self.message, "observed": self.observed}


@dataclass(frozen=True)
class DryRunExplanation:
    """What a plan would cost, which budgets it breaks and which tasks could not be checked."""

    concurrency: int
    expensive: tuple[ExpensiveOperation, ...]
    violations: tuple[BudgetViolation, ...]
    totals: Mapping[str, int]
    unchecked: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Tell whether no declared budget is violated."""
        return not self.violations

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "concurrency": self.concurrency,
            "expensive": [e.to_dict() for e in self.expensive],
            "ok": self.ok,
            "totals": dict(self.totals),
            "unchecked": list(self.unchecked),
            "violations": [v.to_dict() for v in self.violations],
        }

    def to_text(self) -> str:
        """Return a human explanation."""
        lines = [
            f"concurrency {self.concurrency}; declared totals: " + ", ".join(f"{k}={v}" for k, v in self.totals.items())
        ]
        lines.extend(f"expensive: {e.task_id} -> {e.target} ({e.operation}): {e.reason}" for e in self.expensive)
        lines.extend(f"VIOLATION {v.budget}: {v.message}" for v in self.violations)
        if self.unchecked:
            lines.append("no declared size for: " + ", ".join(self.unchecked) + " (budget cannot be verified for them)")
        lines.append("budgets respected" if self.ok else "budgets VIOLATED; the run should not start")
        return "\n".join(lines)

    def enforce(self) -> None:
        """Raise :class:`BudgetExceededError` listing every violation."""
        if self.violations:
            raise BudgetExceededError("; ".join(v.message for v in self.violations))


def _entry_estimate(task_id: str, options: Mapping[str, Any], declared: Mapping[str, TaskEstimate]) -> TaskEstimate:
    found = declared.get(task_id)
    if found:
        return found
    ops = options.get("ops")
    return _estimate(ops, f"ops in {task_id}") if isinstance(ops, dict) else TaskEstimate()


def explain_plan(plan: Plan, config: OpsConfig, *, concurrency: int) -> DryRunExplanation:
    """Explain the expensive operations of ``plan`` and check ``config.budgets`` before anything runs.

    Transfer and scan sizes come only from declarations (``[ops.estimates."<task id>"]`` or a task's
    ``metadata={"ops": {...}}``); a task with writes, sinks or maintenance and no declared size is listed
    under ``unchecked`` rather than assumed to be small.
    """
    budgets = config.budgets
    expensive: list[ExpensiveOperation] = []
    transfer = scan = maintenance = 0
    unchecked: list[str] = []
    for entry in (e for e in plan.entries if e.action == RUN):
        options: Mapping[str, Any] = entry.targets[0].options if entry.targets else {}
        estimate = _entry_estimate(entry.task_id, options, config.estimates)
        transfer += estimate.transfer_bytes or 0
        scan += estimate.scan_bytes or 0
        heavy = bool(entry.sinks)
        for target in entry.targets:
            for op in target.operations:
                if op in MAINTENANCE_OPERATIONS:
                    maintenance += 1
                    expensive.append(ExpensiveOperation(entry.task_id, target.physical, op, "maintenance operation"))
                elif op in _REWRITES:
                    heavy = True
                    expensive.append(
                        ExpensiveOperation(entry.task_id, target.physical, op, "rewrites the target table")
                    )
                elif op in _MERGES:
                    heavy = True
                    expensive.append(
                        ExpensiveOperation(entry.task_id, target.physical, op, "scans the target to match keys")
                    )
        expensive.extend(
            ExpensiveOperation(entry.task_id, sink, "external_sink", "transfers data outside the workspace tables")
            for sink in entry.sinks
        )
        needs = (budgets.max_transfer_bytes is not None and entry.sinks) or (
            budgets.max_scan_bytes is not None and heavy
        )
        if needs and estimate == TaskEstimate():
            unchecked.append(entry.task_id)
    violations = _violations(budgets, concurrency, transfer, scan, maintenance)
    totals = {"maintenance_ops": maintenance, "scan_bytes": scan, "transfer_bytes": transfer}
    return DryRunExplanation(concurrency, tuple(expensive), violations, totals, tuple(unchecked))


def _violations(
    budgets: Budgets, concurrency: int, transfer: int, scan: int, maintenance: int
) -> tuple[BudgetViolation, ...]:
    pairs = (
        ("max_concurrency", budgets.max_concurrency, concurrency, "requested concurrency"),
        ("max_transfer_bytes", budgets.max_transfer_bytes, transfer, "declared transfer bytes"),
        ("max_scan_bytes", budgets.max_scan_bytes, scan, "declared scan bytes"),
        ("max_maintenance_ops", budgets.max_maintenance_ops, maintenance, "maintenance operations"),
    )
    return tuple(
        BudgetViolation(name, limit, observed, f"{label} {observed} exceeds {name} = {limit}")
        for name, limit, observed, label in pairs
        if limit is not None and observed > limit
    )


def to_json(explanation: DryRunExplanation) -> str:
    """Return sorted JSON for an explanation."""
    return json.dumps(explanation.to_dict(), indent=2, sort_keys=True) + "\n"
