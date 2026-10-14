"""Plans, authorization, execution, reports and replay on synthetic projects written to ``tmp_path``."""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
import textwrap
import threading
import time
from typing import TYPE_CHECKING

import pytest

from bricksmith.cli.main import main
from bricksmith.context import ExecutionContext
from bricksmith.errors import (
    EXIT_OK,
    EXIT_RUN_FAILED,
    EXIT_VALIDATION,
    AuthorizationError,
    PlanError,
    StalePlanError,
    ValidationError,
)
from bricksmith.graph import load_graph
from bricksmith.plan import Authorization, Plan, build_plan, validate_plan
from bricksmith.runner import (
    InMemoryTableIO,
    RetryPolicy,
    RunReport,
    TaskContext,
    apply_plan,
    replay_plan,
    run_entrypoint,
    run_plan,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bricksmith.graph import TaskGraph

HEADER = "from bricksmith.tasks import task\n"
CTX = ExecutionContext(environment="test", parameters={})

FILES = {
    "catalogs/main/sales/helpers.py": """
        def double(rows):
            return [r * 2 for r in rows]
    """,
    "catalogs/main/sales/calendar.py": """
        @task(kind="table", static=True)
        def calendar(ctx):
            ctx.write("calendar", ["mon"])
    """,
    "catalogs/main/sales/raw.py": """
        @task(kind="table", outputs="raw_orders")
        def raw(ctx):
            if ctx.execution.parameters.get("fail") == "raw":
                raise RuntimeError("boom")
            ctx.write("raw_orders", [1, 2, 3])
    """,
    "catalogs/main/sales/clean.py": """
        from .helpers import double

        @task(kind="table", outputs="clean_orders", metadata={"protected": True})
        def clean(ctx):
            if ctx.execution.parameters.get("fail") == "clean":
                raise RuntimeError("boom")
            ctx.read("calendar")
            ctx.write("clean_orders", double(ctx.read("raw_orders")))
    """,
    "catalogs/main/sales/agg.py": """
        @task(kind="table", outputs="order_totals", metadata={"protected": True, "write": "merge"})
        def agg(ctx):
            ctx.write("order_totals", sum(ctx.read("clean_orders")))
    """,
    "catalogs/main/sales/side.py": """
        @task(kind="table", outputs="side_table")
        def side(ctx):
            ctx.write("side_table", "ok")
    """,
    "catalogs/main/sales/dormant.py": """
        @task(kind="table", enabled=False)
        def dormant(ctx):
            ctx.write("dormant", 0)
    """,
}


def write(root: Path, files: dict[str, str]) -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        body = textwrap.dedent(text)
        path.write_text(body if "import task" in body else HEADER + body, encoding="utf-8")
    return root


@pytest.fixture
def project(tmp_path: Path) -> Path:
    return write(tmp_path, FILES)


def graph_of(root: Path, context: ExecutionContext | None = None) -> TaskGraph:
    return load_graph(root, context=context)


def tid(name: str) -> str:
    return f"main.sales.{name}"


def physical(graph: TaskGraph, output: str) -> str:
    return next(o.physical for o in graph.outputs if o.name.endswith(f".{output}"))


def authorized(plan: Plan, graph: TaskGraph, *outputs: str) -> Plan:
    grants = [Authorization(physical(graph, o), ("merge",) if o == "order_totals" else ("write",)) for o in outputs]
    return plan.authorize(*grants)


def io_with_calendar(graph: TaskGraph) -> InMemoryTableIO:
    found: dict[str, object] = {o.physical: ["mon"] for o in graph.outputs if o.name.endswith(".calendar")}
    return InMemoryTableIO(found)


def run(
    root: Path,
    plan: Plan,
    graph: TaskGraph,
    context: ExecutionContext = CTX,
    io: InMemoryTableIO | None = None,
    **kw: object,
) -> RunReport:
    return apply_plan(plan, graph, project_root=root, execution=context, table_io=io or io_with_calendar(graph), **kw)  # type: ignore[arg-type]


def test_closure_order_and_reasons(project: Path) -> None:
    graph = graph_of(project)
    plan = build_plan(graph, roots=[tid("agg")])
    assert [(e.task_id, e.action, e.reason) for e in plan.entries] == [
        (tid("calendar"), "skip", "static output; not recomputed"),
        (tid("raw"), "run", "selected"),
        (tid("clean"), "run", "selected"),
        (tid("agg"), "run", "selected"),
    ]
    assert [e.selected_by for e in plan.entries] == ["upstream", "upstream", "upstream", "root"]
    assert not plan.errors
    clean = plan.entry(tid("clean"))
    assert clean.targets[0].physical == physical(graph, "clean_orders")
    assert clean.targets[0].protected
    assert plan.entry(tid("agg")).targets[0].operations == ("merge",)
    assert {r.reference for r in clean.reads} == {"calendar", "raw_orders"}
    down = build_plan(graph, roots=[tid("raw")], upstream=False, downstream=True, partial=True)
    assert [e.task_id for e in down.entries] == [tid("raw"), tid("clean"), tid("agg")]
    assert build_plan(graph, roots=[tid("calendar")]).entry(tid("calendar")).action == "run"
    assert build_plan(graph, include_static=True).entry(tid("calendar")).action == "run"
    assert build_plan(graph).entry(tid("dormant")).reason == "disabled"


def test_filters_and_incomplete_closure_never_start(project: Path) -> None:
    graph = graph_of(project)
    excluded = build_plan(graph, roots=[tid("agg")], exclude=[tid("clean")])
    assert [i.code for i in excluded.errors] == ["missing_upstream"]
    with pytest.raises(PlanError, match="missing_upstream"):
        run(project, excluded, graph, dry_run=True)
    partial = build_plan(graph, roots=[tid("agg")], exclude=[tid("clean")], partial=True)
    assert not partial.errors
    assert [i.severity for i in partial.issues] == ["warning"]
    only = build_plan(graph, roots=["main.sales.*"], include=[tid("side")])
    assert [e.task_id for e in only.entries] == [tid("side")]
    assert [i.code for i in build_plan(graph, roots=["nope.*"]).errors] == ["unknown_root"]
    assert [i.code for i in build_plan(graph, roots=[tid("side")], exclude=["*"]).errors] == ["empty_selection"]
    no_up = build_plan(graph, roots=[tid("agg")], upstream=False)
    assert [i.code for i in no_up.errors] == ["missing_upstream"]


def test_disabled_upstream_and_invalid_graph(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "catalogs/main/s/a.py": '@task(kind="table", enabled=False)\ndef a(ctx):\n    pass\n',
            "catalogs/main/s/b.py": '@task(kind="table")\ndef b(ctx):\n    ctx.read("a")\n',
        },
    )
    plan = build_plan(graph_of(root))
    assert [i.code for i in plan.errors] == ["disabled_upstream"]
    cyclic = write(
        tmp_path / "c",
        {
            "catalogs/main/s/a.py": '@task(kind="table", reads="b")\ndef a(ctx):\n    pass\n',
            "catalogs/main/s/b.py": '@task(kind="table", reads="a")\ndef b(ctx):\n    pass\n',
        },
    )
    bad = build_plan(graph_of(cyclic))
    assert [i.code for i in bad.errors] == ["invalid_graph"]
    assert bad.entries == ()
    started: list[str] = []

    def loader(*args: object) -> Callable[..., object]:
        started.append(str(args))
        return lambda _: None

    with pytest.raises(PlanError):
        run_plan(bad, project_root=cyclic, execution=CTX, table_io=InMemoryTableIO(), loader=loader)
    assert started == []


def test_plan_json_is_stable_and_tamper_evident(project: Path) -> None:
    graph = graph_of(project)
    plan = build_plan(graph, roots=[tid("agg")])
    assert plan.to_json() == build_plan(graph_of(project), roots=[tid("agg")]).to_json()
    again = Plan.from_json(plan.to_json())
    assert again == plan
    assert again.fingerprint == plan.fingerprint
    data = json.loads(plan.to_json())
    data["entries"][1]["targets"][0]["physical"] = "other.place.table"
    with pytest.raises(PlanError, match="fingerprint"):
        Plan.from_dict(data)
    code = "import sys, bricksmith.plan\nassert not {'bricksmith.runner', 'pyspark'} & set(sys.modules)"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_dry_run_performs_no_writes_and_imports_nothing(project: Path) -> None:
    graph = graph_of(project)
    plan = authorized(build_plan(graph, roots=[tid("agg")]), graph, "clean_orders", "order_totals")
    io = io_with_calendar(graph)
    called: list[str] = []

    def loader(*args: object) -> Callable[..., object]:
        called.append(str(args))
        return lambda _: None

    report = run(project, plan, graph, io=io, dry_run=True, loader=loader)
    assert called == []
    assert io.writes == []
    assert report.dry_run
    assert report.ids("not-run") == (tid("raw"), tid("clean"), tid("agg"))
    assert report.ids("skipped") == (tid("calendar"),)
    effects = next(r for r in report.results if r.task_id == tid("agg")).writes
    assert effects == ((physical(graph, "order_totals"), "merge"),)


def test_successful_run_uses_real_loader_and_runtime_context(project: Path) -> None:
    graph = graph_of(project)
    plan = authorized(build_plan(graph, roots=[tid("agg"), tid("side")]), graph, "clean_orders", "order_totals")
    io = io_with_calendar(graph)
    report = run(project, plan, graph, io=io, concurrency=1)
    assert report.ok
    assert report.ids("succeeded") == (tid("raw"), tid("clean"), tid("agg"), tid("side"))
    assert io.tables[physical(graph, "order_totals")] == 12
    assert [w[0] for w in io.writes][:2] == [physical(graph, "raw_orders"), physical(graph, "clean_orders")]
    assert RunReport.from_json(report.to_json()) == report


def test_failure_blocks_dependents_and_keeps_independent_branch(project: Path) -> None:
    graph = graph_of(project)
    context = ExecutionContext(environment="test", parameters={"fail": "clean"})
    plan = authorized(build_plan(graph, roots=[tid("agg"), tid("side")]), graph, "clean_orders", "order_totals")
    report = run(project, plan, graph, context=context)
    states = {r.task_id: r.state for r in report.results}
    assert states == {
        tid("calendar"): "skipped",
        tid("raw"): "succeeded",
        tid("clean"): "failed",
        tid("agg"): "blocked",
        tid("side"): "succeeded",
    }
    assert not report.ok
    blocked = next(r for r in report.results if r.state == "blocked")
    assert blocked.blocked_by == (tid("clean"),)
    failed = next(r for r in report.results if r.state == "failed")
    assert failed.error is not None
    assert failed.error["message"] == "boom"
    loaded = json.loads(report.to_json())
    assert loaded["counts"]["blocked"] == 1
    assert RunReport.from_dict(loaded) == report


def test_fail_fast_leaves_remaining_not_run(project: Path) -> None:
    graph = graph_of(project)
    context = ExecutionContext(environment="test", parameters={"fail": "raw"})
    plan = build_plan(graph, roots=[tid("raw"), tid("side")])
    report = run(project, plan, graph, context=context, concurrency=1, fail_fast=True)
    assert report.ids("failed") == (tid("raw"),)
    assert report.ids("not-run") == (tid("side"),)


def test_replay_selects_only_the_undone_work(project: Path) -> None:
    graph = graph_of(project)
    context = ExecutionContext(environment="test", parameters={"fail": "clean"})
    plan = authorized(build_plan(graph, roots=[tid("agg"), tid("side")]), graph, "clean_orders", "order_totals")
    report = run(project, plan, graph, context=context)
    replay = replay_plan(plan, report, graph)
    assert [(e.task_id, e.action) for e in replay.entries if e.action == "run"] == [
        (tid("clean"), "run"),
        (tid("agg"), "run"),
    ]
    assert replay.entry(tid("raw")).reason == "done in a previous run"
    assert tid("side") not in [e.task_id for e in replay.entries]
    assert replay.grants == ()
    io = io_with_calendar(graph)
    io.tables[physical(graph, "raw_orders")] = [1, 2, 3]  # persisted by the first run
    second = run(project, authorized(replay, graph, "clean_orders", "order_totals"), graph, io=io)
    assert second.ok
    assert physical(graph, "raw_orders") not in [w[0] for w in io.writes]
    other = build_plan(graph, roots=[tid("side")])
    with pytest.raises(PlanError, match="different plan"):
        replay_plan(other, report, graph)
    with pytest.raises(PlanError, match="nothing to replay"):
        replay_plan(other, run(project, other, graph), graph)


def test_stale_plan_is_rejected(project: Path, tmp_path: Path) -> None:
    graph = graph_of(project)
    plan = build_plan(graph, roots=[tid("side")])
    mapped = ExecutionContext(environment="test", schemas={"sales": "sales_dev"})
    with pytest.raises(StalePlanError, match="graph changed"):
        validate_plan(plan, graph_of(project, mapped))
    (project / "catalogs/main/sales/side.py").write_text(
        HEADER + '@task(kind="table", outputs="side_v2")\ndef side(ctx):\n    pass\n', encoding="utf-8"
    )
    with pytest.raises(StalePlanError, match="graph changed"):
        run(project, plan, graph_of(project), dry_run=True)
    assert tmp_path.exists()


def test_authorizing_one_protected_output_does_not_authorize_upstream(project: Path) -> None:
    graph = graph_of(project)
    plan = build_plan(graph, roots=[tid("agg")])
    with pytest.raises(AuthorizationError, match="order_totals:merge"):
        run(project, plan, graph, dry_run=True)
    only_agg = authorized(plan, graph, "order_totals")
    with pytest.raises(AuthorizationError) as caught:
        run(project, only_agg, graph)
    assert "clean_orders:write" in str(caught.value)
    assert "order_totals" not in str(caught.value)
    assert run(project, authorized(only_agg, graph, "clean_orders"), graph, dry_run=True).dry_run


def test_authorization_scope_is_checked(project: Path) -> None:
    graph = graph_of(project)
    plan = build_plan(graph, roots=[tid("agg")])
    with pytest.raises(AuthorizationError, match="not a target"):
        plan.authorize(Authorization("main.sales.side_table", ("write",)))
    with pytest.raises(AuthorizationError, match="not declared"):
        plan.authorize(Authorization(physical(graph, "clean_orders"), ("drop",)))
    ready = authorized(plan, graph, "clean_orders", "order_totals")
    narrower = build_plan(graph, roots=[tid("clean")])
    moved = dataclasses.replace(narrower, grants=ready.grants, granted_for=ready.granted_for)
    with pytest.raises(AuthorizationError, match="different plan"):
        run(project, moved, graph, dry_run=True)
    widened = dataclasses.replace(
        ready, grants=(*ready.grants, Authorization(physical(graph, "side_table"), ("write",)))
    )
    with pytest.raises(AuthorizationError, match="outside the reviewed plan"):
        run(project, dataclasses.replace(widened, granted_for=widened.fingerprint), graph, dry_run=True)
    assert Plan.from_json(ready.to_json()).grants == ready.grants


def test_write_guard_in_task_context(project: Path) -> None:
    graph = graph_of(project)
    plan = build_plan(graph, roots=[tid("clean")])
    entry = plan.entry(tid("clean"))
    io = InMemoryTableIO({physical(graph, "raw_orders"): [1]})
    ctx = TaskContext(entry, CTX, io)
    assert ctx.read("raw_orders") == [1]
    with pytest.raises(AuthorizationError):
        ctx.write("clean_orders", [])
    with pytest.raises(ValidationError, match="did not bind"):
        ctx.read("customers")
    with pytest.raises(ValidationError, match="not one of its targets"):
        ctx.write("raw_orders", [])
    granted = frozenset({(physical(graph, "clean_orders"), "write")})
    TaskContext(entry, CTX, io, granted).write("clean_orders", [2])
    with pytest.raises(ValidationError, match="not declared"):
        TaskContext(entry, CTX, io, granted).write("clean_orders", [], operation="drop")
    assert io.writes == [(physical(graph, "clean_orders"), "write")]


def sides(root: Path, count: int) -> tuple[TaskGraph, Plan]:
    write(
        root,
        {f"catalogs/main/s/t{i}.py": f'@task(kind="table")\ndef t{i}(ctx):\n    pass\n' for i in range(count)},
    )
    graph = graph_of(root)
    return graph, build_plan(graph)


def test_concurrency_bound_and_deterministic_submission_order(tmp_path: Path) -> None:
    graph, plan = sides(tmp_path, 6)
    lock = threading.Lock()
    state = {"now": 0, "max": 0}
    order: list[str] = []

    def loader(_root: object, path: str, _name: str) -> Callable[..., object]:
        def body(_ctx: object) -> None:
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
                order.append(path)
            time.sleep(0.05)
            with lock:
                state["now"] -= 1

        return body

    report = run(tmp_path, plan, graph, concurrency=2, loader=loader)
    assert report.ok
    assert state["max"] == 2
    assert order[:2] == [f"catalogs/main/s/t{i}.py" for i in range(2)]
    order.clear()
    run(tmp_path, plan, graph, concurrency=1, loader=loader)
    assert order == [f"catalogs/main/s/t{i}.py" for i in range(6)]
    assert [e.task_id for e in plan.entries] == [f"main.s.t{i}" for i in range(6)]
    assert plan.to_json() == build_plan(graph_of(tmp_path)).to_json()


def test_retry_policy(tmp_path: Path) -> None:
    graph, plan = sides(tmp_path, 1)
    calls: list[int] = []
    waits: list[float] = []

    def flaky(limit: int, error: Exception) -> Callable[..., Callable[..., object]]:
        def loader(*_: object) -> Callable[..., object]:
            def body(_ctx: object) -> None:
                calls.append(1)
                if len(calls) <= limit:
                    raise error

            return body

        return loader

    policy = RetryPolicy(max_attempts=3, delay=0.5, backoff=2.0)
    report = run(tmp_path, plan, graph, retry=policy, sleep=waits.append, loader=flaky(2, RuntimeError("x")))
    assert report.results[0].attempts == 3
    assert report.ok
    assert waits == [0.5, 1.0]
    calls.clear()
    report = run(tmp_path, plan, graph, retry=policy, sleep=waits.append, loader=flaky(9, RuntimeError("x")))
    assert (report.results[0].state, report.results[0].attempts, len(calls)) == ("failed", 3, 3)
    calls.clear()
    report = run(tmp_path, plan, graph, retry=policy, sleep=waits.append, loader=flaky(9, ValidationError("no")))
    assert (report.results[0].attempts, len(calls)) == (1, 1)
    with pytest.raises(ValidationError):
        RetryPolicy(max_attempts=0)


def test_cli_plan_apply_replay_and_entrypoint(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plan_file = tmp_path / "plan.json"
    base = ["--json"]
    assert (
        main([*base, "run", "plan", "--project", str(project), "--task", tid("side"), "--out", str(plan_file)])
        == EXIT_OK
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["fingerprint"] == Plan.from_json(plan_file.read_text()).fingerprint
    assert main([*base, "run", "plan", "--project", str(project), "--task", "nope"]) == EXIT_VALIDATION
    capsys.readouterr()
    assert main([*base, "run", "apply", "--project", str(project), "--plan", str(plan_file), "--dry-run"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    report_file = tmp_path / "report.json"
    # The default backend is unconfigured, so a task that writes fails; the report says so.
    code = main(
        [*base, "run", "apply", "--project", str(project), "--plan", str(plan_file), "--report", str(report_file)]
    )
    assert code == EXIT_RUN_FAILED
    capsys.readouterr()
    assert RunReport.from_json(report_file.read_text()).ids("failed") == (tid("side"),)
    replay = ["run", "replay", "--project", str(project), "--plan", str(plan_file), "--report-in", str(report_file)]
    assert main([*base, *replay]) == EXIT_OK
    assert [e["task_id"] for e in json.loads(capsys.readouterr().out)["entries"]] == [tid("side")]
    io = InMemoryTableIO()
    assert run_entrypoint(["--project", str(project), "--task", tid("side"), "--json"], table_io=io) == EXIT_OK
    assert [w[1] for w in io.writes] == ["write"]
    capsys.readouterr()
    assert run_entrypoint(["--project", str(project), "--task", tid("agg"), "--json"], table_io=io) == EXIT_VALIDATION
