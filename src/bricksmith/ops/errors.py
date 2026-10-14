"""Errors of the operations capability."""

from __future__ import annotations

from bricksmith.errors import BricksmithError, ValidationError


class StatisticalAlertError(BricksmithError):
    """A statistical alert (drift, stability, threshold) raised as a failure; never a job failure."""

    code = "statistical_alert"


class BudgetExceededError(ValidationError):
    """A declared operational budget would be violated; raised before execution."""

    code = "budget_exceeded"


class OpsConfigError(ValidationError):
    """Invalid ``[ops]`` configuration in ``bricksmith.toml``."""

    code = "ops_config_invalid"
