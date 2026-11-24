"""The model specification: coefficients (and scorecard points) of a linear model, importance of a tree model.

Read from the fitted estimator at training time and logged as an artifact; the renderer never refits or reloads the
model. Standard library only: estimator arrays are read through ``tolist()`` when they have it.

Scorecard points follow the usual scaling. With ``factor = pdo / ln 2`` and
``offset = base_score - factor * ln(base_odds)`` (``base_odds`` are non-event:event odds at ``base_score``), a
logistic model of the event on WoE inputs gives each group of feature ``i`` the points
``-(coefficient_i * woe + intercept / n) * factor + offset / n``, where ``n`` is the number of features; the
points of a row sum to ``offset - factor * logit(p_event)``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.documentation.artifacts import DocumentationError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

LINEAR = "linear"
TREE = "tree"
OTHER = "other"


@dataclass(frozen=True)
class Scaling:
    """Scorecard scaling: points to double the odds, the score at the base odds and the base odds (non-event:event)."""

    pdo: float
    base_score: float
    base_odds: float

    def __post_init__(self) -> None:
        """Refuse non-positive points-to-double-odds or odds."""
        if self.pdo <= 0 or self.base_odds <= 0:
            msg = "documentation: scaling needs pdo > 0 and base_odds > 0"
            raise DocumentationError(msg)

    @property
    def factor(self) -> float:
        """Points per unit of log-odds."""
        return self.pdo / math.log(2)

    @property
    def offset(self) -> float:
        """The score at even odds."""
        return self.base_score - self.factor * math.log(self.base_odds)

    def to_dict(self) -> dict[str, float]:
        """Return the plain form."""
        return {"base_odds": self.base_odds, "base_score": self.base_score, "pdo": self.pdo}


def _flat(value: object) -> list[float]:
    data = value.tolist() if hasattr(value, "tolist") else value  # pyright: ignore[reportAttributeAccessIssue]
    if isinstance(data, (int, float)):
        return [float(data)]
    out: list[float] = []
    for item in data:  # pyright: ignore[reportGeneralTypeIssues]
        out += _flat(item)
    return out


def _points(
    coefficients: Sequence[tuple[str, float]],
    intercept: float,
    woe: Mapping[str, Mapping[str, Any]],
    scaling: Scaling,
    suffix: str,
) -> list[dict[str, Any]]:
    n = len(coefficients)
    rows: list[dict[str, Any]] = []
    for feature, coefficient in coefficients:
        variable = feature.removesuffix(suffix) if suffix else feature
        table = woe.get(variable)
        if table is None:
            msg = f"documentation: no WoE table for model input {feature!r} (looked up {variable!r})"
            raise DocumentationError(msg)
        for group in table.get("groups", ()):
            value = -(coefficient * float(group["woe"]) + intercept / n) * scaling.factor + scaling.offset / n
            rows.append({"bins": list(group["bins"]), "feature": feature, "points": value, "woe": group["woe"]})
    return rows


def model_specification(
    estimator: object,
    features: Sequence[str],
    *,
    woe: Mapping[str, Mapping[str, Any]] | None = None,
    scaling: Scaling | Mapping[str, float] | None = None,
    woe_suffix: str = "_woe",
) -> dict[str, Any]:
    """Return the specification of a fitted estimator over ``features`` (the model inputs, in fit order).

    A linear estimator (``coef_``) gives the intercept and one coefficient per feature, and with ``woe`` (the
    selection's ``woe.json``) and ``scaling`` the points of every WoE group; a tree estimator
    (``feature_importances_``) gives importances, highest first; anything else records its class only.
    """
    names = list(features)
    spec: dict[str, Any] = {"estimator": type(estimator).__name__, "features": names, "kind": OTHER}
    if hasattr(estimator, "coef_"):
        values = _flat(estimator.coef_)  # pyright: ignore[reportAttributeAccessIssue]
        if len(values) != len(names):
            msg = f"documentation: the estimator has {len(values)} coefficients for {len(names)} features"
            raise DocumentationError(msg)
        intercept = _flat(getattr(estimator, "intercept_", 0.0))[0]
        coefficients = list(zip(names, values, strict=True))
        spec |= {"coefficients": [{"coefficient": c, "feature": f} for f, c in coefficients], "intercept": intercept,
                 "kind": LINEAR}  # fmt: skip
        if scaling is not None:
            scale = scaling if isinstance(scaling, Scaling) else Scaling(**{k: float(v) for k, v in scaling.items()})
            spec["scaling"] = scale.to_dict()
            spec["points"] = _points(coefficients, intercept, woe or {}, scale, woe_suffix)
    elif hasattr(estimator, "feature_importances_"):
        values = _flat(estimator.feature_importances_)  # pyright: ignore[reportAttributeAccessIssue]
        if len(values) != len(names):
            msg = f"documentation: the estimator has {len(values)} importances for {len(names)} features"
            raise DocumentationError(msg)
        ranked = sorted(zip(names, values, strict=True), key=lambda item: (-item[1], item[0]))
        spec |= {"importance": [{"feature": f, "importance": v} for f, v in ranked], "kind": TREE}
    return spec


__all__ = ["LINEAR", "OTHER", "TREE", "Scaling", "model_specification"]
