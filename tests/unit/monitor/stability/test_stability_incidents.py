"""Stability incidents: grouping, cause ranking, the noise budget, digest notifications and re-notification."""
# ruff: noqa: PT018, FBT003 - dense assertions and boolean declaration values

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import NoiseBudget, NotifyPolicy, parse_config, parse_notify


def test_declaration_of_digest_and_reminders() -> None:
    policy = parse_notify({"digest": True, "top": 3, "remind_after": 4, "material_change": 0.5})
    assert policy == NotifyPolicy(digest=True, top=3, remind_after=4, material_change=0.5)
    assert policy is not None and policy.wants("reminded", "warning") and policy.wants("changed", "critical")
    assert not NotifyPolicy().wants("reminded", "critical") and not NotifyPolicy().wants("changed", "critical")
    assert parse_notify(True) == NotifyPolicy() and NotifyPolicy().to_dict() == {
        "min_severity": "warning",
        "on": ["opened", "escalated", "recurred"],
    }


@pytest.mark.parametrize("bad", [{"digest": "yes"}, {"top": 0}, {"remind_after": -1}, {"material_change": -0.1}])
def test_declaration_rejects_bad_notify_options(bad: dict[str, object]) -> None:
    with pytest.raises(MonitorError):
        parse_notify(bad)


def test_declaration_of_the_noise_budget_stays_outside_old_fingerprints() -> None:
    plain = parse_config({"period": "month"})
    noisy = parse_config({"period": "month", "noise": {"max_alerts_per_run": 5, "fdr": 0.1}})
    assert plain.noise is None and "noise" not in plain.to_dict()
    assert noisy.noise == NoiseBudget(5, 0.1) and noisy.to_dict()["noise"] == {"fdr": 0.1, "max_alerts_per_run": 5}
    assert plain.binning_fingerprint() == noisy.binning_fingerprint()


@pytest.mark.parametrize("bad", [{}, {"fdr": 1.5}, {"fdr": 0}, {"max_alerts_per_run": 0}, {"other": 1}, "x"])
def test_declaration_rejects_bad_noise(bad: object) -> None:
    with pytest.raises(MonitorError):
        parse_config({"period": "month", "noise": bad})
