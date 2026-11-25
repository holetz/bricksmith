"""Limit rules kept as data: a Delta table the run reads, appended to by people and the console.

``rules: {table: catalog.schema.stability_rules}`` replaces the inline list or the rules file. One row is one rule
version: ``rule_id``, ``version``, ``body`` (the rule mapping as JSON, the exact shape of a rules file), ``author`` and
``created_at``. The table is append-only, so it is also the audit trail: for a repeated ``(rule_id, version)`` the
latest ``created_at`` wins, and every row ever written stays readable.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.actions import order_key
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import rules_of

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from bricksmith.monitor.rules import LimitRule

RULES_COLUMNS = ("rule_id", "version", "body", "author", "created_at")
RULES_SCHEMA = {"rule_id": "STRING", "version": "INT", "body": "STRING", "author": "STRING", "created_at": "STRING"}


def rule_row(body: Mapping[str, Any], *, author: str, created_at: str) -> dict[str, Any]:
    """Return the validated row that appends ``body`` (a rule mapping) to the rules table."""
    if not author.strip():
        msg = "stability rules table: author is required"
        raise MonitorError(msg)
    (rule,) = rules_of([dict(body)])
    return {
        "rule_id": rule.id,
        "version": rule.version,
        "body": json.dumps(dict(body), sort_keys=True),
        "author": author,
        "created_at": created_at,
    }


def rules_from_rows(rows: Iterable[Mapping[str, Any]], table: str = "rules table") -> tuple[LimitRule, ...]:
    """Fold the rows into validated rules: the latest row of each ``(rule_id, version)``, in id order."""
    latest: dict[tuple[str, int], Mapping[str, Any]] = {}
    for row in sorted(rows, key=lambda r: order_key(r, "rule_id")):
        latest[(str(row.get("rule_id")), int(row.get("version") or 1))] = row
    bodies: list[Any] = []
    for (rule_id, version), row in sorted(latest.items()):
        try:
            bodies.append(json.loads(str(row.get("body"))))
        except ValueError:
            msg = f"stability {table}: rule {rule_id!r} version {version} has a body that is not JSON"
            raise MonitorError(msg) from None
    return rules_of(bodies)


def load_rules(io: Any, table: str) -> tuple[LimitRule, ...]:  # noqa: ANN401 - the run's table I/O
    """Read ``table`` through the run's table I/O (rows or a Spark frame) and fold it into rules."""
    try:
        data = io.read(table)
    except Exception as error:  # noqa: BLE001 - any read failure is one refusal naming the table
        msg = f"stability: rules table {table} cannot be read: {error}"
        raise MonitorError(msg) from None
    rows = data.collect() if hasattr(data, "collect") else data
    return rules_from_rows((r.asDict() if hasattr(r, "asDict") else r for r in rows), table)


__all__ = ["RULES_COLUMNS", "RULES_SCHEMA", "load_rules", "rule_row", "rules_from_rows"]
