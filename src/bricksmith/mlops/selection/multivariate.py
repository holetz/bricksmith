"""The optional multivariate step: a penalized model and permutation importance on a bounded sample.

Needs the ``mlops`` extra (scikit-learn, which brings numpy); both load only when the step runs. A binary target
fits an L1 (or elastic-net) logistic regression, a continuous target an elastic net, each on standardized
complete cases of the development sample. Permutation importance is measured on the out-of-time sample when it
has complete rows, else on the development sample.
"""

from __future__ import annotations

import importlib
import inspect
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.selection.config import BINARY, fail
from bricksmith.mlops.selection.summary import target_value

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.mlops.selection.config import SelectionConfig

MIN_ROWS = 10
MAX_ITER = 5000


def _modules() -> dict[str, Any]:
    try:
        return {
            "numpy": importlib.import_module("numpy"),
            "linear": importlib.import_module("sklearn.linear_model"),
            "inspection": importlib.import_module("sklearn.inspection"),
            "pipeline": importlib.import_module("sklearn.pipeline"),
            "preprocessing": importlib.import_module("sklearn.preprocessing"),
        }
    except ImportError:
        fail("the multivariate step needs the mlops extra (scikit-learn): install bricksmith[mlops]")


def _complete(
    rows: Sequence[Mapping[str, Any]], variables: Sequence[str], config: SelectionConfig
) -> tuple[list[list[float]], list[float]]:
    features, labels = [], []
    for row in rows:
        y = target_value(row.get(config.target), config.target_kind)
        if y is None or any(row.get(v) is None for v in variables):
            continue
        features.append([float(row[v]) for v in variables])
        labels.append(y)
    return features, labels


def _model(linear: Any, config: SelectionConfig) -> tuple[Any, str]:  # noqa: ANN401 - sklearn.linear_model
    options = config.multivariate
    if config.target_kind != BINARY:
        model = linear.ElasticNet(alpha=options.alpha, l1_ratio=options.l1_ratio, max_iter=MAX_ITER,
                                  random_state=config.seed)  # fmt: skip
        return model, "r2"
    kwargs: dict[str, Any] = {"C": options.c, "l1_ratio": options.l1_ratio, "solver": "saga", "max_iter": MAX_ITER,
                              "random_state": config.seed}  # fmt: skip
    if inspect.signature(linear.LogisticRegression).parameters["penalty"].default != "deprecated":
        kwargs["penalty"] = "elasticnet"  # scikit-learn before 1.8 needs the penalty named
    return linear.LogisticRegression(**kwargs), "roc_auc"


def evaluate(
    train: Sequence[Mapping[str, Any]],
    holdout: Sequence[Mapping[str, Any]],
    variables: Sequence[str],
    config: SelectionConfig,
) -> dict[str, tuple[float, float]] | None:
    """Return ``variable -> (coefficient, permutation importance)``, or ``None`` with too few complete rows.

    Coefficients are on the standardized scale; importance is the mean drop of ROC AUC (binary) or R^2.
    """
    modules = _modules()
    numpy = modules["numpy"]
    x_train, y_train = _complete(train, variables, config)
    x_hold, y_hold = _complete(holdout, variables, config)
    if len(x_hold) < MIN_ROWS or len(set(y_hold)) < 2:  # noqa: PLR2004 - two classes or values
        x_hold, y_hold = x_train, y_train
    if len(x_train) < MIN_ROWS or len(set(y_train)) < 2:  # noqa: PLR2004
        return None
    model, scoring = _model(modules["linear"], config)
    pipeline = modules["pipeline"].make_pipeline(modules["preprocessing"].StandardScaler(), model)
    pipeline.fit(numpy.asarray(x_train), numpy.asarray(y_train))
    coefficients = numpy.ravel(pipeline[-1].coef_)
    importance = (
        modules["inspection"]
        .permutation_importance(
            pipeline,
            numpy.asarray(x_hold),
            numpy.asarray(y_hold),
            n_repeats=config.multivariate.repeats,
            random_state=config.seed,
            scoring=scoring,
        )
        .importances_mean
    )
    return {v: (float(coefficients[i]), float(importance[i])) for i, v in enumerate(variables)}


__all__ = ["evaluate"]
