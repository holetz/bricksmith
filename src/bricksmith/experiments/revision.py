"""The experimental revision: new configuration (scope, pins, seeds, parameters), never rewritten source code."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from bricksmith.experiments.errors import ExperimentError
from bricksmith.params import Scalar, validate_parameters

if TYPE_CHECKING:
    from collections.abc import Mapping

NAME = re.compile(r"[a-z][a-z0-9_]{0,40}")
REVISION_VERSION = 1


def _only(data: Mapping[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        msg = f"unknown key(s) in {where}: {', '.join(extra)}"
        raise ExperimentError(msg)


@dataclass(frozen=True)
class Pin:
    """Freeze one input at a Delta ``version``, at a ``timestamp`` or at ``current`` (exactly one)."""

    input: str
    version: int | None = None
    timestamp: str | None = None
    current: bool = False

    def __post_init__(self) -> None:
        """Require exactly one way to name the frozen state."""
        chosen = [self.version is not None, self.timestamp is not None, self.current]
        if sum(chosen) != 1 or not self.input:
            msg = f"pin {self.input!r} needs exactly one of version, timestamp or current = true"
            raise ExperimentError(msg)
        if self.version is not None and self.version < 0:
            msg = f"pin {self.input!r}: version must not be negative"
            raise ExperimentError(msg)

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        data: dict[str, object] = {"input": self.input}
        if self.version is not None:
            data["version"] = self.version
        if self.timestamp is not None:
            data["timestamp"] = self.timestamp
        if self.current:
            data["current"] = True
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Pin:
        """Rebuild a pin from :meth:`to_dict` output."""
        _only(data, {"input", "version", "timestamp", "current"}, "a pin")
        return cls(str(data.get("input", "")), data.get("version"), data.get("timestamp"), bool(data.get("current")))


@dataclass(frozen=True)
class Seed:
    """Copy an output's baseline history (optionally at ``version``) into the isolated destination before running."""

    output: str
    version: int | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        data: dict[str, object] = {"output": self.output}
        if self.version is not None:
            data["version"] = self.version
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Seed:
        """Rebuild a seed from :meth:`to_dict` output."""
        _only(data, {"output", "version"}, "a seed")
        if not data.get("output"):
            msg = "a seed needs an output"
            raise ExperimentError(msg)
        return cls(str(data["output"]), data.get("version"))


@dataclass(frozen=True)
class Backfill:
    """A historical window the revision recomputes; stateful tasks then need their earlier periods seeded."""

    start: str
    end: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {"end": self.end, "start": self.start}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Backfill:
        """Rebuild a backfill from :meth:`to_dict` output."""
        _only(data, {"start", "end"}, "backfill")
        if not data.get("start"):
            msg = "backfill needs a start"
            raise ExperimentError(msg)
        return cls(str(data["start"]), data.get("end"))


@dataclass(frozen=True)
class CompareSpec:
    """How one rebuilt output is compared with its baseline."""

    output: str
    keys: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    absolute: float = 0.0
    relative: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """Return the serializable form."""
        return {
            "absolute": self.absolute,
            "exclude": list(self.exclude),
            "keys": list(self.keys),
            "output": self.output,
            "relative": self.relative,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CompareSpec:
        """Rebuild a comparison spec from :meth:`to_dict` output."""
        _only(data, {"output", "keys", "exclude", "absolute", "relative"}, "a compare entry")
        keys = tuple(data.get("keys") or ())
        if not data.get("output") or not keys:
            msg = "a compare entry needs an output and at least one key"
            raise ExperimentError(msg)
        return cls(
            str(data["output"]),
            keys,
            tuple(data.get("exclude") or ()),
            float(data.get("absolute", 0.0)),
            float(data.get("relative", 0.0)),
        )


@dataclass(frozen=True)
class Revision:
    """One experimental revision; names the tasks to rebuild and how their inputs and destination behave."""

    name: str
    roots: tuple[str, ...]
    downstream: bool = False
    suffix: str | None = None
    parameters: Mapping[str, Scalar] = field(default_factory=dict)
    pins: tuple[Pin, ...] = ()
    seeds: tuple[Seed, ...] = ()
    backfill: Backfill | None = None
    compare: tuple[CompareSpec, ...] = ()
    retain: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate names and freeze the parameters."""
        if not NAME.fullmatch(self.name):
            msg = f"revision name {self.name!r} must match [a-z][a-z0-9_]{{0,40}}"
            raise ExperimentError(msg)
        if not self.roots:
            msg = "a revision needs at least one root task"
            raise ExperimentError(msg)
        if self.suffix is not None and not re.fullmatch(r"[A-Za-z0-9_]+", self.suffix):
            msg = f"isolation suffix {self.suffix!r} must use letters, digits and underscores"
            raise ExperimentError(msg)
        object.__setattr__(
            self, "parameters", MappingProxyType(dict(sorted(validate_parameters(self.parameters).items())))
        )

    @property
    def schema_suffix(self) -> str:
        """Return the text appended to each rebuilt task's physical schema."""
        return self.suffix if self.suffix is not None else f"_x_{self.name}"

    def to_dict(self) -> dict[str, object]:
        """Return the sorted, serializable form."""
        return {
            "backfill": self.backfill.to_dict() if self.backfill else None,
            "compare": [c.to_dict() for c in self.compare],
            "downstream": self.downstream,
            "name": self.name,
            "parameters": dict(self.parameters),
            "pins": [p.to_dict() for p in self.pins],
            "retain": list(self.retain),
            "roots": list(self.roots),
            "seeds": [s.to_dict() for s in self.seeds],
            "suffix": self.suffix,
            "version": REVISION_VERSION,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Revision:
        """Rebuild a revision from :meth:`to_dict` output or a parsed revision file."""
        _only(
            data,
            {
                "name",
                "roots",
                "downstream",
                "suffix",
                "parameters",
                "pins",
                "seeds",
                "backfill",
                "compare",
                "retain",
                "version",
            },
            "the revision",
        )
        backfill = data.get("backfill")
        return cls(
            name=str(data.get("name", "")),
            roots=tuple(data.get("roots") or ()),
            downstream=bool(data.get("downstream", False)),
            suffix=data.get("suffix"),
            parameters=dict(data.get("parameters") or {}),
            pins=tuple(Pin.from_dict(p) for p in data.get("pins") or ()),
            seeds=tuple(Seed.from_dict(s) for s in data.get("seeds") or ()),
            backfill=Backfill.from_dict(backfill) if backfill else None,
            compare=tuple(CompareSpec.from_dict(c) for c in data.get("compare") or ()),
            retain=tuple(data.get("retain") or ()),
        )


def load_revision(text: str, *, fmt: str = "toml") -> Revision:
    """Parse a revision file (``toml`` or ``json``) into a validated :class:`Revision`."""
    try:
        data = tomllib.loads(text) if fmt == "toml" else json.loads(text)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError) as error:
        msg = f"the revision file is not valid {fmt}: {error}"
        raise ExperimentError(msg) from error
    if not isinstance(data, dict):
        msg = "the revision file must hold a table/object"
        raise ExperimentError(msg)
    return Revision.from_dict(data)
