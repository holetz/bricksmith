"""The feature selection configuration: samples, steps and their thresholds, expected signs and judgment.

Every threshold is configuration with a default, never a hard truth. ``parse_config`` refuses unknown keys so a
misspelled threshold never silently falls back to its default.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, NoReturn

from bricksmith.errors import ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

QUALITY = "quality"
STABILITY = "stability"
UNIVARIATE = "univariate"
REDUNDANCY = "redundancy"
MULTIVARIATE = "multivariate"
JUDGMENT = "judgment"
#: The funnel steps, in the order they run.
STEPS = (QUALITY, STABILITY, UNIVARIATE, REDUNDANCY, MULTIVARIATE, JUDGMENT)
#: Steps that run unless disabled; ``multivariate`` needs the ``mlops`` extra and is opt-in.
DEFAULT_STEPS = {QUALITY: True, STABILITY: True, UNIVARIATE: True, REDUNDANCY: True, MULTIVARIATE: False,
                 JUDGMENT: True}  # fmt: skip
BINARY = "binary"
CONTINUOUS = "continuous"
TARGET_KINDS = (BINARY, CONTINUOUS)
QUANTILE = "quantile"
MONOTONE = "monotone"
BINNINGS = (QUANTILE, MONOTONE)
CRITICAL = "critical"
SIGNS = ("+", "-")
KEYS = frozenset({
    "name", "closing", "target", "target_kind", "development", "out_of_time", "features", "exclude",
    "categorical", "steps", "quality", "stability", "univariate", "redundancy", "multivariate", "expected_signs",
    "judgment", "sample_rows", "seed",
})  # fmt: skip


class SelectionError(ValidationError):
    """A feature selection configuration or input is invalid."""

    code = "selection_invalid"


def fail(message: str) -> NoReturn:
    """Raise a :class:`SelectionError` prefixed with ``selection:``."""
    msg = f"selection: {message}"
    raise SelectionError(msg)


@dataclass(frozen=True)
class ClosingRange:
    """An inclusive range of closings, compared as ISO text (``2026-01-31`` sorts like the date)."""

    start: str
    end: str

    def contains(self, closing: str) -> bool:
        """Return whether ``closing`` lies in ``[start, end]``."""
        return self.start <= closing <= self.end


@dataclass(frozen=True)
class QualityConfig:
    """Drop thresholds of the quality step (shares over the development sample)."""

    max_missing: float | None = 0.95
    max_constant: float | None = 0.99
    max_categories: int | None = 50


@dataclass(frozen=True)
class StabilityConfig:
    """Stability thresholds: PSI development vs out-of-time and month over month, over development-fitted bins.

    ``psi_max`` is a number, ``None`` (report only) or ``"critical"``: the sample-size-aware critical value
    ``chi2_{B-1}(1 - alpha) * (1/N + 1/M)`` for each comparison.
    """

    psi_max: float | str | None = 0.25
    alpha: float = 0.05
    month_over_month: bool = True
    bins: int = 10
    max_categories: int = 20


@dataclass(frozen=True)
class UnivariateConfig:
    """Univariate power thresholds; ``None`` reports the metric without judging it."""

    binning: str = QUANTILE
    bins: int = 10
    max_categories: int = 20
    smoothing: float = 0.5
    min_iv: float | None = 0.02
    min_ks: float | None = None
    min_gini: float | None = None
    min_spearman: float | None = 0.05
    min_eta: float | None = 0.05


@dataclass(frozen=True)
class RedundancyConfig:
    """Pairwise correlation clustering, then iterative VIF on the survivors."""

    max_correlation: float | None = 0.8
    max_vif: float | None = 10.0


@dataclass(frozen=True)
class MultivariateConfig:
    """Penalized model and permutation importance (needs the ``mlops`` extra)."""

    c: float = 1.0
    alpha: float = 0.01
    l1_ratio: float = 1.0
    min_importance: float | None = 0.0
    repeats: int = 5


@dataclass(frozen=True)
class JudgmentConfig:
    """Declared manual decisions: ``variable -> reason``."""

    keep: Mapping[str, str] = field(default_factory=dict)
    drop: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SelectionConfig:
    """A parsed feature selection configuration."""

    closing: str
    target: str
    development: ClosingRange
    out_of_time: ClosingRange | None = None
    name: str = "selection"
    target_kind: str = BINARY
    features: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    categorical: tuple[str, ...] = ()
    steps: Mapping[str, bool] = field(default_factory=lambda: dict(DEFAULT_STEPS))
    quality: QualityConfig = QualityConfig()
    stability: StabilityConfig = StabilityConfig()
    univariate: UnivariateConfig = UnivariateConfig()
    redundancy: RedundancyConfig = RedundancyConfig()
    multivariate: MultivariateConfig = MultivariateConfig()
    expected_signs: Mapping[str, str] = field(default_factory=dict)
    judgment: JudgmentConfig = JudgmentConfig()
    sample_rows: int = 50_000
    seed: int = 0

    def enabled(self, step: str) -> bool:
        """Return whether ``step`` runs."""
        return bool(self.steps.get(step, False))

    def candidates(self, columns: tuple[str, ...]) -> tuple[str, ...]:
        """Return the candidate variables: ``features`` when declared, else every column but the reserved ones."""
        if self.features:
            missing = [c for c in self.features if c not in columns]
            if missing:
                fail(f"features not in the input: {', '.join(missing)}")
            return self.features
        reserved = {self.closing, self.target, *self.exclude}
        return tuple(c for c in columns if c not in reserved)

    def to_dict(self) -> dict[str, Any]:
        """Return the plain, JSON-serializable form."""
        return asdict(self)

    def fingerprint(self) -> str:
        """Return a short stable hash of the configuration."""
        text = json.dumps(self.to_dict(), sort_keys=True, default=str)
        return hashlib.sha256(text.encode()).hexdigest()[:16]


def _section[T](cls: type[T], name: str, value: object) -> T:
    if value is None:
        return cls()
    if not isinstance(value, dict):
        fail(f"{name} must be a mapping")
    known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
    unknown = sorted(set(value) - known)
    if unknown:
        fail(f"unknown {name} keys: {', '.join(unknown)}; allowed: {', '.join(sorted(known))}")
    return cls(**value)


def _range(name: str, value: object) -> ClosingRange:
    if isinstance(value, dict) and set(value) == {"start", "end"}:
        start, end = str(value["start"]), str(value["end"])
    elif isinstance(value, list | tuple) and len(value) == 2:  # noqa: PLR2004 - a start and an end
        start, end = str(value[0]), str(value[1])
    else:
        fail(f"{name} must be {{start, end}} or [start, end] closings")
    if start > end:
        fail(f"{name} starts after it ends: {start} > {end}")
    return ClosingRange(start, end)


def _names(name: str, value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
        fail(f"{name} must be a list of column names")
    return tuple(value)


def _reasons(name: str, value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        fail(f"judgment.{name} must map variable -> reason")
    for variable, reason in value.items():
        if not isinstance(reason, str) or not reason.strip():
            fail(f"judgment.{name}.{variable} needs a reason")
    return {str(k): str(v) for k, v in value.items()}


def _steps(value: object) -> dict[str, bool]:
    steps = dict(DEFAULT_STEPS)
    if value is None:
        return steps
    if not isinstance(value, dict):
        fail("steps must map step -> true/false")
    for step, enabled in value.items():
        if step not in STEPS:
            fail(f"unknown step {step!r}; steps: {', '.join(STEPS)}")
        if not isinstance(enabled, bool):
            fail(f"steps.{step} must be true or false")
        steps[step] = enabled
    return steps


def _check(config: SelectionConfig) -> SelectionConfig:  # noqa: C901 - one check per rule, read top to bottom
    if config.target_kind not in TARGET_KINDS:
        fail(f"target_kind must be one of {', '.join(TARGET_KINDS)}, got {config.target_kind!r}")
    if config.out_of_time and not (
        config.out_of_time.start > config.development.end or config.out_of_time.end < config.development.start
    ):
        fail("development and out_of_time closings overlap")
    psi_max = config.stability.psi_max
    if isinstance(psi_max, str) and psi_max != CRITICAL:
        fail(f"stability.psi_max must be a number, null or 'critical', got {psi_max!r}")
    if not 0 < config.stability.alpha < 1:
        fail("stability.alpha must lie in (0, 1)")
    if config.univariate.binning not in BINNINGS:
        fail(f"univariate.binning must be one of {', '.join(BINNINGS)}")
    if min(config.stability.bins, config.univariate.bins, config.sample_rows, config.multivariate.repeats) < 1:
        fail("bins, sample_rows and multivariate.repeats must be at least 1")
    for variable, sign in config.expected_signs.items():
        if sign not in SIGNS:
            fail(f"expected_signs.{variable} must be '+' or '-', got {sign!r}")
    both = sorted(set(config.judgment.keep) & set(config.judgment.drop))
    if both:
        fail(f"judgment keeps and drops the same variables: {', '.join(both)}")
    reserved = {config.closing, config.target}
    clash = sorted(reserved & {*config.features, *config.categorical})
    if clash:
        fail(f"the closing and target columns cannot be features: {', '.join(clash)}")
    return config


def parse_config(data: Mapping[str, Any] | SelectionConfig) -> SelectionConfig:
    """Parse and validate a selection configuration mapping (or return a parsed one unchanged)."""
    if isinstance(data, SelectionConfig):
        return _check(data)
    unknown = sorted(set(data) - KEYS)
    if unknown:
        fail(f"unknown keys: {', '.join(unknown)}")
    for required in ("closing", "target", "development"):
        if required not in data:
            fail(f"{required} is required")
    oot = data.get("out_of_time")
    judgment = data.get("judgment") or {}
    if not isinstance(judgment, dict) or set(judgment) - {"keep", "drop"}:
        fail("judgment must be a mapping with keep and drop")
    signs = data.get("expected_signs") or {}
    if not isinstance(signs, dict):
        fail("expected_signs must map variable -> '+' or '-'")
    try:
        config = SelectionConfig(
            closing=str(data["closing"]),
            target=str(data["target"]),
            development=_range("development", data["development"]),
            out_of_time=None if oot is None else _range("out_of_time", oot),
            name=str(data.get("name", "selection")),
            target_kind=str(data.get("target_kind", BINARY)),
            features=_names("features", data.get("features")),
            exclude=_names("exclude", data.get("exclude")),
            categorical=_names("categorical", data.get("categorical")),
            steps=_steps(data.get("steps")),
            quality=_section(QualityConfig, QUALITY, data.get(QUALITY)),
            stability=_section(StabilityConfig, STABILITY, data.get(STABILITY)),
            univariate=_section(UnivariateConfig, UNIVARIATE, data.get(UNIVARIATE)),
            redundancy=_section(RedundancyConfig, REDUNDANCY, data.get(REDUNDANCY)),
            multivariate=_section(MultivariateConfig, MULTIVARIATE, data.get(MULTIVARIATE)),
            expected_signs={str(k): str(v) for k, v in signs.items()},
            judgment=JudgmentConfig(_reasons("keep", judgment.get("keep")), _reasons("drop", judgment.get("drop"))),
            sample_rows=int(data.get("sample_rows", 50_000)),
            seed=int(data.get("seed", 0)),
        )
    except (TypeError, ValueError) as exc:
        msg = f"selection: {exc}"
        raise SelectionError(msg) from exc
    return _check(config)


__all__ = [
    "BINARY",
    "CONTINUOUS",
    "CRITICAL",
    "DEFAULT_STEPS",
    "JUDGMENT",
    "MONOTONE",
    "MULTIVARIATE",
    "QUALITY",
    "QUANTILE",
    "REDUNDANCY",
    "STABILITY",
    "STEPS",
    "UNIVARIATE",
    "ClosingRange",
    "JudgmentConfig",
    "MultivariateConfig",
    "QualityConfig",
    "RedundancyConfig",
    "SelectionConfig",
    "SelectionError",
    "StabilityConfig",
    "UnivariateConfig",
    "fail",
    "parse_config",
]
