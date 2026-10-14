"""Experiments offline: isolation, pinning, state seeding, collisions, evidence, promotion, retirement and the CLI."""

# ruff: noqa: PT018 - dense assertions on synthetic fixtures

from __future__ import annotations

import json
import textwrap
from typing import TYPE_CHECKING

import pytest

from bricksmith.cli.main import main
from bricksmith.context import ExecutionContext
from bricksmith.errors import AuthorizationError
from bricksmith.experiments import (
    Evidence,
    EvidenceError,
    EvidenceExistsError,
    ExperimentError,
    ExperimentIO,
    InMemoryStore,
    IsolationError,
    StaleEvidenceError,
    apply_promotion,
    apply_retirement,
    compare_revision,
    load_revision,
    plan_isolation,
    plan_promotion,
    plan_retirement,
    run_revision,
)
from bricksmith.quality import GatePolicy

if TYPE_CHECKING:
    from pathlib import Path

CTX = ExecutionContext(environment="test", parameters={"factor": 1})
EVENTS = "main.sales.events"
REPORT = "main.sales.report"
ISO = "main.sales_x_rev_a"

FILES = {
    "events.py": """
        @task(kind="table")
        def events(ctx):
            ctx.write("events", [])
    """,
    "report.py": """
        @task(kind="table", metadata={"write": "overwrite"})
        def report(ctx):
            factor = ctx.execution.parameters.get("factor", 1)
            ctx.write("report", [{"id": r["id"], "value": r["value"] * factor} for r in ctx.read("events")])
    """,
    "totals.py": """
        @task(kind="table", reads=("report",), metadata={"write": "overwrite"})
        def totals(ctx):
            ctx.write("totals", [{"id": 0, "value": sum(r["value"] for r in ctx.read("report"))}])
    """,
    "history.py": """
        @task(kind="table", state=("history",), metadata={"write": "append"})
        def history(ctx):
            seen = len(ctx.read_state("history"))
            ctx.write("history", [{"id": seen, "value": len(ctx.read("events"))}])
    """,
}
REVISION = """
name = "rev_a"
roots = ["main.sales.report"]
[parameters]
factor = 3
[[pins]]
input = "main.sales.events"
version = 0
[[compare]]
output = "report"
keys = ["id"]
"""


def make_project(root: Path, extra: dict[str, str] | None = None) -> Path:
    for name, body in {**FILES, **(extra or {})}.items():
        path = root / "catalogs/main/sales" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("from bricksmith.tasks import task\n" + textwrap.dedent(body), encoding="utf-8")
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return make_project(tmp_path)


def baseline_store() -> InMemoryStore:
    store = InMemoryStore({EVENTS: [{"id": 1, "value": 10}, {"id": 2, "value": 20}]})
    store.put(REPORT, [{"id": 1, "value": 10}, {"id": 2, "value": 20}])
    return store


def freeze(project: Path, store: InMemoryStore, text: str = REVISION) -> Evidence:
    plan = plan_isolation(project, load_revision(text), context=CTX, store=store)
    assert plan.ok, plan.to_text()
    return Evidence.freeze(plan, created="2026-01-01T00:00:00Z")


def go(project: Path, evidence: Evidence, store: InMemoryStore) -> object:
    return run_revision(evidence, project_root=project, context=CTX, table_io=store, store=store)


def test_plan_boundary_inputs_targets_and_unpinned(project: Path) -> None:
    store = baseline_store()
    plan = plan_isolation(project, load_revision(REVISION), context=CTX, store=store)
    assert plan.ok and plan.rebuilt == ("main.sales.report",)
    assert plan.targets == {REPORT: f"{ISO}.report"}
    assert plan.outside_consumers == ("main.sales.totals",)
    assert [(i.physical, i.mode, i.version) for i in plan.inputs] == [(EVENTS, "frozen", 0)]
    live = plan_isolation(
        project,
        load_revision(REVISION.replace("[[pins]]", "[[nopins]]")[:0] + 'name="x"\nroots=["main.sales.report"]'),
        context=CTX,
    )
    assert live.unpinned == (EVENTS,)
    assert any(i.code == "unpinned_input" for i in live.issues)
    assert "LIVE (unpinned)" in live.to_text()


def test_baseline_never_modified_and_pinning_reproduces(project: Path) -> None:
    store = baseline_store()
    evidence = freeze(project, store)
    store.put(EVENTS, [{"id": 1, "value": 10}, {"id": 2, "value": 20}, {"id": 3, "value": 99}])  # live drift
    before = {k: [list(map(dict, v)) for v in vs] for k, vs in store.versions.items() if k in (EVENTS, REPORT)}
    outcome = go(project, evidence, store)
    assert outcome.ok  # type: ignore[attr-defined]
    assert store.rows(f"{ISO}.report") == [{"id": 1, "value": 30}, {"id": 2, "value": 60}]  # frozen v0, factor 3
    assert {k: store.versions[k] for k in before} == before
    assert store.writes.count(f"{ISO}.report") == 1 and REPORT not in store.writes
    assert outcome.created_schemas == (ISO,)  # type: ignore[attr-defined]


def test_unpinned_run_sees_live_data(project: Path) -> None:
    store = baseline_store()
    text = REVISION.replace('[[pins]]\ninput = "main.sales.events"\nversion = 0\n', "")
    evidence = freeze(project, store, text)
    assert evidence.unpinned == [EVENTS]
    store.put(EVENTS, [{"id": 7, "value": 1}])
    go(project, evidence, store)
    assert store.rows(f"{ISO}.report") == [{"id": 7, "value": 3}]


def test_writes_outside_destination_are_refused() -> None:
    store = baseline_store()
    guard = ExperimentIO(store, store, pins={}, allowed=frozenset({f"{ISO}.report"}), baseline=frozenset({REPORT}))
    with pytest.raises(IsolationError):
        guard.write(REPORT, [], operation="overwrite")
    with pytest.raises(IsolationError):
        guard.write("main.sales.other", [], operation="overwrite")
    assert REPORT not in store.writes


def test_unseeded_backfill_rejected_and_seeded_accepted(project: Path) -> None:
    store = baseline_store()
    store.put("main.sales.history", [{"id": 0, "value": 2}])
    base = 'name = "rev_a"\nroots = ["main.sales.history"]\n'
    stateful = plan_isolation(
        project, load_revision(base + '[backfill]\nstart = "2024-01-01"\n'), context=CTX, store=store
    )
    assert not stateful.ok
    assert [i.code for i in stateful.errors] == ["unseeded_state"]
    with pytest.raises(ExperimentError):
        Evidence.freeze(stateful, created="x")
    plain = plan_isolation(project, load_revision(base), context=CTX, store=store)
    assert plain.ok and any(i.code == "unseeded_state" and i.severity == "warning" for i in plain.issues)
    seeded = freeze(
        project, store, base + '[backfill]\nstart = "2024-01-01"\n[[seeds]]\noutput = "main.sales.history"\n'
    )
    assert go(project, seeded, store).ok  # type: ignore[attr-defined]
    assert store.rows(f"{ISO}.history") == [{"id": 0, "value": 2}, {"id": 1, "value": 2}]
    assert store.rows("main.sales.history") == [{"id": 0, "value": 2}]


def test_collisions_detected_before_anything_is_created(project: Path) -> None:
    store = baseline_store()
    store.put(f"{ISO}.report", [])
    plan = plan_isolation(project, load_revision(REVISION), context=CTX, store=store)
    assert [i.code for i in plan.errors] == ["target_exists"]
    with pytest.raises(ExperimentError):
        load_revision(REVISION.replace('name = "rev_a"', 'name = "rev_a"\nsuffix = ""'))
    make_project(project, {})
    clash = project / "catalogs/main/sales_x_rev_a/other.py"
    clash.parent.mkdir(parents=True)
    clash.write_text(
        'from bricksmith.tasks import task\n@task(kind="table")\ndef other(ctx):\n    ctx.write("other", [])\n'
    )
    codes = {i.code for i in plan_isolation(project, load_revision(REVISION), context=CTX).errors}
    assert "schema_collision" in codes
    assert not store.schema_exists("main", "other")
    unused = REVISION.replace('input = "main.sales.events"', 'input = "main.sales.nothing"')
    assert "pin_unused" in [i.code for i in plan_isolation(project, load_revision(unused), context=CTX).errors]


def test_unknown_root_and_pin_beyond_latest(project: Path) -> None:
    store = baseline_store()
    assert not plan_isolation(project, load_revision('name="x"\nroots=["main.sales.zzz"]'), context=CTX).ok
    late = REVISION.replace("version = 0", "version = 5")
    assert [i.code for i in plan_isolation(project, load_revision(late), context=CTX, store=store).errors] == [
        "pin_unresolved"
    ]


def test_timestamp_pin_resolves_to_version(project: Path) -> None:
    store = InMemoryStore()
    store.put(EVENTS, [{"id": 1, "value": 1}], "2026-01-01T00:00:00")
    store.put(EVENTS, [{"id": 2, "value": 2}], "2026-02-01T00:00:00")
    text = REVISION.replace("version = 0", 'timestamp = "2026-01-15T00:00:00"')
    plan = plan_isolation(project, load_revision(text), context=CTX, store=store)
    assert plan.pins() == {EVENTS: 0}


def test_fingerprint_changes_when_source_changes(project: Path) -> None:
    store = baseline_store()
    evidence = freeze(project, store)
    first = evidence.data["tasks"]["main.sales.report"]["source"]
    make_project(project, {"report.py": FILES["report.py"] + "\n        # edited\n"})
    with pytest.raises(StaleEvidenceError, match=r"main\.sales\.report"):
        go(project, evidence, store)
    changed = plan_isolation(project, evidence.revision, context=CTX, store=store, resolved=evidence.pins)
    assert changed.tasks["main.sales.report"]["source"] != first
    assert changed.configuration == evidence.data["configuration"]


def test_configuration_change_makes_evidence_stale(project: Path) -> None:
    store = baseline_store()
    evidence = freeze(project, store)
    other = ExecutionContext(environment="test", parameters={"factor": 2, "extra": 1})
    with pytest.raises(StaleEvidenceError):
        run_revision(evidence, project_root=project, context=other, table_io=store, store=store)


def test_evidence_is_immutable(project: Path, tmp_path: Path) -> None:
    store = baseline_store()
    evidence = freeze(project, store)
    path = tmp_path / "rev_a.evidence.json"
    evidence.write(path)
    with pytest.raises(EvidenceExistsError):
        evidence.write(path)
    assert Evidence.read(path) == evidence
    tampered = json.loads(path.read_text())
    tampered["body"]["revision"]["parameters"]["factor"] = 4
    with pytest.raises(EvidenceError):
        Evidence.from_dict(tampered)
    with pytest.raises(AttributeError):
        evidence.body = "{}"  # type: ignore[misc]
    evidence.data["targets"].clear()  # a copy: the record is unchanged
    assert evidence.targets


def test_compare_promote_and_impact_report(project: Path) -> None:
    store = baseline_store()
    evidence = freeze(project, store)
    go(project, evidence, store)
    report = compare_revision(evidence, store)
    assert report.verdict == "fail"  # factor 3 changes every row
    with pytest.raises(ExperimentError, match="did not pass"):
        plan_promotion(evidence, report, store, project_root=project, context=CTX)
    same = freeze(project, baseline_store(), REVISION.replace("factor = 3", "factor = 1").replace('"rev_a"', '"rev_a"'))
    store2 = baseline_store()
    go(project, same, store2)
    ok = compare_revision(same, store2)
    assert ok.verdict == "pass"
    plan = plan_promotion(same, ok, store2, project_root=project, context=CTX)
    kinds = {(c.kind, c.subject) for c in plan.impact}
    assert {
        ("task", "main.sales.report"),
        ("output", REPORT),
        ("consumer", "main.sales.totals"),
        ("input", EVENTS),
    } <= kinds
    with pytest.raises(AuthorizationError):
        apply_promotion(plan, store2, authorized=set())
    assert apply_promotion(plan, store2, authorized={REPORT}) == (REPORT,)
    other = compare_revision(evidence, store)
    with pytest.raises(ExperimentError, match="another"):
        plan_promotion(same, other, store2, project_root=project, context=CTX)


def test_promotion_with_tolerant_gate_lists_parameter_change(project: Path) -> None:
    store = baseline_store()
    evidence = freeze(project, store)
    go(project, evidence, store)
    gate = compare_revision(evidence, store, policy=GatePolicy(max_differing_rows=2))
    assert gate.verdict == "pass"
    plan = plan_promotion(evidence, gate, store, project_root=project, context=CTX)
    assert ("parameter", "factor") in {(c.kind, c.subject) for c in plan.impact}
    assert "1 -> 3" in next(c.detail for c in plan.impact if c.kind == "parameter")


def test_retirement_separates_outputs_from_sources(project: Path) -> None:
    store = baseline_store()
    evidence = freeze(project, store, 'retain = ["model-a"]\n' + REVISION)
    go(project, evidence, store)
    plan = plan_retirement(evidence, store)
    assert plan.drop_tables == (f"{ISO}.report",) and plan.retained["source data"] == (EVENTS,)
    assert plan.retained["baseline outputs"] == (REPORT,)
    dropped, left = apply_retirement(plan, evidence, store)
    assert left == [] and f"{ISO}.report" in dropped
    assert store.exists(EVENTS) and store.exists(REPORT) and not store.schema_exists("main", "sales_x_rev_a")
    assert plan_retirement(evidence, store).drop_tables == ()
    forged = type(plan)(plan.name, (EVENTS,), (), {})
    with pytest.raises(IsolationError):
        apply_retirement(forged, evidence, store)


def test_cli_plan_freeze_and_retire(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = baseline_store()
    from bricksmith.experiments import cli  # noqa: PLC0415

    monkeypatch.setattr(cli, "_store", lambda _args: store)
    monkeypatch.setattr(cli, "context_from_args", lambda _args: CTX)
    monkeypatch.setattr(cli, "_table_io", lambda _args: store)
    rev = tmp_path / "rev.toml"
    rev.write_text(REVISION, encoding="utf-8")
    out = tmp_path / "ev.json"
    assert main(["experiment", "plan", "--project", str(project), "--revision", str(rev), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["targets"] == {REPORT: f"{ISO}.report"}
    args = [
        "experiment",
        "plan",
        "--project",
        str(project),
        "--revision",
        str(rev),
        "--freeze",
        str(out),
        "--created",
        "t",
    ]
    assert main(args) == 0 and out.exists()
    assert main(args) != 0  # evidence is never overwritten
    capsys.readouterr()
    assert main(["experiment", "run", "--project", str(project), "--evidence", str(out)]) == 0  # dry run
    assert not store.exists(f"{ISO}.report")
    assert main(["experiment", "run", "--project", str(project), "--evidence", str(out), "--apply"]) == 0
    assert store.exists(f"{ISO}.report")
    assert main(["experiment", "retire", "--evidence", str(out)]) == 0 and store.exists(f"{ISO}.report")
    assert main(["experiment", "retire", "--evidence", str(out), "--apply"]) == 0
    assert not store.exists(f"{ISO}.report") and store.exists(REPORT)
