"""The ``model_development`` declaration: named samples, the label, the exclusion cascade and the prediction contract.

Standard library only. Samples are inclusive windows of closings of one frequency (the feature store's closing
calendar); each has a role. The declaration refuses an out-of-time window that overlaps or precedes a training
window, and a labelled sample whose label has not matured at ``as_of`` by the sample's upper closing.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import DatasetError
from bricksmith.mlops.featurestore.config import FREQUENCIES, MONTHLY, Lookup, require_closing, shift, to_day
from bricksmith.mlops.spec import check_predicate

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date

METADATA_KEY = "model_development"
TRAIN = "train"
TEST = "test"
OUT_OF_TIME = "out_of_time"
THROUGH_THE_DOOR = "through_the_door"
PREDICTION = "prediction"
MONITORING = "monitoring"
ROLES = (TRAIN, TEST, OUT_OF_TIME, THROUGH_THE_DOOR, PREDICTION, MONITORING)
#: Roles whose rows carry a label, so the label must be mature at the sample's upper closing.
LABELLED = frozenset({TRAIN, TEST, OUT_OF_TIME})
TRAINING = "training"
SCORING = "scoring"
PURPOSES = (TRAINING, SCORING)
#: Columns the exclusion cascade adds to every population row.
EXCLUSION_RULES = "exclusion_rules"
EXCLUSION_FIRST = "exclusion_first"
EXCLUDED = "excluded"
CASCADE_COLUMNS = (EXCLUSION_RULES, EXCLUSION_FIRST, EXCLUDED)
#: Columns the sample selection adds: the sample name and its role.
SAMPLE = "sample"
SAMPLE_ROLE = "sample_role"
#: Prediction contract columns after the entity keys and the closing.
MODEL_NAME = "model_name"
MODEL_VERSION = "model_version"
MODEL_ALIAS = "model_alias"
SCORE = "score"
GRADE = "grade"
PREDICTION_COLUMNS = (MODEL_NAME, MODEL_VERSION, MODEL_ALIAS, SCORE, GRADE, SAMPLE, SAMPLE_ROLE, *CASCADE_COLUMNS)
KEYS = frozenset(
    {"model", "entity_keys", "closing", "frequency", "as_of", "samples", "label", "exclusions", "split",
     "stratify", "bands", "lookups"}
)  # fmt: skip
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _fail(message: str) -> DatasetError:
    return DatasetError(f"{METADATA_KEY}: {message}")


def _ident(value: object, what: str) -> str:
    if isinstance(value, str) and _IDENT.fullmatch(value):
        return value
    msg = f"{what} must be an identifier, got {value!r}"
    raise _fail(msg)


def _mapping(value: object, what: str, allowed: frozenset[str] | set[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        msg = f"{what} must be a mapping, got {type(value).__name__}"
        raise _fail(msg)
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        msg = f"{what} has unknown keys: {', '.join(unknown)}"
        raise _fail(msg)
    return value


def _int(value: object, what: str, minimum: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
        return value
    msg = f"{what} must be a whole number >= {minimum}, got {value!r}"
    raise _fail(msg)


def _fraction(value: object, what: str) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1:
        return float(value)
    msg = f"{what} must be a number in (0, 1], got {value!r}"
    raise _fail(msg)


def _closing(value: object, frequency: str, what: str) -> date:
    try:
        return require_closing(to_day(value, what), frequency, what)
    except DatasetError as error:
        raise _fail(str(error)) from error


@dataclass(frozen=True)
class SampleWindow:
    """A named sample: an inclusive window of closings and the role its rows play."""

    name: str
    role: str
    start: date
    end: date

    @property
    def labelled(self) -> bool:
        """Whether rows of this sample carry a label."""
        return self.role in LABELLED

    def contains(self, day: date) -> bool:
        """Whether ``day`` is inside the window."""
        return self.start <= day <= self.end

    def overlaps(self, other: SampleWindow) -> bool:
        """Whether the two windows share a closing."""
        return self.start <= other.end and other.start <= self.end

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {"end": self.end.isoformat(), "name": self.name, "role": self.role, "start": self.start.isoformat()}


@dataclass(frozen=True)
class Label:
    """The label: computed over ``horizon`` periods after the observation closing, as ``definition`` says."""

    name: str
    horizon: int
    definition: str

    def matures(self, closing: date, frequency: str) -> date:
        """Return the closing at which the label of an observation at ``closing`` is fully observed."""
        return shift(closing, self.horizon, frequency)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {"definition": self.definition, "horizon": self.horizon, "name": self.name}


@dataclass(frozen=True)
class ExclusionRule:
    """One step of the exclusion cascade: a name and a predicate (the dataset predicate grammar)."""

    name: str
    where: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {"name": self.name, "where": dict(self.where)}


@dataclass(frozen=True)
class Split:
    """How rows on closings shared by a train and a test window are divided: a seeded hash below ``test_fraction``."""

    seed: int
    test_fraction: float

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {"seed": self.seed, "test_fraction": self.test_fraction}


@dataclass(frozen=True)
class Stratify:
    """Deterministic sampling by label: a row is kept when its seeded hash is below its label value's fraction.

    ``fractions`` maps a label value written as text to a fraction; a value not listed keeps every row.
    """

    seed: int
    fractions: Mapping[str, float]

    def fraction(self, value: object) -> float:
        """Return the fraction kept for a label value."""
        return self.fractions.get(label_text(value), 1.0)

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {"fractions": dict(sorted(self.fractions.items())), "seed": self.seed}


@dataclass(frozen=True)
class Band:
    """A grade: scores up to ``upper`` (inclusive) that no earlier band took; the last band is open (``None``)."""

    name: str
    upper: float | None = None


def label_text(value: object) -> str:
    """Return a label value as the text that keys :attr:`Stratify.fractions` (``1.0`` and ``1`` read ``1``)."""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass(frozen=True)
class Development:
    """A validated model development declaration."""

    model: str
    entity_keys: tuple[str, ...]
    samples: tuple[SampleWindow, ...]
    label: Label | None = None
    closing: str = "closing_date"
    frequency: str = MONTHLY
    as_of: date | None = None
    exclusions: tuple[ExclusionRule, ...] = ()
    split: Split | None = None
    stratify: Stratify | None = None
    bands: tuple[Band, ...] = ()
    lookups: tuple[Lookup, ...] = ()

    @property
    def spine_keys(self) -> tuple[str, ...]:
        """The entity keys followed by the closing column: one population row per value."""
        return (*self.entity_keys, self.closing)

    def sample(self, name: str) -> SampleWindow:
        """Return the declared sample ``name``."""
        for item in self.samples:
            if item.name == name:
                return item
        msg = f"sample {name!r} is not declared (declared: {', '.join(s.name for s in self.samples)})"
        raise _fail(msg)

    def shared(self, sample: SampleWindow) -> tuple[SampleWindow, ...]:
        """Return the windows of the other split role (train ↔ test) that share closings with ``sample``."""
        other = {TRAIN: TEST, TEST: TRAIN}.get(sample.role)
        return tuple(s for s in self.samples if s.role == other and s.overlaps(sample))

    def grade(self, score: float | None) -> str | None:
        """Return the band of ``score`` (``None`` when no bands are declared or the score is null)."""
        if score is None or not self.bands:
            return None
        for band in self.bands:
            if band.upper is None or score <= band.upper:
                return band.name
        return None  # pragma: no cover - the last band is open

    def to_dict(self) -> dict[str, Any]:
        """Return the serializable form."""
        return {
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "bands": [{"name": b.name, "upper": b.upper} for b in self.bands],
            "closing": self.closing,
            "entity_keys": list(self.entity_keys),
            "exclusions": [r.to_dict() for r in self.exclusions],
            "frequency": self.frequency,
            "label": self.label.to_dict() if self.label else None,
            "lookups": [
                {"features": list(x.features), "keys": list(x.keys), "offset": x.offset, "prefix": x.prefix}
                | {"table": x.table}
                for x in self.lookups
            ],
            "model": self.model,
            "samples": [s.to_dict() for s in self.samples],
            "split": self.split.to_dict() if self.split else None,
            "stratify": self.stratify.to_dict() if self.stratify else None,
        }


def _samples(value: object, frequency: str) -> tuple[SampleWindow, ...]:
    if not isinstance(value, dict) or not value:
        msg = "samples must be a non-empty mapping of name to {role, start, end}"
        raise _fail(msg)
    samples: list[SampleWindow] = []
    for name, raw in value.items():
        what = f"sample {name!r}"
        checked = _ident(name, "sample name")
        data = _mapping(raw, what, {"role", "start", "end"})
        role = data.get("role")
        if role not in ROLES:
            msg = f"{what}: role must be one of {', '.join(ROLES)}, got {role!r}"
            raise _fail(msg)
        start = _closing(data.get("start"), frequency, f"{what} start")
        end = _closing(data.get("end"), frequency, f"{what} end")
        if start > end:
            msg = f"{what}: start {start.isoformat()} is after end {end.isoformat()}"
            raise _fail(msg)
        samples.append(SampleWindow(checked, str(role), start, end))
    return tuple(samples)


def _check_windows(samples: tuple[SampleWindow, ...], split: Split | None) -> None:
    trains = [s for s in samples if s.role == TRAIN]
    for oot in (s for s in samples if s.role == OUT_OF_TIME):
        for train in trains:
            if oot.start <= train.end:
                msg = (
                    f"out-of-time sample {oot.name!r} ({oot.start.isoformat()}..{oot.end.isoformat()}) must start "
                    f"after the last closing of training sample {train.name!r} ({train.end.isoformat()})"
                )
                raise _fail(msg)
    for test in (s for s in samples if s.role == TEST):
        shared = [t.name for t in trains if t.overlaps(test)]
        if shared and split is None:
            msg = f"test sample {test.name!r} shares closings with {', '.join(shared)}; declare split to divide them"
            raise _fail(msg)


def _label(value: object) -> Label | None:
    if value is None:
        return None
    data = _mapping(value, "label", {"name", "horizon", "definition"})
    definition = data.get("definition")
    if not isinstance(definition, str) or not definition.strip():
        msg = "label.definition must say how the label is computed over its forward window"
        raise _fail(msg)
    return Label(_ident(data.get("name"), "label.name"), _int(data.get("horizon"), "label.horizon", 1),
                 definition.strip())  # fmt: skip


def _check_maturity(samples: tuple[SampleWindow, ...], label: Label | None, as_of: date | None, frequency: str) -> None:
    labelled = [s for s in samples if s.labelled]
    if not labelled:
        return
    if label is None or as_of is None:
        msg = f"labelled samples ({', '.join(s.name for s in labelled)}) need a label and as_of"
        raise _fail(msg)
    for sample in labelled:
        matures = label.matures(sample.end, frequency)
        if matures > as_of:
            msg = (
                f"sample {sample.name!r}: the label of its upper closing {sample.end.isoformat()} matures at "
                f"{matures.isoformat()} ({label.horizon} periods), after as_of {as_of.isoformat()}"
            )
            raise _fail(msg)


def _exclusions(value: object) -> tuple[ExclusionRule, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = "exclusions must be an ordered list of {name, where}"
        raise _fail(msg)
    rules: list[ExclusionRule] = []
    for item in value:
        data = _mapping(item, "exclusion", {"name", "where"})
        name = _ident(data.get("name"), "exclusion name")
        rules.append(ExclusionRule(name, check_predicate(data.get("where"), f"exclusion {name}")))
    names = [r.name for r in rules]
    if len(set(names)) != len(names):
        msg = f"exclusion names must be unique: {names!r}"
        raise _fail(msg)
    return tuple(rules)


def _split(value: object) -> Split | None:
    if value is None:
        return None
    data = _mapping(value, "split", {"seed", "test_fraction"})
    fraction = _fraction(data.get("test_fraction"), "split.test_fraction")
    if fraction >= 1:
        msg = "split.test_fraction must be below 1, or no row would stay in training"
        raise _fail(msg)
    return Split(_int(data.get("seed"), "split.seed", 0), fraction)


def _stratify(value: object) -> Stratify | None:
    if value is None:
        return None
    data = _mapping(value, "stratify", {"seed", "fractions"})
    raw = data.get("fractions")
    if not isinstance(raw, dict) or not raw:
        msg = "stratify.fractions must map label values to fractions in (0, 1]"
        raise _fail(msg)
    fractions = {label_text(k): _fraction(v, f"stratify.fractions[{k!r}]") for k, v in raw.items()}
    return Stratify(_int(data.get("seed"), "stratify.seed", 0), fractions)


def _bands(value: object) -> tuple[Band, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        msg = "bands must be a non-empty ordered list of {name, upper}; the last band has no upper"
        raise _fail(msg)
    bands: list[Band] = []
    for index, item in enumerate(value):
        data = _mapping(item, "band", {"name", "upper"})
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            msg = f"band {index} needs a name"
            raise _fail(msg)
        upper = data.get("upper")
        last = index == len(value) - 1
        if last and upper is not None:
            msg = f"the last band {name!r} must be open (no upper) so every score has a grade"
            raise _fail(msg)
        bound: float | None = None
        if not last:
            if isinstance(upper, bool) or not isinstance(upper, (int, float)):
                msg = f"band {name!r} needs a numeric upper bound"
                raise _fail(msg)
            bound = float(upper)
            previous = bands[-1].upper if bands else None
            if previous is not None and bound <= previous:
                msg = f"band {name!r}: upper bounds must increase"
                raise _fail(msg)
        bands.append(Band(name.strip(), bound))
    if len({b.name for b in bands}) != len(bands):
        msg = "band names must be unique"
        raise _fail(msg)
    return tuple(bands)


def _lookups(value: object) -> tuple[Lookup, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = "lookups must be a list of {table, features, keys, offset, prefix}"
        raise _fail(msg)
    lookups: list[Lookup] = []
    for item in value:
        data = _mapping(item, "lookup", {"table", "features", "keys", "offset", "prefix"})
        lookups.append(
            Lookup(
                table=str(data.get("table", "")),
                features=tuple(data.get("features", ())),
                keys=tuple(data.get("keys", ())),
                offset=data.get("offset", 0),
                prefix=str(data.get("prefix", "")),
            )
        )
    return tuple(lookups)


def _entity_keys(value: object) -> tuple[str, ...]:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items:
        msg = "entity_keys must be a non-empty list of column names"
        raise _fail(msg)
    keys = tuple(_ident(item, "entity key") for item in items)
    if len(set(keys)) != len(keys):
        msg = f"entity_keys repeat a column: {list(keys)!r}"
        raise _fail(msg)
    return keys


def parse_development(value: object) -> Development:
    """Validate a ``model_development`` mapping into a :class:`Development`."""
    data = _mapping(value, "the declaration", KEYS)
    model = _ident(data.get("model"), "model")
    frequency = data.get("frequency", MONTHLY)
    if frequency not in FREQUENCIES:
        msg = f"frequency must be one of {', '.join(FREQUENCIES)}, got {frequency!r}"
        raise _fail(msg)
    entity_keys = _entity_keys(data.get("entity_keys"))
    closing = _ident(data.get("closing", "closing_date"), "closing")
    if closing in entity_keys:
        msg = f"closing {closing!r} is also an entity key"
        raise _fail(msg)
    samples = _samples(data.get("samples"), str(frequency))
    split = _split(data.get("split"))
    _check_windows(samples, split)
    label = _label(data.get("label"))
    as_of = _closing(data["as_of"], str(frequency), "as_of") if data.get("as_of") is not None else None
    _check_maturity(samples, label, as_of, str(frequency))
    taken = [*entity_keys, closing, *CASCADE_COLUMNS, SAMPLE, SAMPLE_ROLE, *([label.name] if label else [])]
    if len(set(taken)) != len(taken):
        repeated = sorted({n for n in taken if taken.count(n) > 1})
        msg = f"a column name is used twice across keys, closing, label and reserved columns: {', '.join(repeated)}"
        raise _fail(msg)
    stratify = _stratify(data.get("stratify"))
    if stratify is not None and label is None:
        msg = "stratify needs a label"
        raise _fail(msg)
    return Development(
        model=model,
        entity_keys=entity_keys,
        samples=samples,
        label=label,
        closing=closing,
        frequency=str(frequency),
        as_of=as_of,
        exclusions=_exclusions(data.get("exclusions")),
        split=split,
        stratify=stratify,
        bands=_bands(data.get("bands")),
        lookups=_lookups(data.get("lookups")),
    )


def load_development_file(path: Path | str) -> Development:
    """Load a ``.toml`` or ``.json`` declaration file."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
        data = json.loads(text) if file.suffix == ".json" else tomllib.loads(text)
    except (OSError, ValueError) as error:
        msg = f"cannot read {file.name}: {error}"
        raise _fail(msg) from error
    return parse_development(data)
