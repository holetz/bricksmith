"""Human actions on model monitoring alerts: analysis, closure and time-boxed risk acceptance.

Alerts are rebuilt on every run, so what people decide about them lives in its own append-only table. A person
writes one row per decision (:func:`action_row`); runs and replays fold the latest action of each alert into its
state at an evaluation date (:func:`apply_actions`). An acceptance suppresses gating until its ``valid_until`` date
(inclusive); after it the alert returns to its automatic state while the action stays visible.
"""

from __future__ import annotations

import hashlib
from datetime import date
from typing import TYPE_CHECKING, Any, NoReturn

from bricksmith.monitor.errors import MonitorError

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

ANALYSIS = "analysis"
CLOSED = "closed"
RISK_ACCEPTED = "risk_accepted"
ACTIONS = (ANALYSIS, CLOSED, RISK_ACCEPTED)
#: Alert states: the automatic one, or the one a human action gave.
STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_ACCEPTED = "accepted"
#: The alert columns an applied action fills (empty without one).
ALERT_COLUMNS = ("action", "action_reason", "action_author", "action_at", "valid_until")


def _refuse(message: str) -> NoReturn:
    msg = f"alert action: {message}"
    raise MonitorError(msg)


def _iso_date(value: object, what: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return _refuse(f"{what} must be an ISO date (YYYY-MM-DD), got {value!r}")


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
    """Return the validated row of an alert action.

    ``reason`` and ``author`` are required; ``risk_accepted`` requires ``valid_until`` and the other actions refuse
    one. ``action_id`` hashes the content, so writing the same decision twice is one row.
    """
    if action not in ACTIONS:
        _refuse(f"action must be one of {', '.join(ACTIONS)}; got {action!r}")
    if not model.strip() or not alert_id.strip():
        _refuse("model and alert_id are required")
    if not reason.strip() or not author.strip():
        _refuse("reason and author are required")
    if action == RISK_ACCEPTED and not valid_until:
        _refuse("risk_accepted requires valid_until")
    if action != RISK_ACCEPTED and valid_until:
        _refuse(f"{action} takes no valid_until")
    identity = f"{model}|{alert_id}|{action}|{author}|{created_at}"
    return {
        "action_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
        "model": model,
        "alert_id": alert_id,
        "action": action,
        "reason": reason.strip(),
        "author": author.strip(),
        "valid_until": _iso_date(valid_until, "valid_until") if valid_until else None,
        "created_at": created_at,
    }


def latest_actions(actions: Iterable[Mapping[str, Any]], model: str | None = None) -> dict[str, dict[str, Any]]:
    """Return the latest action per alert id (by ``created_at``, then ``action_id``), optionally of one model."""
    latest: dict[str, dict[str, Any]] = {}
    for row in actions:
        if model is not None and row.get("model") != model:
            continue
        key = str(row.get("alert_id") or "")
        order = (str(row.get("created_at") or ""), str(row.get("action_id") or ""))
        held = latest.get(key)
        if held is None or order > (str(held.get("created_at") or ""), str(held.get("action_id") or "")):
            latest[key] = dict(row)
    return latest


def acceptance_active(action: Mapping[str, Any], as_of: str) -> bool:
    """Tell whether a ``risk_accepted`` action still holds on ``as_of`` (``valid_until`` is inclusive)."""
    until = action.get("valid_until")
    return action.get("action") == RISK_ACCEPTED and until is not None and str(until)[:10] >= as_of[:10]


def apply_actions(
    alerts: Iterable[Mapping[str, Any]], actions: Iterable[Mapping[str, Any]], as_of: str
) -> list[dict[str, Any]]:
    """Return ``alerts`` with the latest action of each folded into its state on the date ``as_of``.

    ``closed`` sets the state ``closed``; an active ``risk_accepted`` sets ``accepted``; an expired one and
    ``analysis`` leave the automatic state. The action is recorded on the alert in every case. Actions of other
    models, or of ids no alert carries, change nothing.
    """
    rows = [dict(a) for a in alerts]
    models = {str(a["model"]) for a in rows}
    pool = list(actions)
    out: list[dict[str, Any]] = []
    latest = {model: latest_actions(pool, model) for model in models}
    for alert in rows:
        action = latest[str(alert["model"])].get(str(alert.get("alert_id") or ""))
        if action is None:
            out.append({**alert, **dict.fromkeys(ALERT_COLUMNS)})
            continue
        state = alert.get("state") or STATE_OPEN
        if action["action"] == CLOSED:
            state = STATE_CLOSED
        elif acceptance_active(action, as_of):
            state = STATE_ACCEPTED
        out.append(
            {
                **alert,
                "state": state,
                "action": action["action"],
                "action_reason": action.get("reason"),
                "action_author": action.get("author"),
                "action_at": action.get("created_at"),
                "valid_until": str(action["valid_until"])[:10] if action.get("valid_until") else None,
            }
        )
    return out


def insert_statement(table: str, row: Mapping[str, Any]) -> str:
    """Return the ``INSERT`` statement a person runs to append ``row`` to the actions ``table``."""

    def literal(value: object) -> str:
        return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"

    columns = ("action_id", "model", "alert_id", "action", "reason", "author", "valid_until", "created_at")
    values = ", ".join(
        f"DATE {literal(row[c])}" if c == "valid_until" and row[c] is not None else literal(row[c]) for c in columns
    )
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values})"  # noqa: S608 - table name checked by caller


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
