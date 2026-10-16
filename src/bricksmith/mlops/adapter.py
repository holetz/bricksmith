"""The model adapter protocol and the scikit-learn adapter.

An adapter owns three explicit contracts over lists of row dicts: ``train`` (fit, never reads other data),
``evaluate`` (metrics of a fitted model on labelled rows) and ``predict`` (scores for unlabelled rows). It also
owns the artifact format (``save``/``load``). Heavy libraries are imported inside the methods that use them.
"""

from __future__ import annotations

import importlib
import math
from typing import TYPE_CHECKING, Any, Protocol

from bricksmith.mlops.errors import FeatureContractError, ModelError
from bricksmith.mlops.meta import capture_runtime

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from bricksmith.mlops.spec import ModelSpec

NUMERIC = ("int", "bigint", "smallint", "tinyint", "float", "double", "boolean")
ARTIFACT_DIR = "model"

#: The ordered feature contract: ``(name, canonical type)`` pairs.
type Contract = Sequence[tuple[str, str]]


class ModelAdapter(Protocol):
    """Explicit train, evaluate and predict contracts plus the artifact format of one model library."""

    name: str

    def train(self, spec: ModelSpec, rows: Sequence[Mapping[str, Any]], params: Mapping[str, Any]) -> object:
        """Fit on labelled ``rows`` following the feature contract; return the fitted model."""
        ...

    def evaluate(
        self, model: object, features: Contract, label: str, rows: Sequence[Mapping[str, Any]]
    ) -> dict[str, float | None]:
        """Return metrics for labelled ``rows``; undefined metrics are None, never invented."""
        ...

    def predict(self, model: object, features: Contract, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Return ``{"score": float, "prediction": int}`` for each row, in order; never fits."""
        ...

    def save(self, model: object, path: Path, features: Contract, rows: Sequence[Mapping[str, Any]]) -> None:
        """Write the fitted model into the directory ``path`` (``rows`` give a signature example)."""
        ...

    def load(self, path: Path) -> object:
        """Load a model written by :meth:`save`."""
        ...

    def dependencies(self) -> dict[str, str]:
        """Return the installed versions that the artifact depends on."""
        ...


def contract_of(spec: ModelSpec) -> tuple[tuple[str, str], ...]:
    """Return the ordered ``(name, type)`` feature contract of a spec."""
    return tuple((f.name, f.type) for f in spec.features)


def feature_frame(features: Contract, rows: Sequence[Mapping[str, Any]]) -> Any:  # noqa: ANN401
    """Build a pandas frame in contract order and types; missing columns, nulls and string features are rejected."""
    import pandas as pd  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    names = [n for n, _ in features]
    for name, kind in features:
        if kind not in NUMERIC:
            msg = f"the scikit-learn adapter supports numeric and boolean features; {name} is {kind}"
            raise FeatureContractError(msg)
    for index, row in enumerate(rows):
        missing = [n for n in names if n not in row]
        if missing:
            msg = f"row {index} lacks features: {', '.join(missing)}"
            raise FeatureContractError(msg)
        nulls = [n for n in names if row[n] is None]
        if nulls:
            msg = f"row {index} has null features: {', '.join(nulls)}"
            raise FeatureContractError(msg)
    dtypes = {"int": "int32", "bigint": "int64", "smallint": "int16", "tinyint": "int8", "float": "float32"}
    frame = pd.DataFrame({n: [r[n] for r in rows] for n in names}, columns=pd.Index(names))
    return frame.astype({n: dtypes.get(k, "bool" if k == "boolean" else "float64") for n, k in features})


def _auc(labels: Sequence[int], scores: Sequence[float]) -> float | None:
    """Rank-based area under the ROC curve with ties counted half; None when a class is absent."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y == 1]
    neg = [s for s, y in zip(scores, labels, strict=True) if y == 0]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


class SklearnAdapter:
    """Logistic regression through scikit-learn, saved in the MLflow ``sklearn`` flavor with a signature."""

    name = "sklearn"

    def train(self, spec: ModelSpec, rows: Sequence[Mapping[str, Any]], params: Mapping[str, Any]) -> object:
        """Fit a logistic regression; the labels must contain both classes."""
        from sklearn.linear_model import LogisticRegression  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

        labels = [int(r[spec.label.name]) for r in rows]
        if set(labels) != {0, 1}:
            msg = f"training needs both classes of {spec.label.name}; got {sorted(set(labels))}"
            raise ModelError(msg)
        allowed = {"C", "max_iter", "random_state", "class_weight"}
        unknown = set(params) - allowed
        if unknown:
            msg = f"unsupported sklearn parameters: {', '.join(sorted(unknown))}; allowed: {', '.join(sorted(allowed))}"
            raise ModelError(msg)
        model = LogisticRegression(**{"max_iter": 200, "random_state": 0, **params})
        return model.fit(feature_frame(contract_of(spec), rows), labels)

    def evaluate(
        self, model: object, features: Contract, label: str, rows: Sequence[Mapping[str, Any]]
    ) -> dict[str, float | None]:
        """Return accuracy, ROC AUC (None with one class), log loss and the positive rate."""
        labels = [int(r[label]) for r in rows]
        out = self.predict(model, features, rows)
        scores = [o["score"] for o in out]
        eps = 1e-15
        loss = -sum(math.log(min(max(s if y else 1 - s, eps), 1)) for s, y in zip(scores, labels, strict=True)) / len(
            labels
        )
        correct = sum(o["prediction"] == y for o, y in zip(out, labels, strict=True))
        return {
            "accuracy": correct / len(labels),
            "auc": _auc(labels, scores),
            "log_loss": loss,
            "positive_rate": sum(labels) / len(labels),
            "rows": float(len(labels)),
        }

    def predict(self, model: object, features: Contract, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        """Return the positive-class probability and the 0.5-threshold class for each row."""
        if not rows:
            return []
        scores = model.predict_proba(feature_frame(features, rows))[:, 1]  # type: ignore[attr-defined]
        return [{"score": float(s), "prediction": int(s >= 0.5)} for s in scores]  # noqa: PLR2004

    def save(self, model: object, path: Path, features: Contract, rows: Sequence[Mapping[str, Any]]) -> None:
        """Write an MLflow sklearn-flavor model with an inferred signature."""
        from mlflow.models import infer_signature  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

        flavor = importlib.import_module("mlflow.sklearn")

        sample = feature_frame(features, rows[:5])
        signature = infer_signature(sample, model.predict(sample))  # type: ignore[attr-defined]
        flavor.save_model(model, str(path), signature=signature)

    def load(self, path: Path) -> object:
        """Load the sklearn model without going through any registry."""
        return importlib.import_module("mlflow.sklearn").load_model(str(path))

    def dependencies(self) -> dict[str, str]:
        """Return interpreter and library versions."""
        return capture_runtime()


def get_adapter(name: str) -> ModelAdapter:
    """Return the built-in adapter called ``name``."""
    if name == "sklearn":
        return SklearnAdapter()
    msg = f"unknown model adapter {name!r}; built-in: sklearn"
    raise ModelError(msg)
