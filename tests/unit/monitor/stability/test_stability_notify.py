"""Stability alert notifications: the policy, the transitions and the delivery through the run notifier."""
# ruff: noqa: PT018, FBT003 - dense assertions and boolean declaration values

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import NotifyPolicy, parse_config, parse_notify


def test_policy_shapes() -> None:
    assert parse_notify(None) is None
    assert parse_notify(True) == NotifyPolicy()
    assert parse_notify(False) == NotifyPolicy(on=())
    policy = parse_notify({"on": "opened", "min_severity": "critical"})
    assert policy == NotifyPolicy(("opened",), "critical")
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
