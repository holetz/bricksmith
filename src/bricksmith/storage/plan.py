"""Pure write planning: metadata, columns and policies become a serializable plan with the SQL it will run."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.errors import AuthorizationError
from bricksmith.storage.errors import WriteSpecError
from bricksmith.storage.spec import OBJECT_STRATEGIES, WriteSpec
from bricksmith.storage.sql import INPUT, column_list, key_match, literal, partition_predicate, quote

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

CHECK, CREATE, MUTATE, POST_CHECK, GUARD = "check", "create", "mutate", "post_check", "guard"
MUTATING = frozenset({CREATE, MUTATE})
SCD2_FLAG = "_bs_new"
SCD2_HAS_CURRENT = "_bs_has_current"


@dataclass(frozen=True)
class Step:
    """One ordered step. ``check`` and ``post_check`` SQL must return one count equal to ``expect``."""

    kind: str
    description: str
    sql: str = ""
    expect: int = 0

    @property
    def mutates(self) -> bool:
        """Whether the step changes the target."""
        return self.kind in MUTATING

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"description": self.description, "expect": self.expect, "kind": self.kind, "sql": self.sql}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Step:
        """Rebuild a step from :meth:`to_dict` output."""
        return cls(data["kind"], data["description"], data.get("sql", ""), int(data.get("expect", 0)))


@dataclass(frozen=True)
class WritePlan:
    """What a write will do, reported before the target is touched."""

    target: str
    operation: str
    strategy: str
    protected: bool
    granted: bool
    on_empty: str
    input_filter: str
    steps: tuple[Step, ...]
    spec: Mapping[str, object]

    @property
    def mutating_steps(self) -> tuple[Step, ...]:
        """Steps that change the target."""
        return tuple(s for s in self.steps if s.mutates)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "granted": self.granted,
            "input_filter": self.input_filter,
            "on_empty": self.on_empty,
            "operation": self.operation,
            "protected": self.protected,
            "spec": dict(self.spec),
            "steps": [s.to_dict() for s in self.steps],
            "strategy": self.strategy,
            "target": self.target,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WritePlan:
        """Rebuild a plan from :meth:`to_dict` output."""
        return cls(
            data["target"],
            data["operation"],
            data["strategy"],
            bool(data["protected"]),
            bool(data["granted"]),
            data["on_empty"],
            data.get("input_filter", ""),
            tuple(Step.from_dict(s) for s in data["steps"]),
            dict(data["spec"]),
        )

    def to_json(self) -> str:
        """Return stable, sorted JSON."""
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def ensure_authorized(target: str, operation: str, *, protected: bool, granted: bool) -> None:
    """Raise before any effect when a protected target has no grant for ``operation``."""
    if protected and not granted:
        msg = f"{target!r} is protected and {operation!r} is not authorized; nothing was written"
        raise AuthorizationError(msg)


def _unknown(label: str, wanted: Sequence[str], available: Sequence[str]) -> WriteSpecError:
    missing = ", ".join(sorted(set(wanted) - set(available)))
    return WriteSpecError(f"{label} not in the input: {missing}")


def _require(label: str, wanted: Sequence[str], available: Sequence[str]) -> None:
    if set(wanted) - set(available):
        raise _unknown(label, wanted, available)


def _window_steps(spec: WriteSpec) -> tuple[list[Step], str]:
    """Return the checks for the declared window and the input filter that implements ``late_input=drop``."""
    window = spec.window
    if window is None:
        return [], ""
    col = quote(window.column)
    start, end = literal(window.start), literal(window.end)
    steps = [
        Step(CHECK, f"window column {window.column} has no nulls", f"SELECT count(*) FROM {INPUT} WHERE {col} IS NULL"),
        Step(
            CHECK,
            f"no input at or after window end {window.end}",
            f"SELECT count(*) FROM {INPUT} WHERE {col} >= {end}",
        ),
    ]
    filter_sql = ""
    if spec.late_input == "fail":
        steps.append(
            Step(
                CHECK,
                f"no late input before window start {window.start}",
                f"SELECT count(*) FROM {INPUT} WHERE {col} < {start}",
            )
        )
    elif spec.late_input == "drop":
        filter_sql = f"{col} >= {start}"
    return steps, filter_sql


def _key_steps(keys: Sequence[str]) -> list[Step]:
    null_any = " OR ".join(f"{quote(k)} IS NULL" for k in keys)
    return [
        Step(CHECK, "no null keys in the input", f"SELECT count(*) FROM {INPUT} WHERE {null_any}"),
        Step(
            CHECK,
            "keys are unique in the input",
            f"SELECT count(*) FROM (SELECT 1 FROM {INPUT} GROUP BY {column_list(keys)} HAVING count(*) > 1)",
        ),
    ]


def _window_predicate(spec: WriteSpec) -> str:
    window = spec.window
    assert window is not None  # noqa: S101 - callers check
    col = quote(window.column)
    return f"{col} >= {literal(window.start)} AND {col} < {literal(window.end)}"


def _replace_steps(
    spec: WriteSpec, cols: Sequence[str], target: str, values: Sequence[Mapping[str, object]] | None
) -> tuple[list[Step], list[Step]]:
    steps: list[Step] = []
    if spec.window is not None:
        predicate = _window_predicate(spec)
    elif spec.partitions:
        predicate = partition_predicate([dict(p) for p in spec.partitions])
        steps.append(
            Step(
                CHECK,
                "every input row lies in the named partitions",
                f"SELECT count(*) FROM {INPUT} WHERE NOT coalesce({predicate}, false)",
            )
        )
    elif values is not None:
        predicate = partition_predicate(values)
    else:
        predicate = "<partitions present in the input>"
    insert = f"INSERT INTO {quote(target)} REPLACE WHERE {predicate} SELECT {column_list(cols)} FROM {INPUT}"
    return steps, [Step(MUTATE, f"replace only rows matching: {predicate}", insert)]


def _merge_steps(spec: WriteSpec, cols: Sequence[str], target: str) -> list[Step]:
    on = key_match(spec.keys, "t", "s")
    insert = f"WHEN NOT MATCHED THEN INSERT ({column_list(cols)}) VALUES ({column_list(cols, 's.')})"
    if spec.strategy == "append":
        sql = f"MERGE INTO {quote(target)} AS t USING {INPUT} AS s ON {on} {insert}"
        return [Step(MUTATE, "insert rows whose key is absent; existing keys are left untouched", sql)]
    updated = spec.update_columns or tuple(c for c in cols if c not in spec.keys)
    update = ""
    if updated:
        sets = ", ".join(f"t.{quote(c)} = s.{quote(c)}" for c in updated)
        update = f"WHEN MATCHED THEN UPDATE SET {sets} "
    sql = f"MERGE INTO {quote(target)} AS t USING {INPUT} AS s ON {on} {update}{insert}"
    return [Step(MUTATE, "upsert on keys: update matches, insert the rest", sql)]


def scd2_invariant_steps(target: str, spec: WriteSpec) -> list[Step]:
    """Post-checks: exactly one current row per key and well-formed, non-overlapping intervals."""
    t, keys = quote(target), column_list(spec.keys)
    vf, vt, cur = quote(spec.valid_from), quote(spec.valid_to), quote(spec.current)
    one_current = (
        f"SELECT count(*) FROM (SELECT 1 FROM {t} GROUP BY {keys} HAVING sum(CASE WHEN {cur} THEN 1 ELSE 0 END) <> 1)"
    )
    overlaps = (
        f"SELECT count(*) FROM (SELECT {vf} AS vf, {vt} AS vt, {cur} AS cur, "
        f"lead({vf}) OVER (PARTITION BY {keys} ORDER BY {vf}) AS nxt FROM {t}) "
        f"WHERE (vt IS NOT NULL AND vt <= vf) OR (nxt IS NOT NULL AND (vt IS NULL OR vt > nxt)) "
        f"OR (cur AND vt IS NOT NULL) OR (NOT cur AND vt IS NULL)"
    )
    return [
        Step(POST_CHECK, "exactly one current row per key", one_current),
        Step(POST_CHECK, "valid intervals are well formed and do not overlap", overlaps),
    ]


def _scd2_steps(spec: WriteSpec, base: Sequence[str], target: str, *, exists: bool) -> tuple[list[Step], list[Step]]:
    t = quote(target)
    eff, vf, vt, cur = (quote(c) for c in (spec.effective, spec.valid_from, spec.valid_to, spec.current))
    k0 = quote(spec.keys[0])
    same = " AND ".join(f"s.{quote(c)} <=> c.{quote(c)}" for c in spec.tracked)
    join = f"{INPUT} AS s JOIN {t} AS c ON {key_match(spec.keys, 's', 'c')} AND c.{cur}"
    pre = [
        Step(CHECK, "effective column has no nulls", f"SELECT count(*) FROM {INPUT} WHERE {eff} IS NULL"),
        *_key_steps(spec.keys),
    ]
    create: list[Step] = []
    if exists:
        pre.append(
            Step(
                CHECK,
                "no conflicting redelivery at the current version's effective time",
                f"SELECT count(*) FROM {join} WHERE NOT ({same}) AND s.{eff} = c.{vf}",
            )
        )
        if spec.late_input == "fail":
            pre.append(
                Step(
                    CHECK, "no backdated input", f"SELECT count(*) FROM {join} WHERE NOT ({same}) AND s.{eff} < c.{vf}"
                )
            )
    else:
        select = f"SELECT {column_list(base)}, {eff} AS {vf}, {eff} AS {vt}, true AS {cur} FROM {INPUT} WHERE 1 = 0"
        create.append(Step(CREATE, "create the empty history table", f"CREATE TABLE {t} AS {select}"))
    cols = column_list(base, "s.")
    changed = (
        f"SELECT {cols}, (c.{k0} IS NOT NULL) AS {SCD2_HAS_CURRENT} FROM {INPUT} AS s "
        f"LEFT JOIN {t} AS c ON {key_match(spec.keys, 's', 'c')} AND c.{cur} "
        f"WHERE c.{k0} IS NULL OR (NOT ({same}) AND s.{eff} > c.{vf})"
    )
    plain = column_list(base)
    stage = (
        f"WITH changed AS ({changed}) "
        f"SELECT {plain}, false AS {SCD2_FLAG} FROM changed WHERE {SCD2_HAS_CURRENT} "
        f"UNION ALL SELECT {plain}, true AS {SCD2_FLAG} FROM changed"
    )
    merge = (
        f"MERGE INTO {t} AS t USING ({stage}) AS s "
        f"ON t.{cur} AND NOT s.{SCD2_FLAG} AND {key_match(spec.keys, 't', 's')} "
        f"WHEN MATCHED THEN UPDATE SET t.{vt} = s.{eff}, t.{cur} = false "
        f"WHEN NOT MATCHED AND s.{SCD2_FLAG} THEN INSERT ({plain}, {vf}, {vt}, {cur}) "
        f"VALUES ({cols}, s.{eff}, NULL, true)"
    )
    return pre + create, [Step(MUTATE, "close changed current rows and insert the new versions", merge)]


def _object_steps(spec: WriteSpec, target: str, statement: object) -> list[Step]:
    if not isinstance(statement, str) or not statement.strip():
        msg = f"{spec.strategy} needs the defining SQL text as the written data"
        raise WriteSpecError(msg)
    body = statement.strip().rstrip(";").strip()
    t = quote(target)
    if spec.strategy == "view":
        return [Step(MUTATE, "create or replace the view", f"CREATE OR REPLACE VIEW {t} AS {body}")]
    if spec.strategy == "materialized_view":
        sql = f"CREATE OR REPLACE MATERIALIZED VIEW {t} AS {body}"
        return [Step(MUTATE, "create or replace the materialized view (where available)", sql)]
    sql = f"CREATE OR REPLACE FUNCTION {t}({spec.params}) RETURNS {spec.returns} RETURN {body}"
    return [Step(MUTATE, "create or replace the SQL function", sql)]


def plan_write(  # noqa: PLR0913 - one keyword per planning input
    target: str,
    spec: WriteSpec,
    *,
    operation: str,
    input_columns: Sequence[str] = (),
    target_columns: Sequence[str] | None = None,
    statement: object = None,
    partition_values: Sequence[Mapping[str, object]] | None = None,
    protected: bool = False,
    granted: bool = False,
) -> WritePlan:
    """Validate columns and policies and return the write plan; reads and writes nothing."""
    ensure_authorized(target, operation, protected=protected, granted=granted)
    steps: list[Step] = []
    input_filter = ""
    if spec.strategy in OBJECT_STRATEGIES:
        steps = _object_steps(spec, target, statement)
        return _finish(
            target,
            spec,
            operation=operation,
            protected=protected,
            granted=granted,
            steps=steps,
            input_filter=input_filter,
        )
    steps, input_filter = _table_steps(target, spec, input_columns, target_columns, partition_values)
    return _finish(
        target, spec, operation=operation, protected=protected, granted=granted, steps=steps, input_filter=input_filter
    )


def _table_steps(  # noqa: C901, PLR0912, PLR0915 - one branch per strategy
    target: str,
    spec: WriteSpec,
    input_columns: Sequence[str],
    target_columns: Sequence[str] | None,
    values: Sequence[Mapping[str, object]] | None,
) -> tuple[list[Step], str]:
    cols = list(input_columns)
    if not cols or len(set(cols)) != len(cols):
        msg = "the input needs at least one column and unique column names"
        raise WriteSpecError(msg)
    window_col = (spec.window.column,) if spec.window else ()
    for label, wanted in (
        ("keys", spec.keys),
        ("partition_by", spec.partition_by),
        ("tracked", spec.tracked),
        ("update_columns", spec.update_columns),
        ("window column", window_col),
        ("effective", (spec.effective,) if spec.effective else ()),
    ):
        _require(label, wanted, cols)
    if set(spec.update_columns) & set(spec.keys):
        msg = "update_columns must not include keys"
        raise WriteSpecError(msg)
    generated = (spec.valid_from, spec.valid_to, spec.current)
    if spec.strategy == "scd2":
        clash = set(generated) & set(cols)
        if clash:
            msg = f"the input already has the generated scd2 columns: {', '.join(sorted(clash))}"
            raise WriteSpecError(msg)
        expected = {*cols, *generated}
    else:
        expected = set(cols)
    if target_columns is not None and set(target_columns) != expected:
        msg = (
            f"target columns differ from the input (missing {sorted(expected - set(target_columns))}, "
            f"extra {sorted(set(target_columns) - expected)}); schema evolution is a separate, explicit step"
        )
        raise WriteSpecError(msg)
    base = [c for c in target_columns if c in cols] if target_columns is not None else cols
    exists = target_columns is not None
    pre: list[Step] = []
    window_checks, input_filter = _window_steps(spec)
    mutate: list[Step]
    create_sql = f"CREATE TABLE {quote(target)} AS SELECT {column_list(base)} FROM {INPUT} WHERE 1 = 0"
    if spec.strategy in ("overwrite", "static_refresh"):
        if spec.strategy == "static_refresh" and not spec.force:
            pre.append(Step(GUARD, "skip when the target already has rows (static output); force: true overrides"))
        verb = "INSERT OVERWRITE" if exists else None
        select = f"SELECT {column_list(base)} FROM {INPUT}"
        sql = f"{verb} {quote(target)} {select}" if verb else f"CREATE TABLE {quote(target)} AS {select}"
        mutate = [Step(MUTATE, "replace the whole table" if exists else "create the table from the input", sql)]
    elif spec.strategy == "replace_partitions":
        pre += window_checks
        extra, mutate = _replace_steps(spec, base, target, values)
        pre += extra
        if not exists:
            parted = f" PARTITIONED BY ({column_list(spec.partition_by)})" if spec.partition_by else ""
            pre.append(
                Step(CREATE, "create the empty table", create_sql.replace(" AS SELECT", f"{parted} AS SELECT", 1))
            )
    elif spec.strategy in ("append", "merge"):
        pre += window_checks
        if spec.keys:
            pre += _key_steps(spec.keys)
        if not exists:
            pre.append(Step(CREATE, "create the empty table", create_sql))
        if spec.keys:
            mutate = _merge_steps(spec, base, target)
        else:
            sql = f"INSERT INTO {quote(target)} ({column_list(base)}) SELECT {column_list(base)} FROM {INPUT}"
            mutate = [Step(MUTATE, "append every row (not idempotent: allow_duplicates)", sql)]
    else:
        pre += window_checks
        more, mutate = _scd2_steps(spec, base, target, exists=exists)
        pre += more
    pre += [
        Step(CHECK, f"input check: {expr}", f"SELECT count(*) FROM {INPUT} WHERE NOT coalesce({expr}, false)")
        for expr in spec.input_checks
    ]
    post = [
        Step(
            POST_CHECK,
            f"target check: {expr}",
            f"SELECT count(*) FROM {quote(target)} WHERE NOT coalesce({expr}, false)",
        )
        for expr in spec.target_checks
    ]
    if spec.strategy == "scd2" and spec.verify:
        post = scd2_invariant_steps(target, spec) + post
    ordered = [s for s in pre if s.kind in (GUARD, CHECK)] + [s for s in pre if s.kind == CREATE] + mutate + post
    return ordered, input_filter


def _finish(  # noqa: PLR0913 - mirrors the plan fields
    target: str,
    spec: WriteSpec,
    *,
    operation: str,
    protected: bool,
    granted: bool,
    steps: list[Step],
    input_filter: str,
) -> WritePlan:
    return WritePlan(
        target=target,
        operation=operation,
        strategy=spec.strategy,
        protected=protected,
        granted=granted,
        on_empty=spec.on_empty,
        input_filter=input_filter,
        steps=tuple(steps),
        spec=spec.to_dict(),
    )
