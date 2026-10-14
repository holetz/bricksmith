"""The task declaration: one decorator, one validated record, one documented read API.

Standard library only. The same validation serves the runtime decorator and static discovery, so
a declaration means the same thing whether the module is imported or only parsed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from bricksmith.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

type VariantValue = str | int | float | bool

DATA = "data"
EXTERNAL = "external"
TEMPORAL = "temporal"
#: Context read methods understood by static inference, and the dependency kind each one yields.
READ_METHODS: dict[str, str] = {"read": DATA, "read_external": EXTERNAL, "read_state": TEMPORAL}
FIELDS = ("kind", "name", "outputs", "reads", "external", "state", "variants", "enabled", "static", "metadata")
TASK_ATTRIBUTE = "__bricksmith_task__"

PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_VARIANT_TEXT = re.compile(r"[A-Za-z0-9_.\-]+")
_MAX_REFERENCE_PARTS = 3


class TaskReads(Protocol):
    """The read API a task context offers; literal calls to it are inferred as dependencies."""

    def read(self, name: str) -> object:
        """Read an output produced by another task of the project."""
        ...

    def read_external(self, name: str) -> object:
        """Read a resource the project does not produce."""
        ...

    def read_state(self, name: str) -> object:
        """Read an earlier window or version of an output (possibly the task's own)."""
        ...


@dataclass(frozen=True)
class TaskDeclaration:
    """What a task declares about itself; location and inferred reads are added by discovery."""

    kind: str
    name: str
    outputs: tuple[str, ...]
    reads: tuple[str, ...] = ()
    external: tuple[str, ...] = ()
    state: tuple[str, ...] = ()
    variants: tuple[Mapping[str, VariantValue], ...] = ()
    enabled: bool = True
    static: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


def _fail(message: str) -> ValidationError:
    return ValidationError(message)


def _identifier(value: object, what: str, *, placeholders: bool = False) -> str:
    if isinstance(value, str):
        plain = PLACEHOLDER.sub("x", value) if placeholders else value
        if _IDENTIFIER.fullmatch(plain):
            return value
    msg = f"{what} {value!r} is not a valid identifier"
    raise _fail(msg)


def _strings(value: object, what: str) -> tuple[str, ...]:
    items = (value,) if isinstance(value, str) else value
    if not isinstance(items, (list, tuple)) or not all(isinstance(item, str) and item for item in items):
        msg = f"{what} must be a string or a list of non-empty strings, got {value!r}"
        raise _fail(msg)
    if len(set(items)) != len(items):
        msg = f"{what} repeats a name: {list(items)!r}"
        raise _fail(msg)
    return tuple(items)


def _references(value: object, what: str) -> tuple[str, ...]:
    items = _strings(value, what)
    for item in items:
        parts = item.split(".")
        if len(parts) > _MAX_REFERENCE_PARTS:
            msg = f"{what} {item!r} must be 'name', 'schema.name' or 'catalog.schema.name'"
            raise _fail(msg)
        for part in parts:
            _identifier(part, f"{what} part of {item!r}", placeholders=True)
    return items


def _variants(value: object) -> tuple[Mapping[str, VariantValue], ...]:
    if not isinstance(value, (list, tuple)):
        msg = f"variants must be a list of mappings, got {value!r}"
        raise _fail(msg)
    seen: set[str] = set()
    result: list[Mapping[str, VariantValue]] = []
    for item in value:
        if not isinstance(item, dict) or not item:
            msg = f"variant {item!r} must be a non-empty mapping"
            raise _fail(msg)
        checked: dict[str, VariantValue] = {}
        for key, raw in sorted(item.items(), key=lambda pair: str(pair[0])):
            _identifier(key, "variant parameter")
            if not isinstance(raw, (str, int, float, bool)) or (
                isinstance(raw, str) and not _VARIANT_TEXT.fullmatch(raw)
            ):
                msg = f"variant value {key}={raw!r} must be a number, a boolean or text matching [A-Za-z0-9_.-]+"
                raise _fail(msg)
            checked[key] = raw
        key = variant_key(checked)
        if key in seen:
            msg = f"variant {key!r} is declared twice"
            raise _fail(msg)
        seen.add(key)
        result.append(checked)
    return tuple(result)


def _json_value(value: object, what: str) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item, what) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {str(key): _json_value(item, what) for key, item in sorted(value.items())}
    msg = f"{what} holds a value that is not plain JSON data: {value!r}"
    raise _fail(msg)


def _flag(value: object, what: str) -> bool:
    if not isinstance(value, bool):
        msg = f"{what} must be True or False, got {value!r}"
        raise _fail(msg)
    return value


def variant_key(variant: Mapping[str, VariantValue]) -> str:
    """Return the stable text of a parameter variant, for example ``region=north,window=7``."""
    return ",".join(f"{key}={variant[key]}" for key in sorted(variant))


def make_declaration(function: str, values: Mapping[str, object]) -> TaskDeclaration:
    """Validate raw declaration values (from the decorator or from parsed source) into a record.

    ``name`` defaults to the function name and ``outputs`` to the task name. ``metadata`` is an
    open JSON mapping whose keys are validated by the slices that consume them.
    """
    unknown = sorted(set(values) - set(FIELDS))
    if unknown:
        msg = f"unknown task declaration fields: {', '.join(unknown)}"
        raise _fail(msg)
    if "kind" not in values:
        msg = "a task declaration needs kind="
        raise _fail(msg)
    name = _identifier(values.get("name") or function, "task name")
    raw_outputs = values.get("outputs")
    outputs = (name,) if raw_outputs is None else _strings(raw_outputs, "outputs")
    for output in outputs:
        _identifier(output, "output name", placeholders=True)
    metadata = _json_value(values.get("metadata") or {}, "metadata")
    if not isinstance(metadata, dict):
        msg = "metadata must be a mapping"
        raise _fail(msg)
    return TaskDeclaration(
        kind=_identifier(values["kind"], "task kind"),
        name=name,
        outputs=outputs,
        reads=_references(values.get("reads", ()), "reads"),
        external=_strings(values.get("external", ()), "external"),
        state=_references(values.get("state", ()), "state"),
        variants=_variants(values.get("variants", ())),
        enabled=_flag(values.get("enabled", True), "enabled"),
        static=_flag(values.get("static", False), "static"),
        metadata=metadata,
    )


def task[F: Callable[..., object]](  # noqa: PLR0913 - one keyword per documented declaration field
    *,
    kind: str,
    name: str | None = None,
    outputs: str | Sequence[str] | None = None,
    reads: str | Sequence[str] = (),
    external: str | Sequence[str] = (),
    state: str | Sequence[str] = (),
    variants: Sequence[Mapping[str, VariantValue]] = (),
    enabled: bool = True,
    static: bool = False,
    metadata: Mapping[str, object] | None = None,
) -> Callable[[F], F]:
    """Declare the decorated function as a task; arguments must be literals to be discoverable.

    ``kind`` labels behaviour (``table``, ``train``, ``monitor``...) and never decides location:
    the logical scope is the ``<catalog>/<schema>`` folder holding the file. ``outputs`` are short
    names owned in that scope. ``reads`` are other tasks' outputs, ``external`` are resources the
    project does not produce and ``state`` are earlier windows or versions of an output.
    ``variants`` expands the task once per parameter mapping; ``{param}`` placeholders in names
    are replaced per variant. ``static`` marks an output that does not change from run to run.
    """
    values: dict[str, object] = {
        "kind": kind,
        "name": name,
        "outputs": outputs,
        "reads": reads,
        "external": external,
        "state": state,
        "variants": variants,
        "enabled": enabled,
        "static": static,
        "metadata": dict(metadata or {}),
    }

    def decorate(function: F) -> F:
        setattr(function, TASK_ATTRIBUTE, make_declaration(function.__name__, values))
        return function

    return decorate


def declaration_of(function: Callable[..., object]) -> TaskDeclaration | None:
    """Return the declaration attached by :func:`task`, or ``None`` for an ordinary callable."""
    found = getattr(function, TASK_ATTRIBUTE, None)
    return found if isinstance(found, TaskDeclaration) else None
