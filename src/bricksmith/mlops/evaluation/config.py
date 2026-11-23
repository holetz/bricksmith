"""The ``model_evaluation`` declaration: which columns hold the score, label and grade, and which samples to compare.

Standard library only. Every sample is measured against the reference sample (the training sample by default):
the score, the grade and the declared features get bins fitted on the reference, so PSI and CSI compare each
sample with it. Unknown keys are refused so a misspelled option never silently falls back to its default.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn

from bricksmith.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.mlops.stages.config import Development

METADATA_KEY = "model_evaluation"
INCREASING = "increasing"
DECREASING = "decreasing"
DIRECTIONS = (INCREASING, DECREASING)
KEYS = frozenset({
    "name", "score", "label", "probability", "grade", "grades", "direction", "sample", "closing", "reference",
    "samples", "features", "categorical", "feature_bins", "score_bins", "calibration_bins", "per_closing", "epsilon",
})  # fmt: skip
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class EvaluationError(ValidationError):
    """A model evaluation declaration or input is invalid."""

    code = "evaluation_invalid"


def fail(message: str) -> NoReturn:
    """Raise an :class:`EvaluationError` prefixed with ``model_evaluation:``."""
    msg = f"{METADATA_KEY}: {message}"
    raise EvaluationError(msg)


def _ident(value: object, what: str) -> str:
    if isinstance(value, str) and _IDENT.fullmatch(value):
        return value
    fail(f"{what} must be an identifier, got {value!r}")


def _names(value: object, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        fail(f"{what} must be a list")
    names = tuple(_ident(v, f"{what} entry") for v in value)
    if len(set(names)) != len(names):
        fail(f"{what} repeat a name: {list(names)!r}")
    return names


def _labels(value: object, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        fail(f"{what} must be a list of non-empty names")
    labels = tuple(str(v) for v in value)
    if len(set(labels)) != len(labels):
        fail(f"{what} repeat a name: {list(labels)!r}")
    return labels


def _int(value: object, what: str, minimum: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= minimum:
        return value
    fail(f"{what} must be a whole number >= {minimum}, got {value!r}")


@dataclass(frozen=True)
class EvaluationConfig:
    """A validated evaluation declaration."""

    score: str
    label: str
    name: str = "evaluation"
    probability: str = ""
    grade: str = ""
    grades: tuple[str, ...] = ()
    direction: str = INCREASING
    sample: str = "sample"
    closing: str = "closing_date"
    reference: str = "train"
    samples: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    categorical: tuple[str, ...] = ()
    feature_bins: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    score_bins: int = 10
    calibration_bins: int = 10
    per_closing: bool = True
    epsilon: float = 1e-4

    @property
    def probability_column(self) -> str:
        """The column read as the event probability: ``probability`` when declared, else the score."""
        return self.probability or self.score

    def bins_of(self, feature: str) -> dict[str, Any]:
        """Return the bin configuration of a feature: declared, else categorical or ten quantile bins."""
        if feature in self.feature_bins:
            return dict(self.feature_bins[feature])
        if feature in self.categorical:
            return {"type": "categorical", "max_categories": 50}
        return {"type": "numeric", "method": "quantile", "bins": 10}

    def to_dict(self) -> dict[str, Any]:
        """Return the plain, JSON-serializable form."""
        return {
            "calibration_bins": self.calibration_bins,
            "categorical": list(self.categorical),
            "closing": self.closing,
            "direction": self.direction,
            "epsilon": self.epsilon,
            "feature_bins": {k: dict(v) for k, v in sorted(self.feature_bins.items())},
            "features": list(self.features),
            "grade": self.grade,
            "grades": list(self.grades),
            "label": self.label,
            "name": self.name,
            "per_closing": self.per_closing,
            "probability": self.probability,
            "reference": self.reference,
            "sample": self.sample,
            "samples": list(self.samples),
            "score": self.score,
            "score_bins": self.score_bins,
        }

    def fingerprint(self) -> str:
        """Return a short stable hash of the declaration."""
        text = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(text.encode()).hexdigest()[:16]


def _feature_bins(value: object, features: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(v, dict) for v in value.values()):
        fail("feature_bins must map a feature to its bin configuration")
    unknown = sorted(set(value) - set(features))
    if unknown:
        fail(f"feature_bins name columns that are not features: {', '.join(unknown)}")
    return {str(k): dict(v) for k, v in value.items()}


def _grades(data: Mapping[str, Any], grade: str, development: Development | None) -> tuple[str, ...]:
    grades = _labels(data.get("grades"), "grades")
    if not grades and grade and development is not None:
        grades = tuple(b.name for b in development.bands)
    if grades and not grade:
        fail("grades need a grade column")
    return grades


def _check_roles(roles: list[str], features: tuple[str, ...], categorical: tuple[str, ...]) -> None:
    if set(categorical) - set(features):
        fail(f"categorical name columns that are not features: {', '.join(sorted(set(categorical) - set(features)))}")
    columns = [*roles, *features]
    clash = sorted({c for c in columns if columns.count(c) > 1})
    if clash:
        fail(f"a column plays two roles: {', '.join(clash)}")


def _options(data: Mapping[str, Any]) -> tuple[str, bool, float]:
    direction = data.get("direction", INCREASING)
    if direction not in DIRECTIONS:
        fail(f"direction must be one of {', '.join(DIRECTIONS)}, got {direction!r}")
    per_closing = data.get("per_closing", True)
    if not isinstance(per_closing, bool):
        fail(f"per_closing must be true or false, got {per_closing!r}")
    epsilon = data.get("epsilon", 1e-4)
    if isinstance(epsilon, bool) or not isinstance(epsilon, (int, float)) or not 0 < epsilon < 1:
        fail(f"epsilon must be a number in (0, 1), got {epsilon!r}")
    return str(direction), per_closing, float(epsilon)


def parse_config(value: object, development: Development | None = None) -> EvaluationConfig:
    """Validate a ``model_evaluation`` mapping; a ``development`` supplies the closing and the grade order (bands)."""
    if isinstance(value, EvaluationConfig):
        return value
    if not isinstance(value, dict):
        fail(f"the declaration must be a mapping, got {type(value).__name__}")
    unknown = sorted(set(value) - KEYS)
    if unknown:
        fail(f"unknown keys: {', '.join(unknown)}")
    data: dict[str, Any] = value
    score = _ident(data.get("score"), "score")
    label = _ident(data.get("label"), "label")
    probability = _ident(data["probability"], "probability") if data.get("probability") else ""
    probability = "" if probability == score else probability
    grade = _ident(data["grade"], "grade") if data.get("grade") else ""
    grades = _grades(data, grade, development)
    direction, per_closing, epsilon = _options(data)
    default_closing = development.closing if development is not None else "closing_date"
    closing = _ident(data.get("closing", default_closing), "closing")
    sample = _ident(data.get("sample", "sample"), "sample")
    features = _names(data.get("features"), "features")
    categorical = _names(data.get("categorical"), "categorical")
    roles = [c for c in (score, label, grade, sample, closing, probability) if c]
    _check_roles(roles, features, categorical)
    reference = _labels([data.get("reference", "train")], "reference")[0]
    samples = _labels(data.get("samples"), "samples")
    if samples and reference not in samples:
        samples = (reference, *samples)
    return EvaluationConfig(
        score=score,
        label=label,
        name=_labels([data.get("name", "evaluation")], "name")[0],
        probability=probability,
        grade=grade,
        grades=grades,
        direction=direction,
        sample=sample,
        closing=closing,
        reference=reference,
        samples=samples,
        features=features,
        categorical=categorical,
        feature_bins=_feature_bins(data.get("feature_bins"), features),
        score_bins=_int(data.get("score_bins", 10), "score_bins", 1),
        calibration_bins=_int(data.get("calibration_bins", 10), "calibration_bins", 1),
        per_closing=per_closing,
        epsilon=epsilon,
    )


__all__ = [
    "DECREASING",
    "DIRECTIONS",
    "INCREASING",
    "METADATA_KEY",
    "EvaluationConfig",
    "EvaluationError",
    "fail",
    "parse_config",
]
