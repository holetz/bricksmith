"""Log a selection to the active MLflow run: thresholds as params, counts as metrics, the ledger as artifacts."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from bricksmith.mlops.selection.funnel import SelectionResult

MAX_PARAM = 500


def _flatten(prefix: str, value: object) -> dict[str, str]:
    if isinstance(value, dict) and value and all(isinstance(k, str) for k in value):
        nested: dict[str, str] = {}
        for key, item in sorted(value.items()):
            nested.update(_flatten(f"{prefix}.{key}", item))
        return nested
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return {prefix: text[:MAX_PARAM]}


def selection_params(result: SelectionResult, prefix: str = "selection") -> dict[str, str]:
    """Return the configuration flattened into MLflow params (``selection.univariate.min_iv`` and so on)."""
    return _flatten(prefix, result.config.to_dict()) | {f"{prefix}.fingerprint": result.config.fingerprint()}


def selection_metrics(result: SelectionResult, prefix: str = "selection") -> dict[str, float]:
    """Return the candidate, selected and per-step dropped counts."""
    metrics = {f"{prefix}.candidates": float(len(result.variables)), f"{prefix}.selected": float(len(result.selected))}
    metrics.update({f"{prefix}.dropped.{step}": float(n) for step, n in result.dropped_by_step().items()})
    return metrics


def log_selection(result: SelectionResult, prefix: str = "selection") -> bool:
    """Log ``result`` to the active MLflow run and return ``True``; without MLflow or an active run, do nothing.

    Artifacts: ``<prefix>/ledger.json``, ``<prefix>/variables.json``, ``<prefix>/features.json`` (the final
    feature set) and ``<prefix>/woe.json`` (the development-fitted WoE tables).
    """
    try:
        mlflow: Any = importlib.import_module("mlflow")
    except ImportError:
        return False
    if mlflow.active_run() is None:
        return False
    mlflow.log_params(selection_params(result, prefix))
    mlflow.log_metrics(selection_metrics(result, prefix))
    mlflow.log_dict(result.ledger_rows(), f"{prefix}/ledger.json")
    mlflow.log_dict(result.variable_rows(), f"{prefix}/variables.json")
    mlflow.log_dict({"features": list(result.selected), "selection": result.config.name}, f"{prefix}/features.json")
    mlflow.log_dict(result.to_dict()["woe"], f"{prefix}/woe.json")
    return True


__all__ = ["log_selection", "selection_metrics", "selection_params"]
