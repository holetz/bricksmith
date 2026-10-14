"""Parse and validate the write metadata a task declares (pure; no Spark)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from bricksmith.storage.errors import WriteSpecError

if TYPE_CHECKING:
    from collections.abc import Mapping

TABLE_STRATEGIES = ("overwrite", "static_refresh", "replace_partitions", "append", "merge", "scd2")
OBJECT_STRATEGIES = ("view", "materialized_view", "function")
STRATEGIES = (*TABLE_STRATEGIES, *OBJECT_STRATEGIES)
EMPTY_POLICIES = ("fail", "skip", "allow_empty")
LATE_POLICIES = ("fail", "drop", "accept")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Window:
    """A half-open temporal window ``[start, end)`` over one column; bounds are ISO literals."""

    column: str
    start: str
    end: str

    def to_dict(self) -> dict[str, str]:
        """Return the serializable form."""
        return {"column": self.column, "end": self.end, "start": self.start}


@dataclass(frozen=True)
class WriteSpec:
    """Validated write metadata of one task output."""

    strategy: str
    on_empty: str = "fail"
    keys: tuple[str, ...] = ()
    partition_by: tuple[str, ...] = ()
    partitions: tuple[tuple[tuple[str, object], ...], ...] = ()
    window: Window | None = None
    late_input: str = "fail"
    update_columns: tuple[str, ...] = ()
    allow_duplicates: bool = False
    force: bool = False
    tracked: tuple[str, ...] = ()
    effective: str = ""
    valid_from: str = "valid_from"
    valid_to: str = "valid_to"
    current: str = "is_current"
    verify: bool = True
    input_checks: tuple[str, ...] = ()
    target_checks: tuple[str, ...] = ()
    params: str = ""
    returns: str = ""
    extra: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form (stable, sorted by key when dumped)."""
        return {
            "allow_duplicates": self.allow_duplicates,
            "current": self.current,
            "effective": self.effective,
            "force": self.force,
            "input_checks": list(self.input_checks),
            "keys": list(self.keys),
            "late_input": self.late_input,
            "on_empty": self.on_empty,
            "partition_by": list(self.partition_by),
            "partitions": [dict(p) for p in self.partitions],
            "returns": self.returns,
            "strategy": self.strategy,
            "target_checks": list(self.target_checks),
            "tracked": list(self.tracked),
            "update_columns": list(self.update_columns),
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "verify": self.verify,
            "window": self.window.to_dict() if self.window else None,
        }


def _fail(message: str) -> WriteSpecError:
    return WriteSpecError(message)


def _identifier(value: object, what: str) -> str:
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        msg = f"{what} must be a plain column name, got {value!r}"
        raise _fail(msg)
    return value


def _columns(options: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = options.get(name)
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple):
        msg = f"{name} must be a column name or a list of column names"
        raise _fail(msg)
    columns = tuple(_identifier(item, f"{name} entry") for item in value)
    if len(set(columns)) != len(columns):
        msg = f"{name} lists a column twice"
        raise _fail(msg)
    return columns


def _texts(options: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = options.get(name) or ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) and v.strip() for v in value):
        msg = f"{name} must be a list of non-empty SQL boolean expressions"
        raise _fail(msg)
    return tuple(value)


def _choice(options: Mapping[str, object], name: str, allowed: tuple[str, ...], default: str) -> str:
    value = options.get(name, default)
    if value not in allowed:
        msg = f"{name} must be one of {', '.join(allowed)}, got {value!r}"
        raise _fail(msg)
    return str(value)


def _window(options: Mapping[str, object]) -> Window | None:
    raw = options.get("window")
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"column", "start", "end"}:
        msg = "window must be a mapping with exactly column, start and end"
        raise _fail(msg)
    start, end = raw["start"], raw["end"]
    if not (isinstance(start, str) and isinstance(end, str) and start and end and start < end):
        msg = "window start and end must be ISO strings with start before end"
        raise _fail(msg)
    return Window(_identifier(raw["column"], "window column"), start, end)


def _partitions(
    options: Mapping[str, object], partition_by: tuple[str, ...]
) -> tuple[tuple[tuple[str, object], ...], ...]:
    raw = options.get("partitions")
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        msg = "partitions must be a non-empty list of {column: value} mappings"
        raise _fail(msg)
    parts: list[tuple[tuple[str, object], ...]] = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != set(partition_by):
            msg = "each partitions entry must give exactly the partition_by columns"
            raise _fail(msg)
        if not all(isinstance(v, str | int | bool) for v in item.values()):
            msg = "partition values must be strings, integers or booleans"
            raise _fail(msg)
        parts.append(tuple((c, item[c]) for c in partition_by))
    return tuple(sorted(set(parts), key=repr))


def resolve_strategy(options: Mapping[str, object], operation: str | None) -> str:
    """Return the strategy: the operation when it names one, else ``metadata.write`` for the generic ``write``."""
    declared = options.get("write")
    if operation in STRATEGIES:
        return str(operation)
    if operation in (None, "write") and isinstance(declared, str) and declared in STRATEGIES:
        return declared
    msg = (
        f"no write strategy for operation {operation!r}"
        f" (metadata write={declared!r}); use one of {', '.join(STRATEGIES)}"
    )
    raise _fail(msg)


def parse_write_spec(options: Mapping[str, object], operation: str | None = None) -> WriteSpec:  # noqa: C901 - one rule per strategy
    """Validate ``options`` (a task's write metadata) for the strategy ``operation`` selects."""
    strategy = resolve_strategy(options, operation)
    keys = _columns(options, "keys")
    partition_by = _columns(options, "partition_by")
    window = _window(options)
    partitions = _partitions(options, partition_by)
    tracked = _columns(options, "tracked")
    late = _choice(options, "late_input", LATE_POLICIES, "fail")
    spec = WriteSpec(
        strategy=strategy,
        on_empty=_choice(options, "on_empty", EMPTY_POLICIES, "fail"),
        keys=keys,
        partition_by=partition_by,
        partitions=partitions,
        window=window,
        late_input=late,
        update_columns=_columns(options, "update_columns"),
        allow_duplicates=options.get("allow_duplicates") is True,
        force=options.get("force") is True,
        tracked=tracked,
        effective=_identifier(options["effective"], "effective") if "effective" in options else "",
        valid_from=_identifier(options.get("valid_from", "valid_from"), "valid_from"),
        valid_to=_identifier(options.get("valid_to", "valid_to"), "valid_to"),
        current=_identifier(options.get("current", "is_current"), "current"),
        verify=options.get("verify", True) is not False,
        input_checks=_texts(options, "input_checks"),
        target_checks=_texts(options, "target_checks"),
        params=str(options.get("params", "")),
        returns=str(options.get("returns", "")),
    )
    if strategy == "replace_partitions":
        if not partition_by and window is None:
            msg = "replace_partitions needs partition_by, a window, or both"
            raise _fail(msg)
        if partitions and window is not None:
            msg = "replace_partitions takes either partitions or a window, not both"
            raise _fail(msg)
        if partitions and not partition_by:
            msg = "partitions needs partition_by"
            raise _fail(msg)
        if late == "accept":
            msg = "late_input=accept would write outside the replaced window; use fail or drop"
            raise _fail(msg)
    if strategy in ("append", "merge", "scd2") and not keys and not (strategy == "append" and spec.allow_duplicates):
        msg = f"{strategy} needs keys" + (
            " (or allow_duplicates: true to accept non-idempotent re-runs)" if strategy == "append" else ""
        )
        raise _fail(msg)
    if strategy == "scd2":
        if not tracked or not spec.effective:
            msg = "scd2 needs tracked columns and an effective column"
            raise _fail(msg)
        if set(keys) & set(tracked) or spec.effective in keys:
            msg = "scd2 keys, tracked columns and the effective column must be distinct"
            raise _fail(msg)
        if late == "accept":
            msg = "scd2 does not support late_input=accept (history corrections are out of scope)"
            raise _fail(msg)
        if len({spec.valid_from, spec.valid_to, spec.current}) != 3:  # noqa: PLR2004 - three generated columns
            msg = "valid_from, valid_to and current must be three different column names"
            raise _fail(msg)
    if strategy == "function" and not spec.returns:
        msg = "function needs returns (the SQL return type)"
        raise _fail(msg)
    return spec
