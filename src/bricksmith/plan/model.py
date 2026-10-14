"""The reviewed plan: plain, serializable records that load without any task runtime."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from bricksmith.errors import AuthorizationError, PlanError

if TYPE_CHECKING:
    from collections.abc import Mapping

PLAN_VERSION = 1
RUN = "run"
SKIP = "skip"
ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Selection:
    """The inputs that make a plan reproducible; re-planning with them must give the same plan."""

    roots: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    upstream: bool = True
    downstream: bool = False
    include_static: bool = False
    partial: bool = False
    done: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "done": list(self.done),
            "downstream": self.downstream,
            "exclude": list(self.exclude),
            "include": list(self.include),
            "include_static": self.include_static,
            "partial": self.partial,
            "roots": list(self.roots),
            "upstream": self.upstream,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Selection:
        """Rebuild a selection from :meth:`to_dict` output."""
        return cls(
            roots=tuple(data["roots"]),
            include=tuple(data["include"]),
            exclude=tuple(data["exclude"]),
            upstream=bool(data["upstream"]),
            downstream=bool(data["downstream"]),
            include_static=bool(data["include_static"]),
            partial=bool(data["partial"]),
            done=tuple(data["done"]),
        )


@dataclass(frozen=True)
class Target:
    """One physical object a task writes and the operations it declares on it."""

    name: str
    physical: str
    operations: tuple[str, ...]
    protected: bool = False
    options: Mapping[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form; ``options`` (the task's write metadata) appear only when present."""
        data: dict[str, object] = {
            "name": self.name,
            "operations": list(self.operations),
            "physical": self.physical,
            "protected": self.protected,
        }
        if self.options:
            data["options"] = dict(self.options)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Target:
        """Rebuild a target from :meth:`to_dict` output."""
        return cls(
            data["name"],
            data["physical"],
            tuple(data["operations"]),
            bool(data["protected"]),
            dict(data.get("options") or {}),
        )


@dataclass(frozen=True)
class ReadBinding:
    """A read a task may perform: the reference used in code and the physical object behind it."""

    reference: str
    kind: str
    physical: str

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"kind": self.kind, "physical": self.physical, "reference": self.reference}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReadBinding:
        """Rebuild a binding from :meth:`to_dict` output."""
        return cls(data["reference"], data["kind"], data["physical"])


@dataclass(frozen=True)
class PlanEntry:
    """One task in the plan with its action (``run`` or ``skip``) and the reason for it."""

    task_id: str
    action: str
    reason: str
    selected_by: str
    kind: str
    path: str
    function: str
    variant: Mapping[str, object] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    reads: tuple[ReadBinding, ...] = ()
    targets: tuple[Target, ...] = ()
    sinks: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form; ``sinks`` (declared external sinks) appear only when present."""
        extra: dict[str, object] = {"sinks": list(self.sinks)} if self.sinks else {}
        return {
            **extra,
            "action": self.action,
            "depends_on": list(self.depends_on),
            "function": self.function,
            "kind": self.kind,
            "path": self.path,
            "reads": [item.to_dict() for item in self.reads],
            "reason": self.reason,
            "selected_by": self.selected_by,
            "targets": [item.to_dict() for item in self.targets],
            "task_id": self.task_id,
            "variant": dict(self.variant),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PlanEntry:
        """Rebuild an entry from :meth:`to_dict` output."""
        return cls(
            task_id=data["task_id"],
            action=data["action"],
            reason=data["reason"],
            selected_by=data["selected_by"],
            kind=data["kind"],
            path=data["path"],
            function=data["function"],
            variant=dict(data["variant"]),
            depends_on=tuple(data["depends_on"]),
            reads=tuple(ReadBinding.from_dict(item) for item in data["reads"]),
            targets=tuple(Target.from_dict(item) for item in data["targets"]),
            sinks=tuple(data.get("sinks", ())),
        )


@dataclass(frozen=True)
class Issue:
    """A finding about the plan; an ``error`` means the plan can be reviewed but never applied."""

    code: str
    severity: str
    message: str
    subject: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"code": self.code, "message": self.message, "severity": self.severity, "subject": self.subject}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Issue:
        """Rebuild an issue from :meth:`to_dict` output."""
        return cls(data["code"], data["severity"], data["message"], data["subject"])


@dataclass(frozen=True)
class Authorization:
    """Permission to apply ``operations`` to one explicit physical object of the reviewed plan."""

    object: str
    operations: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    """What would run, in which order and with which effects; reviewed before it is applied."""

    selection: Selection
    graph_fingerprint: str
    entries: tuple[PlanEntry, ...] = ()
    issues: tuple[Issue, ...] = ()
    grants: tuple[Authorization, ...] = ()
    granted_for: str = ""

    @property
    def errors(self) -> tuple[Issue, ...]:
        """Return the issues that forbid applying the plan."""
        return tuple(item for item in self.issues if item.severity == ERROR)

    @property
    def to_run(self) -> tuple[PlanEntry, ...]:
        """Return the entries that execute, in plan order."""
        return tuple(item for item in self.entries if item.action == RUN)

    def entry(self, task_id: str) -> PlanEntry:
        """Return the entry of ``task_id``."""
        for item in self.entries:
            if item.task_id == task_id:
                return item
        msg = f"no task {task_id!r} in the plan"
        raise PlanError(msg)

    def _content(self) -> dict[str, object]:
        return {
            "entries": [item.to_dict() for item in self.entries],
            "graph_fingerprint": self.graph_fingerprint,
            "issues": [item.to_dict() for item in self.issues],
            "selection": self.selection.to_dict(),
            "version": PLAN_VERSION,
        }

    @property
    def fingerprint(self) -> str:
        """Return a content hash that ignores authorizations, which are added during review."""
        text = json.dumps(self._content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(text.encode()).hexdigest()

    def authorize(self, *grants: Authorization) -> Plan:
        """Return a copy that carries ``grants``, each naming a physical object and operations of this plan.

        A grant never reaches beyond the objects it names: upstream targets need their own grants.
        """
        known = {t.physical: t for e in self.to_run for t in e.targets}
        for grant in grants:
            target = known.get(grant.object)
            if target is None:
                msg = f"{grant.object!r} is not a target of a task that runs in this plan"
                raise AuthorizationError(msg)
            extra = set(grant.operations) - set(target.operations)
            if extra or not grant.operations:
                msg = f"operations {sorted(extra) or '[]'} are not declared for {grant.object!r}"
                raise AuthorizationError(msg)
        merged = {(g.object, op) for g in (*self.grants, *grants) for op in g.operations}
        collapsed: dict[str, list[str]] = {}
        for physical, operation in sorted(merged):
            collapsed.setdefault(physical, []).append(operation)
        return replace(
            self,
            grants=tuple(Authorization(k, tuple(v)) for k, v in collapsed.items()),
            granted_for=self.fingerprint,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form, including the fingerprint and the authorizations."""
        return {
            **self._content(),
            "authorizations": {
                "grants": [{"object": g.object, "operations": list(g.operations)} for g in self.grants],
                "plan": self.granted_for,
            },
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Plan:
        """Rebuild a plan, rejecting an unknown version or content that does not match its fingerprint."""
        if data.get("version") != PLAN_VERSION:
            msg = f"unsupported plan version {data.get('version')!r}; expected {PLAN_VERSION}"
            raise PlanError(msg)
        auth = data.get("authorizations", {"grants": [], "plan": ""})
        plan = cls(
            selection=Selection.from_dict(data["selection"]),
            graph_fingerprint=data["graph_fingerprint"],
            entries=tuple(PlanEntry.from_dict(item) for item in data["entries"]),
            issues=tuple(Issue.from_dict(item) for item in data["issues"]),
            grants=tuple(Authorization(g["object"], tuple(g["operations"])) for g in auth["grants"]),
            granted_for=auth["plan"],
        )
        if "fingerprint" in data and data["fingerprint"] != plan.fingerprint:
            msg = "the plan content does not match its fingerprint; it was edited after review"
            raise PlanError(msg)
        return plan

    def to_json(self) -> str:
        """Return sorted, indented JSON that is identical for identical plans."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Plan:
        """Rebuild a plan from :meth:`to_json` output."""
        return cls.from_dict(json.loads(text))
