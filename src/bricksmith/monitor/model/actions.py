"""Human actions on model monitoring alerts: analysis, closure and time-boxed risk acceptance.

The rules are the shared core of :mod:`bricksmith.monitor.actions`, bound here to the ``model`` column. A person
writes one row per decision (:func:`action_row`); runs and replays fold the latest action of each alert into its
state at an evaluation date (:func:`apply_actions`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.monitor import actions as core
from bricksmith.monitor.actions import (
    ACTIONS,
    ALERT_COLUMNS,
    ANALYSIS,
    CLOSED,
    RISK_ACCEPTED,
    STATE_ACCEPTED,
    STATE_CLOSED,
    STATE_OPEN,
    acceptance_active,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_COLUMNS = ("action_id", "model", "alert_id", "action", "reason", "author", "valid_until", "created_at")


def action_row(  # noqa: PLR0913 - one keyword per column a person writes
    *,
    model: str,
    alert_id: str,
    action: str,
    reason: str,
    author: str,
    created_at: str,
    valid_until: str = "",
) -> dict[str, Any]:
    """Return the validated row of a model alert action (see :func:`bricksmith.monitor.actions.action_row`)."""
    return core.action_row(
        entity="model", name=model, alert_id=alert_id, action=action, reason=reason, author=author,
        created_at=created_at, valid_until=valid_until,
    )  # fmt: skip


def latest_actions(actions: Iterable[Mapping[str, Any]], model: str | None = None) -> dict[str, dict[str, Any]]:
    """Return the latest action per alert id, optionally of one model."""
    return core.latest_actions(actions, model, "model")


def apply_actions(
    alerts: Iterable[Mapping[str, Any]], actions: Iterable[Mapping[str, Any]], as_of: str
) -> list[dict[str, Any]]:
    """Return ``alerts`` with the latest action of each folded into its state on the date ``as_of``."""
    return core.apply_actions(alerts, actions, as_of, "model")


def insert_statement(table: str, row: Mapping[str, Any]) -> str:
    """Return the ``INSERT`` statement a person runs to append ``row`` to the actions ``table``."""
    return core.insert_statement(table, row, _COLUMNS)


__all__ = [
    "ACTIONS",
    "ALERT_COLUMNS",
    "ANALYSIS",
    "CLOSED",
    "RISK_ACCEPTED",
    "STATE_ACCEPTED",
    "STATE_CLOSED",
    "STATE_OPEN",
    "acceptance_active",
    "action_row",
    "apply_actions",
    "insert_statement",
    "latest_actions",
]
