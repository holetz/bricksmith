"""Stability alert notifications: the policy, the transitions and the delivery through the run notifier."""
# ruff: noqa: PT018, FBT003 - dense assertions and boolean declaration values

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import NotifyPolicy, parse_config, parse_notify
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, alert_events, check
from bricksmith.monitor.stability.tables import merge_rows


def test_policy_shapes() -> None:
    assert parse_notify(None) is None
    assert parse_notify(True) == NotifyPolicy()
    assert parse_notify(False) == NotifyPolicy(on=())
    policy = parse_notify({"on": "opened", "min_severity": "critical"})
    assert policy is not None and policy == NotifyPolicy(("opened",), "critical")
    assert policy.wants("opened", "critical") and not policy.wants("opened", "warning")
    assert not policy.wants("recurred", "critical")


def test_policy_rejects_unknown_values() -> None:
    for bad in ({"on": ["closed"]}, {"min_severity": "info"}, {"other": 1}, "yes", 3):
        with pytest.raises(MonitorError):
            parse_notify(bad)


def test_notify_is_a_declaration_key_outside_the_fingerprints() -> None:
    plain = parse_config({"period": "month"})
    declared = parse_config({"period": "month", "notify": {"on": ["opened"]}})
    assert plain.notify is None and declared.notify == NotifyPolicy(("opened",))
    assert plain.binning_fingerprint() == declared.binning_fingerprint()
    assert "notify" not in plain.to_dict() and declared.to_dict()["notify"]["on"] == ["opened"]


def _rows(months: int, drift_from: int = 2) -> list[dict[str, object]]:
    return [
        {"month": f"2026-{m + 1:02d}", "amount": float(i % 20 + (40 if m >= drift_from else 0))}
        for m in range(months)
        for i in range(60)
    ]


def _run(months: int, store: MemoryStore, run_id: str, **declared: object) -> tuple[CheckResult, list[str]]:
    config = parse_config({"period": "month", "notify": True, "persistence": {"immediate_red": True}, **declared})
    result = check(_rows(months), config, store, dataset="orders", run_id=run_id, physical="main.s.orders")
    for kind, rows in result.tables.items():
        store.tables[kind] = list(merge_rows(kind, store.tables.get(kind, []), rows))
    return result, [e.dedup_key for e in result.notifications]


def test_a_new_alert_is_notified_once_and_replays_are_silent() -> None:
    store = MemoryStore()
    first, keys = _run(4, store, "r1")
    assert first.notifications and len(keys) == len(set(keys))
    assert all(e.kind == "statistical_alert" and e.dedup_key.endswith(":opened") for e in first.notifications)
    assert all(e.subject == "orders" and e.target == "main.s.orders" for e in first.notifications)
    assert _run(4, store, "r2")[1] == []
    assert _run(4, store, "r3")[1] == []


def test_the_same_alert_keeps_its_key_across_runs() -> None:
    first = _run(4, MemoryStore(), "r1")[1]
    assert first == _run(4, MemoryStore(), "other-run")[1]


def test_a_sliding_window_never_reopens_a_carried_alert() -> None:
    store = MemoryStore()
    assert _run(4, store, "r1", max_periods=3)[1]
    for months in (5, 6, 7):
        assert _run(months, store, f"r{months}", max_periods=3)[1] == []


def test_escalation_and_policy_filters() -> None:
    store = MemoryStore()
    config = parse_config({"period": "month", "notify": {"on": ["escalated"]}})
    assert alert_events([_alert("warning")], [], config.notify, **_ids()) == []
    events = alert_events([_alert("critical")], [_alert("warning")], config.notify, **_ids())
    assert [e.details["transition"] for e in events] == ["escalated"] and events[0].severity == "critical"
    assert alert_events([_alert("critical")], [_alert("critical")], config.notify, **_ids()) == []
    assert alert_events([_alert("critical")], [], parse_notify(False), **_ids()) == []
    assert not store.tables


def test_recurrence_is_its_own_transition() -> None:
    stored = [_alert("warning")]
    fresh = [{**_alert("warning"), "recurrence": True}]
    events = alert_events(fresh, stored, NotifyPolicy(), **_ids())
    assert [e.dedup_key for e in events] == ["stability:orders:a1:recurred"]
    assert [e.dedup_key for e in alert_events(fresh, [], NotifyPolicy(), **_ids())] == ["stability:orders:a1:recurred"]
    closed = [{**_alert("warning"), "state": "closed"}]
    assert [e.dedup_key for e in alert_events([_alert("warning")], closed, NotifyPolicy(), **_ids())] == [
        "stability:orders:a1:opened"
    ]


def _alert(severity: str) -> dict[str, object]:
    return {
        "alert_id": "a1", "state": "open", "severity": severity, "recurrence": False, "message": "psi of amount",
        "kind": "persistence", "metric": "psi", "column": "amount", "segment_label": "all", "opened_period": "2026-03",
        "last_period": "2026-04",
    }  # fmt: skip


def _ids() -> dict[str, str]:
    return {"dataset": "orders", "target": "t", "run_id": "r", "occurred_at": "2026-04-01T00:00:00+00:00"}
