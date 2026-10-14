"""The migration planner: ordered, classified, serializable operations from a declared and a live schema."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from bricksmith.evolution import diff as d
from bricksmith.evolution.errors import SchemaSpecError
from bricksmith.evolution.model import TableSchema, normalize_expression, references, substitute
from bricksmith.evolution.types import INCOMPATIBLE, NARROW, WIDEN
from bricksmith.storage.plan import CHECK, MUTATE, Step
from bricksmith.storage.sql import literal, quote

COMPATIBLE = "compatible"
INCOMPATIBLE_CLASS = "incompatible"
DESTRUCTIVE_CLASS = "destructive"
_RANK = {COMPATIBLE: 0, INCOMPATIBLE_CLASS: 1, DESTRUCTIVE_CLASS: 2}
COLUMN_MAPPING = {
    "delta.columnMapping.mode": "name",
    "delta.minReaderVersion": "2",
    "delta.minWriterVersion": "5",
}
TYPE_WIDENING = {"delta.enableTypeWidening": "true"}
_POLICY_KEYS = frozenset({"renames", "narrowing", "incompatible", "destructive"})
_HANDLING = ("refuse", "checked")


@dataclass(frozen=True)
class EvolutionPolicy:
    """What a migration may do beyond compatible changes. The default allows nothing risky.

    ``narrowing`` and ``incompatible`` are ``refuse`` or ``checked`` (allowed only after a lossless-conversion
    check on the data); ``destructive`` allows dropping columns; ``renames`` maps live paths to new leaf names.
    """

    renames: Mapping[str, str] = field(default_factory=dict)
    narrowing: str = "refuse"
    incompatible: str = "refuse"
    destructive: bool = False

    def to_dict(self) -> dict[str, object]:
        """Return the stable serializable form."""
        return {
            "destructive": self.destructive,
            "incompatible": self.incompatible,
            "narrowing": self.narrowing,
            "renames": dict(sorted(self.renames.items())),
        }


def parse_policy(data: Mapping[str, Any] | None) -> EvolutionPolicy:
    """Validate a task's ``evolution`` metadata (or a declaration's ``evolution`` table)."""
    if data is None:
        return EvolutionPolicy()
    extra = set(data) - _POLICY_KEYS
    if extra:
        msg = f"unknown evolution keys: {sorted(extra)}"
        raise SchemaSpecError(msg)
    for key in ("narrowing", "incompatible"):
        if data.get(key, "refuse") not in _HANDLING:
            msg = f"evolution {key} must be one of {', '.join(_HANDLING)}"
            raise SchemaSpecError(msg)
    renames = data.get("renames", {})
    if not isinstance(renames, Mapping) or not all(isinstance(v, str) and "." not in v for v in renames.values()):
        msg = "evolution renames maps a live path to a new leaf name"
        raise SchemaSpecError(msg)
    if data.get("destructive", False) not in {True, False}:
        msg = "evolution destructive must be true or false"
        raise SchemaSpecError(msg)
    return EvolutionPolicy(
        {str(k): str(v) for k, v in renames.items()},
        data.get("narrowing", "refuse"),
        data.get("incompatible", "refuse"),
        data.get("destructive", False) is True,
    )


@dataclass(frozen=True)
class Operation:
    """One classified step of a migration; ``blocked`` explains the missing policy, if any."""

    kind: str
    path: str
    classification: str
    description: str
    steps: tuple[Step, ...] = ()
    blocked: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the stable serializable form."""
        return {
            "blocked": self.blocked,
            "classification": self.classification,
            "description": self.description,
            "kind": self.kind,
            "path": self.path,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Operation:
        """Rebuild an operation from :meth:`to_dict` output."""
        steps = tuple(Step.from_dict(s) for s in data.get("steps", ()))
        return cls(
            data["kind"], data["path"], data["classification"], data["description"], steps, data.get("blocked", "")
        )


@dataclass(frozen=True)
class MigrationPlan:
    """The ordered operations that turn the live table into the declaration; empty means nothing to do."""

    target: str
    fingerprint: str
    operations: tuple[Operation, ...]
    policy: Mapping[str, object] = field(default_factory=dict)
    projected_columns: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        """Whether the table already matches (re-running is a no-op)."""
        return not self.operations

    @property
    def blocked(self) -> tuple[Operation, ...]:
        """Operations the policy does not allow."""
        return tuple(o for o in self.operations if o.blocked)

    @property
    def ok(self) -> bool:
        """Whether every operation is allowed."""
        return not self.blocked

    @property
    def steps(self) -> tuple[Step, ...]:
        """All steps in execution order."""
        return tuple(s for o in self.operations for s in o.steps)

    @property
    def classification(self) -> str:
        """The worst classification present (``compatible`` for an empty plan)."""
        return max((o.classification for o in self.operations), key=_RANK.__getitem__, default=COMPATIBLE)

    def to_dict(self) -> dict[str, object]:
        """Return the stable serializable form."""
        return {
            "classification": self.classification,
            "fingerprint": self.fingerprint,
            "operations": [o.to_dict() for o in self.operations],
            "policy": dict(self.policy),
            "projected_columns": list(self.projected_columns),
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> MigrationPlan:
        """Rebuild a plan from :meth:`to_dict` output."""
        return cls(
            data["target"],
            data["fingerprint"],
            tuple(Operation.from_dict(o) for o in data["operations"]),
            dict(data.get("policy", {})),
            tuple(data.get("projected_columns", ())),
        )

    def to_json(self) -> str:
        """Return stable, sorted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    def to_text(self) -> str:
        """Return a readable summary."""
        if self.empty:
            return f"{self.target}: schema matches the declaration; nothing to do"
        lines = [f"{self.target}: {len(self.operations)} operation(s), worst class {self.classification}"]
        for number, op in enumerate(self.operations, 1):
            tag = f"  BLOCKED: {op.blocked}" if op.blocked else ""
            lines.append(f"{number:>3}. [{op.classification}] {op.description}{tag}")
        return "\n".join(lines)


def fingerprint(live: TableSchema) -> str:
    """Return a short stable hash of the live state a plan was made against."""
    return hashlib.sha256(json.dumps(live.to_dict(), sort_keys=True).encode()).hexdigest()[:16]


class _Builder:
    def __init__(self, target: str, declared: TableSchema, live: TableSchema, policy: EvolutionPolicy) -> None:
        self.target, self.declared, self.live, self.policy = target, declared, live, policy
        self.t = quote(target)

    def alter(self, tail: str) -> str:
        return f"ALTER TABLE {self.t} {tail}"

    def op(self, kind: str, path: str, cls: str, text: str, *sql: str, blocked: str = "") -> Operation:
        steps = tuple(Step(MUTATE, text, s) for s in sql)
        return Operation(kind, path, cls, text, steps, blocked)

    def properties(self, props: Mapping[str, str], why: str = "") -> Operation:
        pairs = ", ".join(f"{literal(k)} = {literal(v)}" for k, v in sorted(props.items()))
        text = f"set table properties {', '.join(sorted(props))}{why}"
        return self.op(
            d.SET_PROPERTY, ",".join(sorted(props)), COMPATIBLE, text, self.alter(f"SET TBLPROPERTIES ({pairs})")
        )

    def add(self, change: d.Change) -> Operation:
        want = change.declared
        assert want is not None  # noqa: S101 - set by diff for adds
        ddl = f"{quote(change.path)} {want.type.ddl()}{'' if want.nullable else ' NOT NULL'}"
        if want.comment:
            ddl += f" COMMENT {literal(want.comment)}"
        text = f"add column {change.path} {want.type.ddl()}{'' if want.nullable else ' NOT NULL'}"
        if want.nullable:
            return self.op(d.ADD_COLUMN, change.path, COMPATIBLE, text, self.alter(f"ADD COLUMN {ddl}"))
        if self.policy.incompatible != "checked":
            why = "existing rows would violate NOT NULL; policy incompatible=checked allows it on an empty table"
            return self.op(d.ADD_COLUMN, change.path, INCOMPATIBLE_CLASS, text, blocked=why)
        check = Step(CHECK, f"{self.target} is empty", f"SELECT count(*) FROM {self.t}")
        return Operation(
            d.ADD_COLUMN,
            change.path,
            INCOMPATIBLE_CLASS,
            text,
            (check, Step(MUTATE, text, self.alter(f"ADD COLUMN {ddl}"))),
        )

    def drop(self, change: d.Change) -> Operation:
        text = f"drop column {change.path} (data in it is lost)"
        why = "" if self.policy.destructive else "dropping a column needs policy destructive=true"
        return self.op(
            d.DROP_COLUMN,
            change.path,
            DESTRUCTIVE_CLASS,
            text,
            self.alter(f"DROP COLUMN {quote(change.path)}"),
            blocked=why,
        )

    def rename(self, change: d.Change) -> Operation:
        new = str(change.after).rpartition(".")[2]
        text = f"rename column {change.path} to {new}"
        return self.op(
            d.RENAME_COLUMN,
            change.path,
            COMPATIBLE,
            text,
            self.alter(f"RENAME COLUMN {quote(change.path)} TO {quote(new)}"),
        )

    def retype(self, change: d.Change) -> Operation:
        kind = change.type_change
        text = f"change type of {change.path} from {change.before} to {change.after} ({kind})"
        if kind == WIDEN:
            sql = self.alter(f"ALTER COLUMN {quote(change.path)} TYPE {change.after}")
            return self.op(d.CHANGE_TYPE, change.path, COMPATIBLE, text, sql)
        handling = self.policy.narrowing if kind == NARROW else self.policy.incompatible
        policy_name = "narrowing" if kind == NARROW else "incompatible"
        if handling != "checked":
            why = f"{kind} type change needs policy {policy_name}=checked (verified lossless conversion)"
            return self.op(d.CHANGE_TYPE, change.path, INCOMPATIBLE_CLASS, text, blocked=why)
        if "." in change.path or change.declared is None or change.live is None:
            why = "nested type rewrites are not supported; widen the leaf type or replace the column explicitly"
            return self.op(d.CHANGE_TYPE, change.path, INCOMPATIBLE_CLASS, text, blocked=why)
        return self._rewrite(change, text)

    def _rewrite(self, change: d.Change, text: str) -> Operation:
        want, have = change.declared, change.live
        assert want is not None  # noqa: S101 - checked by the caller
        assert have is not None  # noqa: S101 - checked by the caller
        col, tmp, new = quote(change.path), quote(f"_bs_tmp_{change.path}"), want.type.ddl()
        lossy = (
            f"SELECT count(*) FROM {self.t} WHERE {col} IS NOT NULL AND "
            f"(try_cast({col} AS {new}) IS NULL OR CAST(try_cast({col} AS {new}) AS {have.type.ddl()}) <> {col})"
        )
        comment = want.comment if want.comment is not None else have.comment
        add = f"ADD COLUMN {tmp} {new}" + (f" COMMENT {literal(comment)}" if comment else "")
        steps = [
            Step(CHECK, f"every value of {change.path} converts to {new} and back unchanged", lossy),
            Step(MUTATE, f"add temporary column for {change.path}", self.alter(add)),
            Step(
                MUTATE,
                f"convert {change.path} into the temporary column",
                f"UPDATE {self.t} SET {tmp} = CAST({col} AS {new})",
            ),
            Step(MUTATE, f"drop the old {change.path}", self.alter(f"DROP COLUMN {col}")),
            Step(MUTATE, f"rename the temporary column to {change.path}", self.alter(f"RENAME COLUMN {tmp} TO {col}")),
        ]
        if not want.nullable:
            steps.append(
                Step(MUTATE, f"restore NOT NULL on {change.path}", self.alter(f"ALTER COLUMN {col} SET NOT NULL"))
            )
        return Operation(
            d.CHANGE_TYPE, change.path, INCOMPATIBLE_CLASS, text + "; rewritten via a temporary column", tuple(steps)
        )

    def nullability(self, change: d.Change) -> Operation:
        path = change.path
        if change.after is True:
            return self.op(
                d.SET_NULLABLE,
                path,
                COMPATIBLE,
                f"allow nulls in {path}",
                self.alter(f"ALTER COLUMN {quote(path)} DROP NOT NULL"),
            )
        text = f"require non-null {path}"
        sql = self.alter(f"ALTER COLUMN {quote(path)} SET NOT NULL")
        if self.policy.narrowing != "checked":
            return self.op(
                d.SET_NULLABLE,
                path,
                INCOMPATIBLE_CLASS,
                text,
                blocked="tightening nullability needs policy narrowing=checked",
            )
        if "." in path:
            return self.op(
                d.SET_NULLABLE, path, INCOMPATIBLE_CLASS, text, blocked="nested nullability tightening is not supported"
            )
        check = Step(CHECK, f"{path} has no nulls", f"SELECT count(*) FROM {self.t} WHERE {quote(path)} IS NULL")
        return Operation(d.SET_NULLABLE, path, INCOMPATIBLE_CLASS, text, (check, Step(MUTATE, text, sql)))

    def add_check(self, name: str, expression: str) -> Operation:
        text = f"add check {name}: {expression}"
        guard = Step(CHECK, f"existing rows satisfy {name}", f"SELECT count(*) FROM {self.t} WHERE NOT ({expression})")
        add = Step(MUTATE, text, self.alter(f"ADD CONSTRAINT {quote(name)} CHECK ({expression})"))
        return Operation(d.ADD_CHECK, name, COMPATIBLE, text, (guard, add))

    def drop_check(self, name: str, why: str) -> Operation:
        text = f"drop check {name} ({why})"
        return self.op("drop_check", name, COMPATIBLE, text, self.alter(f"DROP CONSTRAINT {quote(name)}"))

    def key(self, change: d.Change) -> list[Operation]:
        out: list[Operation] = []
        if self.live.key_name and change.before:
            out.append(
                self.op(
                    "drop_key",
                    self.live.key_name,
                    COMPATIBLE,
                    f"drop primary key {self.live.key_name}",
                    self.alter(f"DROP CONSTRAINT {quote(self.live.key_name)}"),
                )
            )
        columns = list(change.after) if isinstance(change.after, list) else []
        if columns:
            name = self.declared.key_name or f"pk_{self.target.rpartition('.')[2]}"
            cols = ", ".join(quote(c) for c in columns)
            out.append(
                self.op(
                    d.SET_KEY,
                    name,
                    COMPATIBLE,
                    f"set primary key ({', '.join(columns)})",
                    self.alter(f"ADD CONSTRAINT {quote(name)} PRIMARY KEY ({cols})"),
                )
            )
        return out


def _dependents(live: TableSchema, paths: list[str], skip: set[str]) -> list[str]:
    return sorted(
        name for name, expr in live.checks.items() if name not in skip and any(references(expr, p) for p in paths)
    )


def plan_migration(  # noqa: C901 - one block per ordered phase
    target: str, declared: TableSchema, live: TableSchema, policy: EvolutionPolicy | None = None
) -> MigrationPlan:
    """Plan the migration of ``live`` to ``declared`` under ``policy``; reads and writes nothing.

    Order: required properties, checks that depend on changing columns are dropped, renames, adds, type
    changes, nullability, comments, drops, checks (re)created, keys, other properties, table comment.
    """
    policy = policy or EvolutionPolicy()
    changes = d.diff_schemas(declared, live, policy.renames)
    builder = _Builder(target, declared, live, policy)
    by = {k: [c for c in changes if c.kind == k] for k in {c.kind for c in changes}}

    def of(kind: str) -> list[d.Change]:
        return by.get(kind, [])

    rewrites = [c for c in of(d.CHANGE_TYPE) if c.type_change in {NARROW, INCOMPATIBLE} and "." not in c.path]
    needs_mapping = bool(of(d.RENAME_COLUMN) or of(d.DROP_COLUMN) or rewrites)
    needs_widening = any(c.type_change == WIDEN for c in of(d.CHANGE_TYPE))
    required: dict[str, str] = {}
    if needs_mapping and live.properties.get("delta.columnMapping.mode") != "name":
        required.update(COLUMN_MAPPING)
    if needs_widening and live.properties.get("delta.enableTypeWidening") != "true":
        required.update(TYPE_WIDENING)

    ops: list[Operation] = []
    if required:
        ops.append(builder.properties(required, " (required by the planned changes; an irreversible protocol upgrade)"))

    affected = [c.path for c in of(d.RENAME_COLUMN) + of(d.DROP_COLUMN) + of(d.CHANGE_TYPE)]
    changed_checks = {c.path for c in of(d.CHANGE_CHECK)}
    dependents = _dependents(live, affected, changed_checks)
    renamed = {c.path: str(c.after).rpartition(".")[2] for c in of(d.RENAME_COLUMN)}
    dropped = {c.path for c in of(d.DROP_COLUMN)}
    recreate: dict[str, str | None] = {}
    for name in dependents:
        if name in declared.checks:
            recreate[name] = declared.checks[name]
            continue
        expression = live.checks[name]
        if any(references(expression, p) for p in dropped):
            recreate[name] = None
            continue
        for old, new in renamed.items():
            expression = substitute(expression, old, new)
        recreate[name] = expression
    for name in [*dependents, *sorted(changed_checks)]:
        why = "depends on a changing column" if name in dependents else "its expression changed"
        ops.append(builder.drop_check(name, why))

    ops.extend(builder.rename(c) for c in of(d.RENAME_COLUMN))
    ops.extend(builder.add(c) for c in sorted(of(d.ADD_COLUMN), key=lambda c: c.path.count(".")))
    ops.extend(builder.retype(c) for c in of(d.CHANGE_TYPE))
    ops.extend(builder.nullability(c) for c in of(d.SET_NULLABLE))
    ops.extend(
        builder.op(
            d.SET_COMMENT,
            c.path,
            COMPATIBLE,
            f"comment on {c.path}",
            builder.alter(f"ALTER COLUMN {quote(c.path)} COMMENT {literal(c.after)}"),
        )
        for c in of(d.SET_COMMENT)
    )
    ops.extend(builder.drop(c) for c in of(d.DROP_COLUMN))

    final: dict[str, str] = {n: e for n, e in recreate.items() if e is not None}
    final.update({c.path: str(c.after) for c in of(d.CHANGE_CHECK)})
    final.update({c.path: str(c.after) for c in of(d.ADD_CHECK)})
    ops.extend(builder.add_check(n, e) for n, e in sorted(final.items()))
    for change in of(d.SET_KEY):
        ops.extend(builder.key(change))
    ops.extend(builder.properties({c.path: str(c.after)}) for c in of(d.SET_PROPERTY) if c.path not in required)
    ops.extend(
        builder.op(
            d.SET_TABLE_COMMENT,
            "",
            COMPATIBLE,
            "set table comment",
            f"COMMENT ON TABLE {builder.t} IS {literal(c.after)}",
        )
        for c in of(d.SET_TABLE_COMMENT)
    )
    names = _projected(live, changes, renamed, dropped, rewrites)
    return MigrationPlan(target, fingerprint(live), tuple(ops), policy.to_dict(), names)


def _projected(
    live: TableSchema, changes: list[d.Change], renamed: Mapping[str, str], dropped: set[str], rewrites: list[d.Change]
) -> tuple[str, ...]:
    names = [renamed.get(c.name, c.name) for c in live.columns]
    moved = {c.path for c in rewrites}
    names = [n for n in names if n not in dropped and n not in moved]
    names.extend(c.path for c in changes if c.kind == d.CHANGE_TYPE and c.path in moved)
    names.extend(c.path for c in changes if c.kind == d.ADD_COLUMN and "." not in c.path)
    return tuple(names)


__all__ = [
    "COMPATIBLE",
    "DESTRUCTIVE_CLASS",
    "INCOMPATIBLE_CLASS",
    "EvolutionPolicy",
    "MigrationPlan",
    "Operation",
    "fingerprint",
    "normalize_expression",
    "parse_policy",
    "plan_migration",
]
