"""Explicit model dataset metadata: identity, cohort sample, segments, exclusions, features, label, split.

Everything here is plain data (a mapping or a TOML/JSON file) and standard library only. A model is
identified by the string ``model`` declared in its metadata, never by a folder, class path, catalog or
schema; nothing in this module reads a location.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import DatasetError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

METADATA_KEY = "model_dataset"
ROLES = ("population", "features", "labels", "training", "prediction")
OPERATORS = ("eq", "ne", "lt", "le", "gt", "ge", "in", "is_null", "not_null")
TYPE_ALIASES = {
    "int": "int",
    "integer": "int",
    "bigint": "bigint",
    "long": "bigint",
    "smallint": "smallint",
    "short": "smallint",
    "tinyint": "tinyint",
    "byte": "tinyint",
    "float": "float",
    "real": "float",
    "double": "double",
    "string": "string",
    "boolean": "boolean",
    "bool": "boolean",
    "date": "date",
    "timestamp": "timestamp",
}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DECIMAL = re.compile(r"^decimal\(\s*\d+\s*,\s*\d+\s*\)$")


def normalize_type(value: str) -> str:
    """Return the canonical spelling of a column type, so ``integer`` and ``int`` compare equal."""
    text = value.strip().lower().replace(" ", "")
    if _DECIMAL.match(text):
        return text
    return TYPE_ALIASES.get(text, text)


def to_date(value: object) -> date:
    """Return ``value`` as a date; accepts dates, datetimes and ISO strings."""
    if isinstance(value, date):
        return date(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    msg = f"{value!r} is not a date (use YYYY-MM-DD)"
    raise DatasetError(msg)


def _ident(value: object, what: str) -> str:
    if not isinstance(value, str) or not _IDENT.match(value):
        msg = f"{what} must be an identifier, got {value!r}"
        raise DatasetError(msg)
    return value


def _mapping(value: object, what: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        msg = f"{what} must be a mapping, got {type(value).__name__}"
        raise DatasetError(msg)
    return value


def _unknown(data: Mapping[str, Any], allowed: tuple[str, ...], what: str) -> None:
    extra = sorted(set(data) - set(allowed))
    if extra:
        msg = f"{what} has unknown keys: {', '.join(extra)}"
        raise DatasetError(msg)


def check_predicate(node: object, what: str = "predicate") -> dict[str, Any]:  # noqa: C901 - one validation pass
    """Validate a declarative predicate and return it.

    Forms: ``{"column", "op", "value"}`` with ``op`` one of :data:`OPERATORS` (``is_null`` and
    ``not_null`` take no value, ``in`` takes a list), ``{"all": [...]}``, ``{"any": [...]}``, ``{"not": p}``.
    Comparisons with a null column are false.
    """
    data = _mapping(node, what)
    forms = [name for name in ("all", "any", "not") if name in data]
    if forms:
        if len(data) != 1:
            msg = f"{what}: {forms[0]!r} cannot be combined with other keys"
            raise DatasetError(msg)
        inner = data[forms[0]]
        if forms[0] == "not":
            check_predicate(inner, what)
        elif not isinstance(inner, list) or not inner:
            msg = f"{what}: {forms[0]!r} needs a non-empty list"
            raise DatasetError(msg)
        else:
            for item in inner:
                check_predicate(item, what)
        return dict(data)
    _unknown(data, ("column", "op", "value"), what)
    _ident(data.get("column"), f"{what} column")
    op = data.get("op")
    if op not in OPERATORS:
        msg = f"{what}: op must be one of {', '.join(OPERATORS)}, got {op!r}"
        raise DatasetError(msg)
    if op in ("is_null", "not_null"):
        if "value" in data:
            msg = f"{what}: {op} takes no value"
            raise DatasetError(msg)
    elif "value" not in data:
        msg = f"{what}: {op} needs a value"
        raise DatasetError(msg)
    elif op == "in" and not isinstance(data["value"], list):
        msg = f"{what}: in needs a list value"
        raise DatasetError(msg)
    return dict(data)


@dataclass(frozen=True)
class Rule:
    """A named predicate: an exclusion reason or a segment."""

    name: str
    where: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {"name": self.name, "where": dict(self.where)}


@dataclass(frozen=True)
class Feature:
    """One feature column with its declared type."""

    name: str
    type: str


@dataclass(frozen=True)
class Sample:
    """Deterministic sampling by key hash: a key is kept when its seeded hash fraction is below ``fraction``."""

    fraction: float
    seed: int

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {"fraction": self.fraction, "seed": self.seed}


@dataclass(frozen=True)
class Label:
    """The label column; it is observed ``maturity_days`` after the row's snapshot date."""

    name: str
    type: str
    maturity_days: int

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {"maturity_days": self.maturity_days, "name": self.name, "type": self.type}


@dataclass(frozen=True)
class Split:
    """Date-based train/evaluation split on the snapshot date; the window between the two is dropped."""

    train_until: date
    evaluation_from: date
    separate_keys: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {
            "evaluation_from": self.evaluation_from.isoformat(),
            "separate_keys": self.separate_keys,
            "train_until": self.train_until.isoformat(),
        }


@dataclass(frozen=True)
class ModelSpec:
    """The resolved dataset metadata of one model identity."""

    model: str
    key: tuple[str, ...]
    snapshot: str
    feature_time: str
    features: tuple[Feature, ...]
    label: Label
    reference_date: date | None = None
    sample: Sample | None = None
    segments: tuple[Rule, ...] = ()
    exclusions: tuple[Rule, ...] = ()
    split: Split | None = None
    reserved: tuple[str, ...] = field(init=False, default=())

    def __post_init__(self) -> None:
        """Compute the reserved (non-feature) column names and reject collisions."""
        reserved = (*self.key, self.snapshot, self.feature_time, "segment", "split", self.label.name, "label_time")
        object.__setattr__(self, "reserved", reserved)
        names = [f.name for f in self.features]
        if len(set(names)) != len(names) or set(names) & set(reserved):
            msg = f"model {self.model!r}: feature names must be unique and not collide with {', '.join(reserved)}"
            raise DatasetError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical plain form (stable key order, ISO dates)."""
        return {
            "exclusions": [r.to_dict() for r in self.exclusions],
            "feature_time": self.feature_time,
            "features": [{"name": f.name, "type": f.type} for f in self.features],
            "key": list(self.key),
            "label": self.label.to_dict(),
            "model": self.model,
            "reference_date": self.reference_date.isoformat() if self.reference_date else None,
            "sample": self.sample.to_dict() if self.sample else None,
            "segments": [r.to_dict() for r in self.segments],
            "snapshot": self.snapshot,
            "split": self.split.to_dict() if self.split else None,
        }

    def config_hash(self) -> str:
        """Return the SHA-256 of the canonical configuration."""
        return hashlib.sha256(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


def _rules(value: object, what: str) -> tuple[Rule, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = f"{what} must be a list"
        raise DatasetError(msg)
    rules: list[Rule] = []
    for item in value:
        data = _mapping(item, what)
        _unknown(data, ("name", "where"), what)
        name = _ident(data.get("name"), f"{what} name")
        rules.append(Rule(name, check_predicate(data.get("where"), f"{what} {name}")))
    if len({r.name for r in rules}) != len(rules):
        msg = f"{what} names must be unique"
        raise DatasetError(msg)
    return tuple(rules)


def parse_spec(data: Mapping[str, Any]) -> ModelSpec:  # noqa: C901, PLR0915 - one linear validation pass
    """Validate a spec mapping and return the :class:`ModelSpec`."""
    root = _mapping(data, "spec")
    _unknown(
        root,
        ("model", "columns", "reference_date", "sample", "segments", "exclusions", "features", "label", "split"),
        "spec",
    )
    model = root.get("model")
    if not isinstance(model, str) or not _IDENT.match(model):
        msg = f"spec must declare an explicit identifier 'model', got {model!r}"
        raise DatasetError(msg)
    columns = _mapping(root.get("columns", {}), "columns")
    _unknown(columns, ("key", "snapshot", "feature_time"), "columns")
    raw_key = columns.get("key")
    keys = [raw_key] if isinstance(raw_key, str) else raw_key
    if not isinstance(keys, list) or not keys:
        msg = "columns.key must name at least one key column"
        raise DatasetError(msg)
    key = tuple(_ident(k, "key column") for k in keys)
    features_raw = root.get("features")
    if not isinstance(features_raw, list) or not features_raw:
        msg = "spec needs a non-empty ordered 'features' list of {name, type}"
        raise DatasetError(msg)
    features: list[Feature] = []
    for item in features_raw:
        entry = _mapping(item, "feature")
        _unknown(entry, ("name", "type"), "feature")
        kind = entry.get("type")
        if not isinstance(kind, str):
            msg = f"feature {entry.get('name')!r} needs a type"
            raise DatasetError(msg)
        features.append(Feature(_ident(entry.get("name"), "feature name"), normalize_type(kind)))
    label_raw = _mapping(root.get("label"), "label")
    _unknown(label_raw, ("name", "type", "maturity_days"), "label")
    days = label_raw.get("maturity_days")
    if not isinstance(days, int) or isinstance(days, bool) or days < 0:
        msg = "label.maturity_days must be a non-negative integer"
        raise DatasetError(msg)
    label_type = label_raw.get("type")
    if not isinstance(label_type, str):
        msg = "label needs a type"
        raise DatasetError(msg)
    label = Label(_ident(label_raw.get("name"), "label name"), normalize_type(label_type), days)
    sample = None
    if root.get("sample") is not None:
        raw = _mapping(root["sample"], "sample")
        _unknown(raw, ("fraction", "seed"), "sample")
        fraction, seed = raw.get("fraction"), raw.get("seed")
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)) or not 0 < fraction <= 1:
            msg = "sample.fraction must be a number in (0, 1]"
            raise DatasetError(msg)
        if not isinstance(seed, int) or isinstance(seed, bool):
            msg = "sample.seed must be an explicit integer"
            raise DatasetError(msg)
        sample = Sample(float(fraction), seed)
    split = None
    if root.get("split") is not None:
        raw = _mapping(root["split"], "split")
        _unknown(raw, ("train_until", "evaluation_from", "separate_keys"), "split")
        split = Split(
            to_date(raw.get("train_until")), to_date(raw.get("evaluation_from")), bool(raw.get("separate_keys", False))
        )
        if split.evaluation_from <= split.train_until:
            msg = "split.evaluation_from must be after split.train_until"
            raise DatasetError(msg)
    reference = to_date(root["reference_date"]) if root.get("reference_date") is not None else None
    return ModelSpec(
        model=model,
        key=key,
        snapshot=_ident(columns.get("snapshot", "snapshot_date"), "snapshot column"),
        feature_time=_ident(columns.get("feature_time", "feature_time"), "feature_time column"),
        features=tuple(features),
        label=label,
        reference_date=reference,
        sample=sample,
        segments=_rules(root.get("segments"), "segments"),
        exclusions=_rules(root.get("exclusions"), "exclusions"),
        split=split,
    )


def load_spec_file(path: Path | str) -> ModelSpec:
    """Load a ``.toml`` or ``.json`` spec file."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
        data = json.loads(text) if file.suffix == ".json" else tomllib.loads(text)
    except (OSError, ValueError) as error:
        msg = f"cannot read spec file {file.name}: {error}"
        raise DatasetError(msg) from error
    return parse_spec(data)


@dataclass(frozen=True)
class DatasetDeclaration:
    """The ``model_dataset`` task metadata: explicit model id, role and the spec (file reference or inline)."""

    model: str
    role: str
    spec: str | Mapping[str, Any]

    def resolve(self, root: Path | str) -> ModelSpec:
        """Load the spec and require it to declare the same model id as the task."""
        spec = parse_spec(self.spec) if isinstance(self.spec, dict) else load_spec_file(Path(root) / str(self.spec))
        if spec.model != self.model:
            msg = f"task declares model {self.model!r} but its spec declares {spec.model!r}"
            raise DatasetError(msg)
        return spec


def parse_declaration(metadata: Mapping[str, object]) -> DatasetDeclaration | None:
    """Return the dataset declaration in task metadata, or None when the task has none."""
    if METADATA_KEY not in metadata:
        return None
    data = _mapping(metadata[METADATA_KEY], METADATA_KEY)
    _unknown(data, ("model", "role", "spec"), METADATA_KEY)
    model, role, spec = data.get("model"), data.get("role"), data.get("spec")
    if not isinstance(model, str) or not _IDENT.match(model):
        msg = f"{METADATA_KEY}.model must be an explicit identifier, got {model!r}"
        raise DatasetError(msg)
    if role not in ROLES:
        msg = f"{METADATA_KEY}.role must be one of {', '.join(ROLES)}, got {role!r}"
        raise DatasetError(msg)
    if not isinstance(spec, (str, dict)):
        msg = f"{METADATA_KEY}.spec must be a path relative to the project root or an inline mapping"
        raise DatasetError(msg)
    return DatasetDeclaration(model, str(role), spec)


def find_project_root(start: Path | str) -> Path:
    """Walk up from ``start`` to the directory holding ``bricksmith.toml``."""
    here = Path(start).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "bricksmith.toml").is_file():
            return candidate
    msg = f"no bricksmith.toml above {here.name}"
    raise DatasetError(msg)


def dataset_spec(function: Callable[..., object]) -> ModelSpec:
    """Return the spec of a declared dataset task, for use inside the task body."""
    import inspect  # noqa: PLC0415

    from bricksmith.tasks import declaration_of  # noqa: PLC0415

    declared = declaration_of(function)
    if declared is None:
        msg = f"{getattr(function, '__name__', function)!r} is not a @task function"
        raise DatasetError(msg)
    declaration = parse_declaration(declared.metadata)
    if declaration is None:
        msg = f"task {declared.name!r} has no {METADATA_KEY} metadata"
        raise DatasetError(msg)
    source = inspect.getsourcefile(function)
    return declaration.resolve(find_project_root(source) if source else Path.cwd())
