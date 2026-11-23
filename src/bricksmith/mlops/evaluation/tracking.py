"""Log an evaluation to the active MLflow run: the declaration as params, whole-sample values as metrics, JSON files."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bricksmith.mlops.evaluation.engine import EvaluationResult

MAX_PARAM = 500
#: The artifact files, under the prefix, that the technical model documentation reads.
FILES = ("results.json", "distribution.json", "grades.json", "evaluation.json")


def evaluation_params(result: EvaluationResult, prefix: str = "evaluation") -> dict[str, str]:
    """Return the declaration flattened into MLflow params (``evaluation.score``, ``evaluation.reference``...)."""
    params = {f"{prefix}.{k}": str(v)[:MAX_PARAM] for k, v in sorted(result.config.to_dict().items())}
    return params | {f"{prefix}.fingerprint": result.config.fingerprint()}


def evaluation_metrics(result: EvaluationResult, prefix: str = "evaluation") -> dict[str, float]:
    """Return the whole-sample values as ``<prefix>.<sample>.<metric>[.<subject>]``."""
    return {f"{prefix}.{name}": value for name, value in result.metrics().items()}


def log_evaluation(result: EvaluationResult, prefix: str = "evaluation") -> bool:
    """Log ``result`` to the active MLflow run and return ``True``; without MLflow or an active run, do nothing.

    Artifacts: ``<prefix>/results.json``, ``<prefix>/distribution.json``, ``<prefix>/grades.json`` and
    ``<prefix>/evaluation.json`` (the declaration, the fitted reference and the sample names).
    """
    try:
        mlflow: Any = importlib.import_module("mlflow")
    except ImportError:
        return False
    if mlflow.active_run() is None:
        return False
    data = result.to_dict()
    mlflow.log_params(evaluation_params(result, prefix))
    mlflow.log_metrics(evaluation_metrics(result, prefix))
    mlflow.log_dict(data["results"], f"{prefix}/results.json")
    mlflow.log_dict(data["distribution"], f"{prefix}/distribution.json")
    mlflow.log_dict(data["grades"], f"{prefix}/grades.json")
    summary = {k: data[k] for k in ("config", "fingerprint", "name", "reference", "samples")}
    mlflow.log_dict(summary, f"{prefix}/evaluation.json")
    return True


__all__ = ["FILES", "evaluation_metrics", "evaluation_params", "log_evaluation"]
