"""Revision reliability: the declaration, the revision metrics and the first-publication snapshot."""
# ruff: noqa: PT018 - dense assertions and boolean declaration values

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

import pytest

from bricksmith.cli.main import main
from bricksmith.context import ExecutionContext
from bricksmith.graph import load_graph
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.limits import Limits
from bricksmith.monitor.stability.config import REVISION_LIMITS, REVISION_METRICS, StabilityConfig, parse_config
from bricksmith.monitor.stability.engine import CheckResult, MemoryStore, check
from bricksmith.monitor.stability.profile import fit_rows_binning, periods_of_rows, profile_rows
from bricksmith.monitor.stability.revision import revision_results, snapshot_rows
from bricksmith.monitor.stability.tables import ALL_KINDS, KEYS, REVISIONS, SCHEMAS, merge_rows
from bricksmith.plan import build_plan
from bricksmith.runner import InMemoryTableIO, RunReport, apply_plan

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.monitor.reference import Reference


def test_config_revision_is_off_by_default_and_outside_serialized_forms() -> None:
    plain = parse_config({"period": "month"})
    declared = parse_config({"period": "month", "revision": True})
    assert not plain.revision and declared.revision
    assert "revision" not in plain.to_dict() and declared.to_dict()["revision"] is True
    assert plain.binning_fingerprint() == declared.binning_fingerprint()
    assert plain.to_dict()["limits"] == declared.to_dict()["limits"]


def test_config_rejects_a_non_boolean_revision() -> None:
    for bad in ("yes", 1, {"on": True}):
        with pytest.raises(MonitorError):
            parse_config({"period": "month", "revision": bad})


def test_config_revision_limits_default_and_override() -> None:
    config = parse_config({"period": "month", "revision": True})
    for metric in REVISION_METRICS:
        assert config.limits_for(metric, "x") == REVISION_LIMITS[metric]
    custom = parse_config({"period": "month", "limits": {"revision_psi": {"green": 0.01, "red": 0.02}}})
    assert custom.limits_for("revision_psi", "x") == Limits(0.01, 0.02, "higher")
    off = parse_config({"period": "month", "limits": {"revision_psi": None}})
    assert off.limits_for("revision_psi", "x") is None


def test_config_revisions_table_has_a_schema_and_a_key() -> None:
    assert REVISIONS in ALL_KINDS and REVISIONS in SCHEMAS and set(KEYS[REVISIONS]) <= set(SCHEMAS[REVISIONS])


def _profile(config: StabilityConfig, rows: list[dict[str, object]]) -> tuple[Reference, list[dict[str, object]]]:
    periods = periods_of_rows(rows, "month")
    binning, _ = fit_rows_binning(rows, config, periods[:1], dataset="orders", version=1)
    return binning, profile_rows(binning, rows, config, periods, dataset="orders")[0]


def _month(month: str, count: int, shift: float = 0.0) -> list[dict[str, object]]:
    return [{"month": month, "amount": float(i % 20) + i * 0.013 + shift} for i in range(count)]


def _compare(first_rows: list[dict[str, object]], now_rows: list[dict[str, object]], config: StabilityConfig) -> dict:
    binning, first = _profile(config, first_rows)
    now = profile_rows(binning, now_rows, config, ["2026-01"], dataset="orders")[0]
    snap = snapshot_rows(first, ["2026-01"], run_id="r1", published_at="t")
    found = revision_results(binning, now, snap, config, dataset="orders", window=["2026-01"], run_id="r2")
    return {(r["column"], r["metric"]): r for r in found if r["segment"] == ""}


def test_metrics_an_unrevised_period_is_green_and_zero() -> None:
    config = parse_config(
        {"period": "month", "revision": True, "min_rows": 10, "psi_limits": "fixed", "min_rows_per_bin": 0}
    )
    rows = _month("2026-01", 100)
    found = _compare(rows, rows, config)
    assert found[("", "revision_row_change")]["value"] == 0.0
    assert found[("amount", "revision_psi")]["value"] == pytest.approx(0.0, abs=1e-9)
    assert found[("amount", "revision_mean_shift")]["value"] == 0.0
    assert {r["status"] for r in found.values()} == {"green"}
    assert found[("", "revision_row_change")]["baseline_kind"] == "revision"
    assert found[("", "revision_row_change")]["baseline_label"] == "first publication"


def test_metrics_a_republished_period_reports_changed_rows_psi_and_value_delta() -> None:
    config = parse_config(
        {"period": "month", "revision": True, "min_rows": 10, "psi_limits": "fixed", "min_rows_per_bin": 0}
    )
    found = _compare(_month("2026-01", 100), _month("2026-01", 150, shift=8.0), config)
    assert found[("", "revision_row_change")]["value"] == pytest.approx(0.5)
    assert found[("", "revision_row_change")]["status"] == "red"
    assert found[("amount", "revision_psi")]["status"] == "red"
    assert found[("amount", "revision_mean_shift")]["value"] > 1
    assert found[("amount", "revision_mean_shift")]["status"] == "red"


def test_metrics_a_period_without_a_snapshot_has_no_revision_result() -> None:
    config = parse_config({"period": "month", "revision": True})
    rows = _month("2026-01", 100) + _month("2026-02", 100)
    binning, profiles = _profile(config, rows)
    snap = snapshot_rows(profiles, ["2026-01"], run_id="r1", published_at="t")
    found = revision_results(binning, profiles, snap, config, dataset="orders", window=["2026-01", "2026-02"])
    assert {r["period"] for r in found} == {"2026-01"}


def test_metrics_snapshot_keeps_only_the_wanted_periods_and_the_table_fields() -> None:
    config = parse_config({"period": "month", "revision": True})
    _, profiles = _profile(config, _month("2026-01", 40) + _month("2026-02", 40))
    snap = snapshot_rows(profiles, ["2026-02"], run_id="r9", published_at="t9")
    assert snap and {r["period"] for r in snap} == {"2026-02"}
    assert set(snap[0]) == set(SCHEMAS[REVISIONS])


def _rows(months: int, *, republished: bool = False) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for m in range(months):
        late = republished and m == months - 1
        out += _month(f"2026-{m + 1:02d}", 80 + (40 if late else 0), shift=15.0 if late else 0.0)
    return out


def _check(rows: list[dict[str, object]], store: MemoryStore, run_id: str, **declared: object) -> CheckResult:
    config = parse_config(
        {
            "period": "month",
            "min_rows": 10,
            "psi_limits": "fixed",
            "min_rows_per_bin": 0,
            "refresh_periods": 2,
            **declared,
        }
    )
    return check(rows, config, store, dataset="orders", run_id=run_id)


def _commit(store: MemoryStore, result: CheckResult) -> None:
    for kind, rows in result.tables.items():
        store.tables[kind] = merge_rows(kind, store.tables.get(kind, []), rows)


def test_engine_snapshots_first_publication_and_flags_a_republished_period() -> None:
    store = MemoryStore()
    first = _check(_rows(3), store, "r1", revision=True)
    assert {r["period"] for r in first.tables[REVISIONS]} == {"2026-01", "2026-02", "2026-03"}
    assert not [r for r in first.tables["results"] if r["metric"].startswith("revision_")]
    _commit(store, first)
    second = _check(_rows(3, republished=True), store, "r2", revision=True)
    found = {(r["period"], r["column"], r["metric"]): r for r in second.tables["results"] if r["segment"] == ""}
    assert found[("2026-03", "", "revision_row_change")]["value"] == pytest.approx(0.5)
    assert found[("2026-03", "amount", "revision_psi")]["status"] == "red"
    assert found[("2026-02", "amount", "revision_psi")]["status"] == "green"
    assert second.tables[REVISIONS] == []  # the first publication is never rewritten
    _commit(store, second)
    snapshot = [r for r in store.tables[REVISIONS] if r["period"] == "2026-03" and r["column"] == "amount"]
    assert sum(int(r["count"]) for r in snapshot) == 80


def test_engine_without_revision_has_no_revisions_table_and_no_revision_result() -> None:
    result = _check(_rows(3), MemoryStore(), "r1")
    assert REVISIONS not in result.tables
    assert not [r for r in result.tables["results"] if r["metric"].startswith("revision_")]


def test_engine_enabling_revision_later_snapshots_the_stored_profiles() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1"))
    later = _check(_rows(3, republished=True), store, "r2", revision=True)
    snap = [r for r in later.tables[REVISIONS] if r["period"] == "2026-03" and r["column"] == "amount"]
    assert sum(int(r["count"]) for r in snap) == 80  # the stored profile, not the refreshed one
    assert not [r for r in later.tables["results"] if r["metric"].startswith("revision_")]


def test_engine_revision_alerts_use_persistence() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1", revision=True))
    second = _check(_rows(3, republished=True), store, "r2", revision=True, persistence={"immediate_red": True})
    assert any(a["metric"] == "revision_psi" and a["state"] == "open" for a in second.tables["alerts"])


def _without(rows: list[dict[str, object]], month: str) -> list[dict[str, object]]:
    return [r for r in rows if r["month"] != month]


def _vanish(months: int, gone: str, refresh: int, **declared: object) -> tuple[MemoryStore, CheckResult]:
    store = MemoryStore()
    _commit(store, _check(_rows(months), store, "r1", revision=True, refresh_periods=refresh, **declared))
    second = _check(_without(_rows(months), gone), store, "r2", revision=True, refresh_periods=refresh, **declared)
    _commit(store, second)
    return store, second


def _row_change(result: CheckResult, period: str) -> list[dict]:
    return [r for r in result.tables["results"] if r["period"] == period and r["metric"] == "revision_row_change"]


def test_engine_a_vanished_middle_period_in_the_refresh_range_is_a_revision_change() -> None:
    store, second = _vanish(3, "2026-02", 2)
    found = _row_change(second, "2026-02")
    assert [(r["segment"], r["value"], r["status"]) for r in found] == [("", -1.0, "red")]
    profile = [r for r in store.tables["profiles"] if r["period"] == "2026-02"]
    assert profile and sum(int(r["count"]) for r in profile) == 0 and {r["run_id"] for r in profile} == {"r2"}
    assert {r["period"] for r in store.tables[REVISIONS]} == {"2026-01", "2026-02", "2026-03"}


def test_engine_a_vanished_latest_period_is_reported_and_fails_the_gate() -> None:
    _, second = _vanish(3, "2026-03", 2, gate={"fail_on": ["red"]})
    found = _row_change(second, "2026-03")
    assert [(r["value"], r["status"]) for r in found] == [(-1.0, "red")]
    assert second.outcome.latest_period == "2026-03" and second.outcome.red >= 1 and second.outcome.failed


def test_engine_a_vanished_period_older_than_the_refresh_range_is_not_judged() -> None:
    store, second = _vanish(4, "2026-01", 2)
    assert not _row_change(second, "2026-01")
    assert sum(int(r["count"]) for r in store.tables["profiles"] if r["period"] == "2026-01") > 0


def test_engine_a_vanished_period_respects_the_configured_limits() -> None:
    _, second = _vanish(3, "2026-02", 2, limits={"revision_row_change": None})
    assert [r["status"] for r in _row_change(second, "2026-02")] == ["no_rule"]


def test_engine_a_vanished_period_is_not_reported_without_revision() -> None:
    store = MemoryStore()
    _commit(store, _check(_rows(3), store, "r1"))
    second = _check(_without(_rows(3), "2026-02"), store, "r2")
    assert not [r for r in second.tables["results"] if r["metric"].startswith("revision_")]


def _metric(rows: list[dict], period: str, metric: str, column: str = "") -> list[dict]:
    return [r for r in rows if r["period"] == period and r["metric"] == metric and r["column"] == column]


def test_engine_an_emptied_middle_period_reports_zero_rows() -> None:
    store, second = _vanish(3, "2026-02", 2)
    for rows in (second.tables["results"], store.tables["results"]):
        counts = _metric(rows, "2026-02", "row_count")
        assert [(r["segment"], r["value"]) for r in counts] == [("", 0.0)]
        assert [r["value"] for r in _metric(rows, "2026-02", "volume_change")] == [-1.0]


def test_engine_an_emptied_latest_period_replaces_the_stale_count_in_stored_results() -> None:
    store, second = _vanish(3, "2026-03", 2)
    assert [r["value"] for r in _metric(second.tables["results"], "2026-03", "row_count")] == [0.0]
    stored = _metric(store.tables["results"], "2026-03", "row_count")
    assert [r["value"] for r in stored] == [0.0]
    assert second.outcome.latest_period == "2026-03"


def _later_period(baseline: object, *, revision: bool) -> list[dict]:
    store = MemoryStore()
    declared: dict[str, object] = {"baseline": baseline, "revision": revision}
    _commit(store, _check(_rows(3), store, "r1", **declared))
    second = _check(_without(_rows(3), "2026-02"), store, "r2", **declared)
    return [r for r in second.tables["results"] if r["period"] == "2026-03" and not r["metric"].startswith("revision_")]


@pytest.mark.parametrize("baseline", ["previous", {"kind": "rolling", "periods": 2}])
def test_engine_an_emptied_period_is_never_a_baseline_for_previous_and_rolling(baseline: object) -> None:
    on = _later_period(baseline, revision=True)
    off = _later_period(baseline, revision=False)
    assert on and {(r["metric"], r["column"]): (r["value"], r["status"]) for r in on} == {
        (r["metric"], r["column"]): (r["value"], r["status"]) for r in off
    }
    volume = _metric(on, "2026-03", "volume_change")
    assert [(r["value"], r["status"]) for r in volume] == [(0.0, "green")]
    assert not [r for r in on if r["status"] == "no_baseline"]


def test_engine_the_fixed_baseline_ignores_an_emptied_period() -> None:
    assert _later_period("fixed", revision=True) == _later_period("fixed", revision=False)


_TASKS = """
from bricksmith.tasks import task


@task(kind="table", outputs="orders", metadata={"stability": DECLARED})
def orders(ctx):
    late = ctx.execution.parameters.get("late") == "yes"
    rows = []
    for m in range(3):
        last = late and m == 2
        for i in range(80 + (40 if last else 0)):
            rows.append({"month": f"2026-{m + 1:02d}", "amount": float(i % 20) + i * 0.013 + (15.0 if last else 0.0)})
    ctx.write("orders", rows)
"""


def _project(root: Path, declared: dict[str, object]) -> Path:
    path = root / "catalogs/main/sales/orders.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(_TASKS).replace("DECLARED", repr(declared)), encoding="utf-8")
    return root


def _run(root: Path, io: InMemoryTableIO, late: str) -> RunReport:
    context = ExecutionContext(environment="test", parameters={"late": late})
    graph = load_graph(root, context=context)
    return apply_plan(build_plan(graph), graph, project_root=root, execution=context, table_io=io)  # type: ignore[arg-type]


def test_hook_writes_the_revisions_table_only_when_declared(tmp_path: Path) -> None:
    declared: dict[str, object] = {
        "period": "month",
        "min_rows": 10,
        "psi_limits": "fixed",
        "min_rows_per_bin": 0,
        "refresh_periods": 2,
    }
    plain = build_plan(load_graph(_project(tmp_path / "plain", declared)))
    assert not [t for t in plain.entry("main.sales.orders").targets if t.physical.endswith("revisions")]
    root = _project(tmp_path / "rev", {**declared, "revision": True})
    plan = build_plan(load_graph(root))
    assert "main.sales.stability_revisions" in [t.physical for t in plan.entry("main.sales.orders").targets]
    io = InMemoryTableIO()
    assert _run(root, io, "no").ok
    snapshot = io.tables["main.sales.stability_revisions"]
    assert isinstance(snapshot, list) and {r["period"] for r in snapshot} == {"2026-01", "2026-02", "2026-03"}
    assert _run(root, io, "yes").ok
    results = io.tables["main.sales.stability_results"]
    assert isinstance(results, list)
    changed = [r for r in results if r["metric"] == "revision_row_change" and r["period"] == "2026-03"]
    assert changed and changed[0]["value"] == pytest.approx(0.5)
    again = io.tables["main.sales.stability_revisions"]
    assert isinstance(again, list) and len(again) == len(snapshot)


def _cli_check(tmp_path: Path, rows: list[dict[str, object]], state: Path, *, revision: bool) -> dict:
    data = tmp_path / "orders.jsonl"
    data.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    config = tmp_path / "config.json"
    declared = {
        "period": "month",
        "min_rows": 10,
        "psi_limits": "fixed",
        "min_rows_per_bin": 0,
        "refresh_periods": 2,
        "revision": revision,
    }
    config.write_text(json.dumps(declared), encoding="utf-8")
    args = ["stability", "check", "--config", str(config), "--data", str(data), "--state", str(state)]
    main([*args, "--dataset", "orders", "--json"])
    return json.loads(state.read_text(encoding="utf-8"))


def test_cli_state_keeps_the_revision_snapshot_while_revision_is_off(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    first = _cli_check(tmp_path, _rows(3), state, revision=True)
    stored = first[REVISIONS]
    assert {r["period"] for r in stored} == {"2026-01", "2026-02", "2026-03"}
    off = _cli_check(tmp_path, _rows(3), state, revision=False)
    assert off[REVISIONS] == stored  # a kind the run did not return is written back untouched
    on = _cli_check(tmp_path, _rows(3, republished=True), state, revision=True)
    assert on[REVISIONS] == stored  # the first publication survives, so the republished period is caught
    changed = [r for r in on["results"] if r["metric"] == "revision_row_change" and r["period"] == "2026-03"]
    assert changed and changed[0]["value"] == pytest.approx(0.5)
