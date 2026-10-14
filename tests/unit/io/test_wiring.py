"""External I/O in the task graph, the plan and a real run through the injectable provider."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from helpers import dst_rows, sink_db, source_db, sqlite_sink, sqlite_source

from bricksmith.context import ExecutionContext
from bricksmith.errors import ValidationError
from bricksmith.graph import load_graph
from bricksmith.io import ExternalResources, IoLink, Limits, io_lineage
from bricksmith.plan import Plan, build_plan
from bricksmith.runner import InMemoryTableIO, apply_plan, run_plan

FILES = {
    "catalogs/main/ingest/pull.py": """
        from bricksmith.tasks import task

        @task(kind="ingest", external="orders_src", outputs="orders_copy", metadata={"external_sinks": ["orders_dst"]})
        def pull(ctx):
            table = ctx.read_external("orders_src")
            ctx.write_external("orders_dst", table)
            ctx.write("orders_copy", len(table))
    """,
    "catalogs/main/ingest/undeclared.py": """
        from bricksmith.tasks import task

        @task(kind="ingest")
        def undeclared(ctx):
            ctx.write_external("orders_dst", [{"id": 1, "label": "x"}])
    """,
}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    for relative, text in FILES.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return tmp_path


def test_external_sources_and_sinks_appear_in_lineage(project: Path) -> None:
    graph = load_graph(project)
    links = io_lineage(graph)
    assert links == (
        IoLink("main.ingest.pull", "in", "orders_src"),
        IoLink("main.ingest.pull", "out", "orders_dst"),
    )
    assert links[0].to_dict() == {"direction": "in", "resource": "orders_src", "task": "main.ingest.pull"}
    entry = build_plan(graph).entry("main.ingest.pull")
    assert entry.sinks == ("orders_dst",)
    assert "external_sinks" not in entry.targets[0].options
    assert Plan.from_dict(build_plan(graph).to_dict()).entry("main.ingest.pull").sinks == ("orders_dst",)
    assert "sinks" not in build_plan(graph).entry("main.ingest.undeclared").to_dict()


def test_a_run_reads_and_writes_through_the_provider(project: Path, tmp_path: Path) -> None:
    src, dst = source_db(tmp_path / "s.db", 6), sink_db(tmp_path / "d.db")
    resources = ExternalResources(
        sources={"orders_src": lambda: sqlite_source(src)},
        sinks={"orders_dst": lambda: sqlite_sink(dst)},
        limits=Limits(batch_rows=4),
    )
    graph = load_graph(project)
    plan = build_plan(graph, roots=["main.ingest.pull"])
    tables = InMemoryTableIO()
    report = apply_plan(
        plan,
        graph,
        project_root=project,
        execution=ExecutionContext(environment="test", parameters={}),
        table_io=tables,
        externals=resources,
    )
    assert report.ok, report.to_dict()
    assert dst_rows(dst) == [(i, f"row-{i}") for i in range(1, 7)]
    assert list(tables.tables.values()) == [6]


def test_undeclared_sink_and_missing_provider_are_rejected(project: Path) -> None:
    graph = load_graph(project)
    context = ExecutionContext(environment="test", parameters={})
    plan = build_plan(graph, roots=["main.ingest.undeclared"])
    report = run_plan(plan, project_root=project, execution=context, table_io=InMemoryTableIO())
    assert not report.ok
    assert "did not declare" in str(report.to_dict())
    plan = build_plan(graph, roots=["main.ingest.pull"])
    failed = run_plan(plan, project_root=project, execution=context, table_io=InMemoryTableIO())
    assert not failed.ok  # without a provider the legacy path reads the bound physical name from table I/O


def test_unknown_resource_names_fail_clearly() -> None:
    with pytest.raises(ValidationError, match="no external source"):
        ExternalResources().read_external("nope")
    with pytest.raises(ValidationError, match="no external sink"):
        ExternalResources().write_external("nope", [{"a": 1}])
