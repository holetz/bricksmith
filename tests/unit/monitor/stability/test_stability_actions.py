"""Human actions on stability alerts: a risk acceptance keeps an alert out of the gate until its date."""
# ruff: noqa: PT018 - dense assertions on rows

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from bricksmith.cli.main import main
from bricksmith.monitor.actions import RESOLUTIONS, action_row
from bricksmith.monitor.stability.config import parse_config
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check, judgment_fingerprint
from bricksmith.monitor.stability.tables import ACTIONS, merge_rows, view_statements

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

CRITICAL_GATE = {
    "period": "month",
    "psi_limits": "fixed",
    "min_rows_per_bin": 0,
    "gate": {"fail_on": ["critical"]},
    "persistence": {"immediate_red": True},
}


def _rows(months: int = 4, drift_from: int = 2) -> list[dict[str, object]]:
    return [
        {"month": f"2026-{m + 1:02d}", "amount": float(i % 20 + (40 if m >= drift_from else 0))}
        for m in range(months)
        for i in range(60)
    ]


def _run(store: MemoryStore, run_id: str, **declared: Any) -> CheckResult:  # noqa: ANN401
    config = parse_config({**CRITICAL_GATE, **declared})
    result = check(_rows(), config, store, dataset="orders", run_id=run_id)
    for kind, rows in result.tables.items():
        store.tables[kind] = list(merge_rows(kind, store.tables.get(kind, []), rows))
    return result


def _action(alert_id: str, action: str, **extra: str) -> dict[str, Any]:
    return action_row(
        entity="dataset", name="orders", alert_id=alert_id, action=action, reason="known drift", author="ana",
        created_at="2026-01-01T00:00:00+00:00", resolutions=RESOLUTIONS, **extra,
    )  # fmt: skip


def _critical_ids(store: MemoryStore) -> list[str]:
    return [a["alert_id"] for a in store.tables["alerts"] if a["state"] == "open" and a["severity"] == "critical"]


def test_actions_table_is_part_of_the_run_and_starts_empty() -> None:
    result = _run(MemoryStore(), "r1")
    assert ACTIONS in result.tables and result.tables[ACTIONS] == []


def test_a_critical_alert_fails_the_gate_until_it_is_accepted() -> None:
    store = MemoryStore()
    assert _run(store, "r1").outcome.failed
    ids = _critical_ids(store)
    assert ids
    store.tables[ACTIONS] = [_action(i, "risk_accepted", valid_until="2999-01-01") for i in ids]
    accepted = _run(store, "r2")
    assert not accepted.outcome.failed and accepted.outcome.critical_alerts == 0
    assert "accepted" in accepted.outcome.message
    assert [a["state"] for a in store.tables["alerts"] if a["alert_id"] in ids] == ["open"] * len(ids)


def test_an_expired_acceptance_fails_the_gate_again() -> None:
    store = MemoryStore()
    _run(store, "r1")
    ids = _critical_ids(store)
    store.tables[ACTIONS] = [_action(i, "risk_accepted", valid_until="2000-01-01") for i in ids]
    assert _run(store, "r2").outcome.failed


def test_a_closure_leaves_the_gate_and_analysis_does_not() -> None:
    store = MemoryStore()
    _run(store, "r1")
    ids = _critical_ids(store)
    store.tables[ACTIONS] = [_action(i, "analysis") for i in ids]
    assert _run(store, "r2").outcome.failed
    store.tables[ACTIONS] = [_action(i, "closed", resolution="expected") for i in ids]
    assert not _run(store, "r3").outcome.failed


def test_an_accepted_alert_is_not_notified() -> None:
    store = MemoryStore()
    first = _run(store, "r1", notify=True)
    assert first.notifications
    fresh = MemoryStore()
    seed = _run(fresh, "r0")
    store2 = MemoryStore({ACTIONS: [_action(a["alert_id"], "risk_accepted", valid_until="2999-01-01")
                                    for a in seed.tables["alerts"]]})  # fmt: skip
    assert _run(store2, "r1", notify=True).notifications == []


def test_actions_enter_the_judgment_fingerprint_only_when_present() -> None:
    config = parse_config(CRITICAL_GATE)
    plain = judgment_fingerprint(config)
    assert judgment_fingerprint(config, []) == plain
    decided = judgment_fingerprint(config, [_action("a1", "analysis")])
    assert decided != plain
    assert judgment_fingerprint(config, [_action("a1", "analysis")]) == decided


def test_action_command_appends_to_the_state_and_prints_the_insert(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "state.json"
    state.write_text("{}", encoding="utf-8")
    argv = ["stability", "action", "--dataset", "orders", "--alert-id", "a1", "--action", "risk_accepted",
            "--reason", "known", "--author", "ana", "--valid-until", "2999-01-01", "--state", str(state),
            "--scope", "main.monitoring", "--created-at", "2026-01-01T00:00:00+00:00"]  # fmt: skip
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "INSERT INTO main.monitoring.stability_actions" in out and "DATE '2999-01-01'" in out
    stored = json.loads(state.read_text(encoding="utf-8"))["actions"]
    assert [r["alert_id"] for r in stored] == ["a1"] and stored[0]["dataset"] == "orders"
    assert main(argv) == 0  # the same decision is one row
    assert len(json.loads(state.read_text(encoding="utf-8"))["actions"]) == 1


def test_action_command_refuses_what_the_core_refuses(capsys: pytest.CaptureFixture[str]) -> None:
    base = ["stability", "action", "--dataset", "orders", "--alert-id", "a1", "--reason", "r", "--author", "ana"]
    assert main([*base, "--action", "closed"]) != 0  # a closure needs a resolution
    assert main([*base, "--action", "closed", "--resolution", "fixed"]) == 0
    capsys.readouterr()


def test_views_expose_actions_and_response_measures() -> None:
    text = ";\n".join(view_statements("main.monitoring"))
    for name in ("actions_latest", "open_alerts", "alert_response", "response_summary", "false_positive_rate"):
        assert f"main.monitoring.stability_{name}" in text
    assert "effective_state" in text and "days_to_first_action" in text and "days_to_resolve" in text
