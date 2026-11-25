"""Human actions on monitoring alerts, shared by model and stability monitoring: analysis, closure, risk acceptance.

Alerts are rebuilt on every run, so what people decide about them lives in its own append-only table. A person
writes one row per decision (:func:`action_row`); runs and replays fold the latest action of each alert into its
state at an evaluation date (:func:`apply_actions`). An acceptance suppresses gating until its ``valid_until`` date
(inclusive); after it the alert returns to its automatic state while the action stays visible.

The rules are the same for every monitor; only the column naming the monitored entity differs (``model`` for model
monitoring, ``dataset`` for stability), so each function takes it as ``entity``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
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
#: Typed resolutions of a closure, for monitors that record why an alert was closed.
RESOLUTIONS = ("fixed", "expected", "new_normal", "false_positive")


def _refuse(message: str) -> NoReturn:
    msg = f"alert action: {message}"
    raise MonitorError(msg)


def _iso_date(value: object, what: str) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return _refuse(f"{what} must be an ISO date (YYYY-MM-DD), got {value!r}")


def _utc_instant(value: object) -> datetime | None:
    """Return ``value`` parsed as an ISO-8601 instant in UTC (a naive one is read as UTC), or None."""
    try:
        moment = datetime.fromisoformat(str(value))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC)
    except (ValueError, OverflowError):  # unparsable, or outside the date range once converted to UTC
        return None


def _utc_text(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds" if moment.microsecond else "seconds")


def _order(row: Mapping[str, Any]) -> tuple[int, datetime | str, str]:
    """Sort key of a stored action: its UTC instant, or its raw text (before every instant) when unparsable."""
    raw = str(row.get("created_at") or "")
    moment = _utc_instant(raw) if raw else None
    if moment is None:
        return (0, raw, str(row.get("action_id") or ""))
    return (1, moment, str(row.get("action_id") or ""))


def _check_resolution(action: str, resolution: str, resolutions: tuple[str, ...]) -> None:
    if not resolutions:
        if resolution:
            _refuse("this monitor takes no resolution")
    elif action == CLOSED and resolution not in resolutions:
        _refuse(f"closed requires a resolution of {', '.join(resolutions)}; got {resolution!r}")
    elif action != CLOSED and resolution:
        _refuse(f"{action} takes no resolution")


def action_row(  # noqa: PLR0913 - one keyword per column a person writes
    *,
    entity: str,
    name: str,
    alert_id: str,
    action: str,
    reason: str,
    author: str,
    created_at: str,
    valid_until: str = "",
    resolution: str = "",
    resolutions: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return the validated row of an alert action; ``entity`` is the column that carries ``name``.

    ``reason`` and ``author`` are required; ``risk_accepted`` requires ``valid_until`` and the other actions refuse
    one. ``created_at`` must be ISO-8601; it is stored in UTC (a naive value is read as UTC). ``action_id`` hashes
    the content, so writing the same decision twice, at the same instant in any offset, is one row. A monitor that
    declares ``resolutions`` stores a ``resolution`` column, which only a ``closed`` action may carry (and must).
    """
    if action not in ACTIONS:
        _refuse(f"action must be one of {', '.join(ACTIONS)}; got {action!r}")
    if not name.strip() or not alert_id.strip():
        _refuse(f"{entity} and alert_id are required")
    if not reason.strip() or not author.strip():
        _refuse("reason and author are required")
    if action == RISK_ACCEPTED and not valid_until:
        _refuse("risk_accepted requires valid_until")
    if action != RISK_ACCEPTED and valid_until:
        _refuse(f"{action} takes no valid_until")
    _check_resolution(action, resolution, resolutions)
    moment = _utc_instant(created_at)
    if moment is None:
        _refuse(f"created_at must be an ISO-8601 timestamp, got {created_at!r}")
    created = _utc_text(moment)
    identity = f"{name}|{alert_id}|{action}|{author}|{created}"
    row: dict[str, Any] = {
        "action_id": hashlib.sha256(identity.encode()).hexdigest()[:16],
        entity: name,
        "alert_id": alert_id,
        "action": action,
    }
    if resolutions:
        row["resolution"] = resolution or None
    row |= {
        "reason": reason.strip(),
        "author": author.strip(),
        "valid_until": _iso_date(valid_until, "valid_until") if valid_until else None,
        "created_at": created,
    }
    return row


def latest_actions(
    actions: Iterable[Mapping[str, Any]], name: str | None = None, entity: str = "model"
) -> dict[str, dict[str, Any]]:
    """Return the latest action per alert id, optionally of one entity (``name`` in the ``entity`` column).

    Actions are ordered by the UTC instant of ``created_at``, then ``action_id``. A stored ``created_at`` that does not
    parse still counts, ordered by its text before every parsable one.
    """
    latest: dict[str, dict[str, Any]] = {}
    for row in actions:
        if name is not None and row.get(entity) != name:
            continue
        key = str(row.get("alert_id") or "")
        held = latest.get(key)
        if held is None or _order(row) > _order(held):
            latest[key] = dict(row)
    return latest


def acceptance_active(action: Mapping[str, Any], as_of: str) -> bool:
    """Tell whether a ``risk_accepted`` action still holds on ``as_of`` (``valid_until`` is inclusive)."""
    until = action.get("valid_until")
    return action.get("action") == RISK_ACCEPTED and until is not None and str(until)[:10] >= as_of[:10]


def apply_actions(
    alerts: Iterable[Mapping[str, Any]],
    actions: Iterable[Mapping[str, Any]],
    as_of: str,
    entity: str = "model",
    *,
    resolution: bool = False,
) -> list[dict[str, Any]]:
    """Return ``alerts`` with the latest action of each folded into its state on the date ``as_of``.

    ``closed`` sets the state ``closed``; an active ``risk_accepted`` sets ``accepted``; an expired one and
    ``analysis`` leave the automatic state. The action is recorded on the alert in every case (with its
    ``resolution`` when ``resolution`` is set). Actions of other entities, or of ids no alert carries, change nothing.
    """
    rows = [dict(a) for a in alerts]
    names = {str(a[entity]) for a in rows}
    pool = list(actions)
    columns = (*ALERT_COLUMNS, "resolution") if resolution else ALERT_COLUMNS
    out: list[dict[str, Any]] = []
    latest = {name: latest_actions(pool, name, entity) for name in names}
    for alert in rows:
        action = latest[str(alert[entity])].get(str(alert.get("alert_id") or ""))
        if action is None:
            out.append({**alert, **dict.fromkeys(columns)})
            continue
        state = alert.get("state") or STATE_OPEN
        if action["action"] == CLOSED:
            state = STATE_CLOSED
        elif acceptance_active(action, as_of):
            state = STATE_ACCEPTED
        folded = {
            **alert,
            "state": state,
            "action": action["action"],
            "action_reason": action.get("reason"),
            "action_author": action.get("author"),
            "action_at": action.get("created_at"),
            "valid_until": str(action["valid_until"])[:10] if action.get("valid_until") else None,
        }
        if resolution:
            folded["resolution"] = action.get("resolution")
        out.append(folded)
    return out


def insert_statement(table: str, row: Mapping[str, Any], columns: tuple[str, ...]) -> str:
    """Return the ``INSERT`` statement a person runs to append ``row`` to the actions ``table``."""

    def literal(value: object) -> str:
        return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"

    values = ", ".join(
        f"DATE {literal(row[c])}" if c == "valid_until" and row[c] is not None else literal(row[c]) for c in columns
    )
    return f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({values})"  # noqa: S608 - table name checked by caller


__all__ = [
    "ACTIONS",
    "ALERT_COLUMNS",
    "ANALYSIS",
    "CLOSED",
    "RESOLUTIONS",
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
