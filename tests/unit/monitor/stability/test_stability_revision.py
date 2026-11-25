"""Revision reliability: the declaration, the revision metrics and the first-publication snapshot."""
# ruff: noqa: PT018 - dense assertions and boolean declaration values

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

import pytest

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
    config = parse_config({"period": "month", "revision": True, "min_rows": 10})
    rows = _month("2026-01", 100)
    found = _compare(rows, rows, config)
    assert found[("", "revision_row_change")]["value"] == 0.0
    assert found[("amount", "revision_psi")]["value"] == pytest.approx(0.0, abs=1e-9)
    assert found[("amount", "revision_mean_shift")]["value"] == 0.0
    assert {r["status"] for r in found.values()} == {"green"}
    assert found[("", "revision_row_change")]["baseline_kind"] == "revision"
    assert found[("", "revision_row_change")]["baseline_label"] == "first publication"


def test_metrics_a_republished_period_reports_changed_rows_psi_and_value_delta() -> None:
    config = parse_config({"period": "month", "revision": True, "min_rows": 10})
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
    config = parse_config({"period": "month", "min_rows": 10, "refresh_periods": 2, **declared})
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
    declared: dict[str, object] = {"period": "month", "min_rows": 10, "refresh_periods": 2}
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
