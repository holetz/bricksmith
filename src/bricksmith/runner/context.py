"""The runtime task context: declared reads, guarded writes, nothing implicit."""

from __future__ import annotations

from typing import TYPE_CHECKING

from bricksmith.errors import AuthorizationError, ConfigError, ValidationError
from bricksmith.tasks.declaration import DATA, EXTERNAL, TEMPORAL

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.context import ExecutionContext
    from bricksmith.plan import PlanEntry, Target
    from bricksmith.runner.io import ExternalProvider, TableIO


class TaskContext:
    """Passed to the task callable; implements ``bricksmith.tasks.TaskReads`` plus ``write``.

    Reads resolve only references that the plan bound for this task. Writes resolve only the
    task's own targets, only declared operations, and protected targets only when granted.
    """

    def __init__(
        self,
        entry: PlanEntry,
        execution: ExecutionContext,
        table_io: TableIO,
        granted: frozenset[tuple[str, str]] = frozenset(),
        externals: ExternalProvider | None = None,
    ) -> None:
        """Bind the entry, the execution context, the I/O backend, the granted pairs and optional external I/O."""
        self.entry = entry
        self.execution = execution
        self.io = table_io
        self.writes: list[tuple[str, str]] = []
        self._granted = granted
        self._externals = externals

    @property
    def task_id(self) -> str:
        """Return the task identity."""
        return self.entry.task_id

    @property
    def variant(self) -> Mapping[str, object]:
        """Return the variant parameters of this task."""
        return self.entry.variant

    def _read(self, kind: str, name: str) -> object:
        for binding in self.entry.reads:
            if binding.kind == kind and binding.reference == name:
                return self.io.read(binding.physical)
        msg = f"{self.task_id} read {kind} {name!r}, which the plan did not bind to this task"
        raise ValidationError(msg)

    def read(self, name: str) -> object:
        """Read an output of another task."""
        return self._read(DATA, name)

    def read_external(self, name: str) -> object:
        """Read a resource the project does not produce, through the external provider when one is configured."""
        if self._externals is None:
            return self._read(EXTERNAL, name)
        for binding in self.entry.reads:
            if binding.kind == EXTERNAL and binding.reference == name:
                return self._externals.read_external(binding.physical)
        msg = f"{self.task_id} read external {name!r}, which the plan did not bind to this task"
        raise ValidationError(msg)

    def write_external(self, name: str, data: object) -> None:
        """Write ``data`` to an external sink declared in the task's ``external_sinks`` metadata."""
        if name not in self.entry.sinks:
            msg = f"{self.task_id} wrote external sink {name!r}, which the task did not declare in external_sinks"
            raise ValidationError(msg)
        if self._externals is None:
            msg = "no external provider is configured; pass externals= to run_plan"
            raise ConfigError(msg)
        self._externals.write_external(name, data)

    def read_state(self, name: str) -> object:
        """Read an earlier window or version of an output."""
        return self._read(TEMPORAL, name)

    def _target(self, name: str) -> Target:
        for target in self.entry.targets:
            if name in (target.name, target.name.rsplit(".", 1)[-1], target.physical):
                return target
        msg = f"{self.task_id} wrote {name!r}, which is not one of its targets"
        raise ValidationError(msg)

    def write(self, name: str, data: object, *, operation: str | None = None) -> None:
        """Write ``data`` to the task's own target ``name`` with a declared ``operation`` (default: the first)."""
        target = self._target(name)
        chosen = operation or target.operations[0]
        if chosen not in target.operations:
            msg = f"operation {chosen!r} is not declared for {target.physical!r}"
            raise ValidationError(msg)
        if target.protected and (target.physical, chosen) not in self._granted:
            msg = f"{target.physical!r} is protected and {chosen!r} is not authorized"
            raise AuthorizationError(msg)
        granted = (target.physical, chosen) in self._granted
        options = {**target.options, "target": target.name, "protected": target.protected, "granted": granted}
        self.io.write(target.physical, data, operation=chosen, options=options)
        self.writes.append((target.physical, chosen))
