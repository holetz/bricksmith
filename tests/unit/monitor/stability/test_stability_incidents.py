"""Stability incidents: grouping, cause ranking, the noise budget, digest notifications and re-notification."""
# ruff: noqa: PT018, FBT003 - dense assertions and boolean declaration values

from __future__ import annotations

import math

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import NoiseBudget, NotifyPolicy, parse_config, parse_notify
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, alert_events, check
from bricksmith.monitor.stability.incident import (
    apply_noise,
    benjamini_hochberg,
    build_incident,
    false_positive_rates,
    trend_p_value,
)
from bricksmith.monitor.stability.tables import merge_rows


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


def _alert(column: str, *, segment: str = "", metric: str = "psi", kind: str = "trend", severity: str = "warning"):  # noqa: ANN202
    return {
        "alert_id": f"{kind}-{segment}-{column}-{metric}", "segment": segment, "column": column, "metric": metric,
        "kind": kind, "severity": severity, "state": "open",
    }  # fmt: skip


def _result(column: str, deviation: float, *, segment: str = "", metric: str = "psi", value: float = 0.3):  # noqa: ANN202
    return {
        "period": "2026-04", "segment": segment, "column": column, "metric": metric, "value": value,
        "red_limit": 0.25, "trend_deviation": deviation, "direction": "higher", "rule_id": "",
    }  # fmt: skip


def test_incident_ranks_the_injected_column_first_with_its_bins() -> None:
    config = parse_config({"period": "month"})
    columns = {"a": 3.2, "b": 3.1, "c": 9.0}
    alerts = [_alert(c, severity="critical" if d > 5 else "warning") for c, d in columns.items()]
    results = [_result(c, d, value=0.9 if c == "c" else 0.3) for c, d in columns.items()]
    profiles = [
        {"period": "2026-04", "segment": "", "column": "c", "bin": f"b{i}", "bin_label": f"bin {i}", "contribution": v}
        for i, v in enumerate((0.01, 0.5, -0.2))
    ]
    incident = build_incident(alerts, results, profiles, config, dataset="orders", period="2026-04")
    assert incident is not None and incident.severity == "critical"
    assert [c.column for c in incident.causes] == ["c", "a", "b"]
    assert incident.columns[0][0] == "c" and incident.to_dict(top=1)["alerts"] == 3
    assert [b["bin"] for b in incident.bins] == ["b1", "b2", "b0"]
    again = build_incident(alerts, results, [], config, dataset="orders", period="2026-04")
    assert again is not None and incident.incident_id == again.incident_id
    assert build_incident([], results, profiles, config, dataset="orders", period="2026-04") is None


def test_incident_ranks_segments_by_summed_score() -> None:
    config = parse_config({"period": "month"})
    alerts = [_alert("x", segment="eu"), _alert("y", segment="eu"), _alert("x", segment="us")]
    results = [_result("x", 4.0, segment="eu"), _result("y", 4.0, segment="eu"), _result("x", 4.0, segment="us")]
    incident = build_incident(alerts, results, [], config, dataset="d", period="2026-04")
    assert incident is not None and [s for s, _ in incident.segments] == ["eu", "us"]


def test_noise_p_values_and_benjamini_hochberg() -> None:
    assert trend_p_value(0.0) == 1.0 and trend_p_value(math.inf) == 0.0
    assert trend_p_value(2.0, "higher") == pytest.approx(0.02275, abs=1e-4)
    assert trend_p_value(-2.0, "higher") > 0.97 and trend_p_value(-2.0, "both") == pytest.approx(0.0455, abs=1e-4)
    p = {("", "a", "psi"): 0.001, ("", "b", "psi"): 0.02, ("", "c", "psi"): 0.9}
    assert benjamini_hochberg(p, 0.05) == {("", "a", "psi"), ("", "b", "psi")}
    assert benjamini_hochberg(p, 0.001) == set()


def test_noise_fdr_drops_new_yellow_trend_alerts_but_keeps_critical_and_stored() -> None:
    config = parse_config({"period": "month"})
    deviations = {"a": 5.0, "b": 2.1, "c": 2.1, "d": 1.0, "e": 0.2}
    rows = [_alert(c) for c in deviations] + [_alert("z", severity="critical")]
    results = [_result(c, d) for c, d in deviations.items()] + [_result("z", 0.1)]
    kept, suppressed = apply_noise(rows, {"trend--b-psi"}, results, config, NoiseBudget(fdr=0.05), latest="2026-04")
    assert {r["column"] for r in kept} == {"a", "b", "c", "z"} and suppressed == 2


def test_noise_cap_keeps_the_highest_scores() -> None:
    config = parse_config({"period": "month"})
    rows = [_alert(c) for c in "abc"]
    results = [_result("a", 3.0), _result("b", 6.0), _result("c", 4.0)]
    kept, suppressed = apply_noise(rows, set(), results, config, NoiseBudget(max_alerts_per_run=2), latest="2026-04")
    assert [r["column"] for r in kept] == ["b", "c"] or {r["column"] for r in kept} == {"b", "c"}
    assert suppressed == 1


def test_false_positive_rate_per_rule_counts_the_latest_closed_action() -> None:
    results = [{**_result("a", 0.0), "rule_id": "r1"}, {**_result("b", 0.0), "rule_id": "r1"}, _result("c", 0.0)]
    alerts = [_alert("a"), _alert("b"), _alert("c")]
    actions = [
        {"alert_id": alerts[0]["alert_id"], "action": "closed", "resolution": "false_positive", "created_at": "1"},
        {"alert_id": alerts[1]["alert_id"], "action": "closed", "resolution": "fixed", "created_at": "1"},
        {"alert_id": alerts[2]["alert_id"], "action": "closed", "resolution": "fixed", "created_at": "1"},
    ]
    assert false_positive_rates(actions, alerts, results) == {"r1": {"closed": 2, "false_positive": 1, "rate": 0.5}}


def _wide_rows(months: int, broken: int, drift_from: int = 2) -> list[dict[str, object]]:
    """Rows with 20 columns; from month ``drift_from`` the first ``broken`` columns shift (column c0 the most)."""
    rows = []
    for m in range(months):
        for i in range(60):
            row: dict[str, object] = {"month": f"2026-{m + 1:02d}"}
            for c in range(20):
                shift = (80 if c == 0 else 40) if (m >= drift_from and c < broken) else 0
                row[f"c{c}"] = float(i % 20 + shift)
            rows.append(row)
    return rows


def _run(store: MemoryStore, run_id: str, rows: list[dict[str, object]], **declared: object) -> CheckResult:
    config = parse_config({"period": "month", "persistence": {"immediate_red": True}, **declared})
    result = check(rows, config, store, dataset="orders", run_id=run_id, physical="main.s.orders")
    for kind, table in result.tables.items():
        store.tables[kind] = list(merge_rows(kind, store.tables.get(kind, []), table))
    return result


def test_incident_of_a_load_breaking_twenty_columns_is_one_digest_with_the_injected_column_first() -> None:
    store = MemoryStore()
    result = _run(store, "r1", _wide_rows(4, 20), notify={"digest": True, "top": 3})
    assert result.incident is not None and len(result.incident.causes) >= 20
    assert result.incident.columns[0][0] == "c0" and result.outcome.incident_id == result.incident.incident_id
    assert result.incident.bins and result.incident.bins[0]["column"] == "c0"
    assert len(result.notifications) == 1
    digest = result.notifications[0]
    assert digest.dedup_key.startswith("stability:orders:digest:") and len(digest.details["alert_keys"]) >= 20
    assert digest.details["columns"][0] == "c0" and len(digest.details["causes"]) == 3
    assert _run(store, "r2", _wide_rows(4, 20), notify={"digest": True}).notifications == []


def test_per_alert_notifications_stay_the_default() -> None:
    result = _run(MemoryStore(), "r1", _wide_rows(4, 3), notify=True)
    assert len(result.notifications) > 1 and all(e.dedup_key.endswith(":opened") for e in result.notifications)


def test_noise_budget_caps_the_alerts_opened_by_a_run() -> None:
    capped = _run(MemoryStore(), "r1", _wide_rows(4, 20), noise={"max_alerts_per_run": 4}, notify=True)
    free = _run(MemoryStore(), "r1", _wide_rows(4, 20), notify=True)
    opened = [r for r in capped.tables["alerts"] if r["state"] == "open"]
    assert len(opened) == 4 and capped.outcome.suppressed == len(free.tables["alerts"]) - 4
    assert f"{capped.outcome.suppressed} suppressed by the noise budget" in capped.outcome.message
    assert "suppressed" in capped.outcome.to_dict() and "suppressed" not in free.outcome.to_dict()
    assert capped.incident is not None and capped.incident.suppressed == capped.outcome.suppressed


def test_reminders_and_material_changes_renotify_an_open_alert() -> None:
    def alert(flagged: int, value: float) -> dict[str, object]:
        return {
            "alert_id": "a1", "state": "open", "severity": "warning", "recurrence": False, "flagged_periods": flagged,
            "last_value": value, "message": "m", "kind": "persistence", "metric": "psi", "column": "x",
            "segment_label": "", "opened_period": "2026-01", "last_period": "2026-0x",
        }  # fmt: skip

    def keys(policy: NotifyPolicy, fresh: dict[str, object], before: dict[str, object]) -> list[str]:
        events = alert_events([fresh], [before], policy, dataset="d", target="t", run_id="r", occurred_at="now")
        return [e.dedup_key.rsplit(":", 1)[1] for e in events]

    base = alert(2, 0.30)
    assert keys(NotifyPolicy(), alert(4, 0.9), base) == []
    assert keys(NotifyPolicy(remind_after=4), alert(4, 0.30), base) == ["reminded"]
    assert keys(NotifyPolicy(remind_after=4), alert(3, 0.30), base) == []
    assert keys(NotifyPolicy(material_change=0.5), alert(3, 0.50), base) == ["changed"]
    assert keys(NotifyPolicy(material_change=0.5), alert(3, 0.33), base) == []
    assert keys(NotifyPolicy(on=(), remind_after=1), alert(3, 0.30), base) == []


def test_a_run_reports_the_false_positive_rate_of_the_rule() -> None:
    store = MemoryStore()
    rules = [{"id": "loose", "metric": "psi", "limits": {"green": 5, "red": 9}}]
    first = _run(store, "r1", _wide_rows(4, 3), rules=rules)
    store.tables["actions"] = [
        {
            "action_id": f"x{n}", "dataset": "orders", "alert_id": r["alert_id"], "action": "closed",
            "resolution": "false_positive", "reason": "noise", "author": "me", "valid_until": None,
            "created_at": "2026-05-01T00:00:00+00:00",
        }
        for n, r in enumerate(first.tables["alerts"])
        if r["metric"] == "psi"
    ]  # fmt: skip
    again = _run(store, "r2", _wide_rows(4, 3), rules=rules)
    assert again.incident is not None
    assert again.incident.false_positive_rates["loose"]["rate"] == 1.0
    assert "false positives loose" in again.outcome.message
