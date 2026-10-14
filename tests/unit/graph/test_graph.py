from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from datetime import date
from typing import TYPE_CHECKING

import pytest

from bricksmith.cli.main import main
from bricksmith.context import ExecutionContext
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, ConfigError, ValidationError
from bricksmith.graph import TaskGraph, load_graph
from bricksmith.tasks import declaration_of, task
from bricksmith.tasks.discovery import discover
from bricksmith.tasks.project import load_project

if TYPE_CHECKING:
    from pathlib import Path

TODAY = date(2026, 6, 1)
HEADER = "from bricksmith.tasks import task\n"

MIXED = {
    "catalogs/main/sales/orders.py": """
        import pyspark  # heavy: discovery must never import this module
        from bricksmith.tasks import task

        raise RuntimeError("task modules must not be executed during discovery")

        @task(kind="table", external="landing.raw.orders_feed", static=False)
        def orders(ctx):
            return ctx.read_external("landing.raw.orders_feed")
    """,
    "catalogs/main/sales/helpers.py": """
        def load_orders(context, columns=()):
            return context.read("orders")
    """,
    "catalogs/main/sales/features.py": """
        from bricksmith.tasks import task
        from .helpers import load_orders

        @task(kind="table", outputs="customer_features", metadata={"write": "merge", "keys": ["customer_id"]})
        def build_features(ctx):
            return load_orders(ctx, columns=("customer_id",))
    """,
    "catalogs/main/sales/models/churn/train.py": """
        import bricksmith.tasks as bt

        @bt.task(kind="train", name="train_churn", outputs="churn_runs")
        def run(ctx):
            return ctx.read("customer_features")
    """,
    "catalogs/main/sales/models/churn/predict.py": """
        from bricksmith.tasks import task

        @task(kind="predict", name="predict_churn", outputs="churn_scores", reads="churn_runs")
        def run(ctx):
            return ctx.read("customer_features")
    """,
    "catalogs/main/sales/monitoring/drift.py": """
        from bricksmith import tasks

        @tasks.task(kind="monitor", name="churn_drift", outputs="churn_drift_metrics")
        def run(ctx):
            current = ctx.read("churn_scores")
            earlier = ctx.read_state("churn_drift_metrics")
            return current, earlier
    """,
}


def write(root: Path, files: dict[str, str], toml: str = "") -> Path:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    if toml:
        (root / "bricksmith.toml").write_text(textwrap.dedent(toml), encoding="utf-8")
    return root


def graph_of(root: Path, files: dict[str, str], toml: str = "", **options: object) -> TaskGraph:
    return load_graph(write(root, files, toml), today=TODAY, **options)  # type: ignore[arg-type]


def codes(graph: TaskGraph, severity: str = "error") -> list[str]:
    return [item.code for item in graph.diagnostics if item.severity == severity]


def producers(graph: TaskGraph, consumer: str, kind: str = "data") -> set[tuple[str | None, str]]:
    return {(edge.producer, edge.provenance) for edge in graph.edges if edge.consumer == consumer and edge.kind == kind}


def one_task(function: str, body: str = "return None", /, **fields: object) -> str:
    arguments = "".join(f", {key}={value!r}" for key, value in fields.items())
    return f"{HEADER}\n@task(kind='table'{arguments})\ndef {function}(ctx):\n    {body}\n"


def test_mixed_schema_is_discovered_offline(tmp_path: Path) -> None:
    write(tmp_path, MIXED)
    script = (
        "import json, sys\n"
        "from bricksmith.graph import load_graph\n"
        "graph = load_graph(sys.argv[1])\n"
        "heavy = [m for m in sys.modules if m.split('.')[0] in ('pyspark', 'mlflow', 'databricks', 'orders')]\n"
        "print(json.dumps({'ok': graph.ok, 'kinds': sorted({t.kind for t in graph.tasks}), 'heavy': heavy}))\n"
    )
    done = subprocess.run([sys.executable, "-c", script, str(tmp_path)], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout) == {"ok": True, "kinds": ["monitor", "predict", "table", "train"], "heavy": []}


def test_mixed_schema_shares_one_scope_and_graph(tmp_path: Path) -> None:
    graph = graph_of(tmp_path, MIXED)
    assert graph.ok
    assert graph.diagnostics == ()
    assert {node.scope for node in graph.tasks} == {"main.sales"}
    assert graph.order() == (
        "main.sales.orders",
        "main.sales.build_features",
        "main.sales.train_churn",
        "main.sales.predict_churn",
        "main.sales.churn_drift",
    )
    assert graph.task("main.sales.build_features").metadata == {"keys": ["customer_id"], "write": "merge"}
    assert producers(graph, "main.sales.predict_churn") == {
        ("main.sales.train_churn", "declared"),
        ("main.sales.build_features", "inferred"),
    }
    assert producers(graph, "main.sales.churn_drift", "temporal") == {("main.sales.churn_drift", "inferred")}
    external = [edge for edge in graph.edges if edge.kind == "external"]
    assert {(edge.target, edge.producer, edge.provenance) for edge in external} == {
        ("landing.raw.orders_feed", None, "declared"),
        ("landing.raw.orders_feed", None, "inferred"),
    }


def test_helper_read_keeps_its_own_location(tmp_path: Path) -> None:
    graph = graph_of(tmp_path, MIXED)
    (edge,) = [edge for edge in graph.edges if edge.consumer == "main.sales.build_features"]
    assert (edge.producer, edge.provenance, edge.path, edge.line) == (
        "main.sales.orders",
        "inferred",
        "catalogs/main/sales/helpers.py",
        3,
    )


def test_nested_folders_keep_the_schema_scope(tmp_path: Path) -> None:
    graph = graph_of(tmp_path, MIXED)
    node = graph.task("main.sales.train_churn")
    assert (node.catalog, node.schema, node.path) == ("main", "sales", "catalogs/main/sales/models/churn/train.py")
    assert node.function == "run"


def test_renamed_output_is_referenced_by_output_name_only(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/a.py": one_task("build", outputs=["daily_totals", "daily_rejects"]),
        "catalogs/main/sales/b.py": one_task("good", reads="daily_rejects"),
        "catalogs/main/sales/c.py": one_task("bad", reads="build"),
    }
    graph = graph_of(tmp_path, files)
    assert graph.task("main.sales.build").outputs == ("main.sales.daily_totals", "main.sales.daily_rejects")
    assert graph.dependencies("main.sales.good") == ("main.sales.build",)
    assert [item.subject for item in graph.errors] == ["main.sales.bad -> build"]


def test_same_local_names_in_different_schemas(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/daily.py": one_task("daily"),
        "catalogs/main/sales/report.py": one_task("report", "return ctx.read('daily')"),
        "catalogs/main/stock/daily.py": one_task("daily"),
        "catalogs/main/stock/report.py": one_task("report", "return ctx.read('daily')"),
        "catalogs/lab/sales/daily.py": one_task("daily"),
    }
    graph = graph_of(tmp_path, files)
    assert graph.ok
    assert [node.id for node in graph.tasks] == [
        "lab.sales.daily",
        "main.sales.daily",
        "main.sales.report",
        "main.stock.daily",
        "main.stock.report",
    ]
    assert graph.dependencies("main.sales.report") == ("main.sales.daily",)
    assert graph.dependencies("main.stock.report") == ("main.stock.daily",)


def test_no_edge_is_resolved_by_an_unrelated_short_name(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/orders.py": one_task("orders"),
        "catalogs/main/stock/short.py": one_task("short", "return ctx.read('orders')"),
        "catalogs/main/stock/partial.py": one_task("partial", "return ctx.read('sales.orders')"),
        "catalogs/lab/stock/full.py": one_task("full", "return ctx.read('main.sales.orders')"),
        "catalogs/lab/stock/wrong.py": one_task("wrong", "return ctx.read('sales.orders')"),
    }
    graph = graph_of(tmp_path, files)
    assert graph.dependencies("main.stock.short") == ()
    assert graph.dependencies("main.stock.partial") == ("main.sales.orders",)
    assert graph.dependencies("lab.stock.full") == ("main.sales.orders",)
    assert graph.dependencies("lab.stock.wrong") == ()
    missing = {item.subject: item.message for item in graph.errors}
    assert set(missing) == {"main.stock.short -> orders", "lab.stock.wrong -> sales.orders"}
    assert "main.sales.orders (qualify the reference)" in missing["main.stock.short -> orders"]
    unresolved = [edge for edge in graph.edges if edge.consumer == "main.stock.short"]
    assert [(edge.target, edge.producer) for edge in unresolved] == [(None, None)]


def test_dynamic_reads_must_be_declared(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/north.py": one_task("north"),
        "catalogs/main/sales/hidden.py": one_task("hidden", "return ctx.read(f'{ctx.region}')"),
        "catalogs/main/sales/covered.py": one_task("covered", "return ctx.read(pick())", reads="north"),
    }
    graph = graph_of(tmp_path, files)
    found = {(item.subject, item.code, item.severity) for item in graph.diagnostics}
    assert found == {
        ("main.sales.hidden", "dynamic_read", "error"),
        ("main.sales.covered", "dynamic_read", "info"),
    }
    assert producers(graph, "main.sales.covered") == {("main.sales.north", "declared")}


def test_context_passed_to_an_unfollowed_helper_is_a_coverage_limit(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/a.py": f"import shared\n{one_task('a', 'return shared.load(ctx)')}",
        "catalogs/main/sales/b.py": one_task("b", "alias = ctx\n    return alias.read('a')"),
    }
    graph = graph_of(tmp_path, files)
    assert [(item.code, item.severity, item.subject) for item in graph.diagnostics] == [
        ("unfollowed_helper", "warning", "main.sales.a")
    ]
    assert graph.dependencies("main.sales.b") == ("main.sales.a",)


def test_helper_module_import_and_keyword_binding(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/src.py": one_task("src"),
        "catalogs/main/sales/lib/__init__.py": "",
        "catalogs/main/sales/lib/io.py": "def load(limit, source):\n    return inner(source)\n\n"
        "def inner(handle):\n    return handle.read('src')\n",
        "catalogs/main/sales/use.py": f"from .lib import io\n{one_task('use', 'return io.load(10, source=ctx)')}",
    }
    graph = graph_of(tmp_path, files)
    assert graph.ok
    assert producers(graph, "main.sales.use") == {("main.sales.src", "inferred")}


def test_earlier_window_state_is_not_a_cycle(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/balance.py": one_task("balance", "return ctx.read('moves')", state="balance"),
        "catalogs/main/sales/moves.py": one_task("moves", state="balance"),
    }
    graph = graph_of(tmp_path, files)
    assert graph.ok
    assert graph.order() == ("main.sales.moves", "main.sales.balance")
    assert graph.dependencies("main.sales.balance", kind="temporal") == ("main.sales.balance",)
    assert graph.dependencies("main.sales.moves", kind="temporal") == ("main.sales.balance",)


def test_ordinary_cycles_are_rejected(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/a.py": one_task("a", reads="b"),
        "catalogs/main/sales/b.py": one_task("b", reads="a"),
        "catalogs/main/sales/own.py": one_task("own", "return ctx.read('own')"),
        "catalogs/main/sales/free.py": one_task("free"),
    }
    graph = graph_of(tmp_path, files)
    assert [(item.code, item.subject) for item in graph.errors] == [
        ("cycle", "main.sales.a"),
        ("cycle", "main.sales.own"),
    ]
    with pytest.raises(ValidationError, match="cycle"):
        graph.order()


def test_missing_producer_is_reported_and_retained(tmp_path: Path) -> None:
    graph = graph_of(tmp_path, {"catalogs/main/sales/a.py": one_task("a", reads="nowhere", state="gone")})
    assert codes(graph) == ["missing_producer", "missing_producer"]
    assert {(edge.kind, edge.reference, edge.producer) for edge in graph.edges} == {
        ("data", "nowhere", None),
        ("temporal", "gone", None),
    }


def test_external_input_needs_no_producer(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/a.py": one_task("a", external=["vendor.public.rates", "a_copy"]),
        "catalogs/main/sales/a_copy.py": one_task("a_copy"),
    }
    graph = graph_of(tmp_path, files)
    assert graph.ok
    assert codes(graph, "warning") == ["external_shadows_output"]
    assert graph.dependencies("main.sales.a") == ()


def test_duplicate_identity(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/first.py": one_task("totals", outputs="t1"),
        "catalogs/main/sales/nested/second.py": one_task("other", name="totals", outputs="t2"),
    }
    graph = graph_of(tmp_path, files)
    assert [(item.code, item.subject) for item in graph.errors] == [("duplicate_identity", "main.sales.totals")]
    assert [node.path for node in graph.tasks] == ["catalogs/main/sales/first.py"]


def test_two_producers_of_one_output(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/a.py": one_task("a", outputs="shared"),
        "catalogs/main/sales/b.py": one_task("b", outputs="shared"),
        "catalogs/main/sales/c.py": one_task("c", reads="shared"),
    }
    graph = graph_of(tmp_path, files)
    assert [(item.code, item.subject) for item in graph.errors] == [
        ("ambiguous_reference", "main.sales.c -> shared"),
        ("output_collision", "main.sales.shared"),
    ]
    assert graph.dependencies("main.sales.c") == ("main.sales.a", "main.sales.b")


def test_physical_collision_from_project_and_context_mappings(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/a.py": one_task("totals"),
        "catalogs/main/stock/a.py": one_task("totals"),
    }
    toml = """
        [mappings.catalogs]
        main = "dev"
        [mappings.schemas]
        sales = "shared"
        stock = "shared"
    """
    graph = graph_of(tmp_path, files, toml)
    assert [(item.code, item.subject) for item in graph.errors] == [("physical_collision", "dev.shared.totals")]
    assert [node.id for node in graph.tasks] == ["main.sales.totals", "main.stock.totals"]
    context = ExecutionContext(environment="free", schemas={"stock": "stock_dev"})
    remapped = load_graph(tmp_path, context=context, today=TODAY)
    assert remapped.ok
    assert [item.physical for item in remapped.outputs] == ["dev.shared.totals", "dev.stock_dev.totals"]


def test_physical_names_collide_without_regard_to_case(tmp_path: Path) -> None:
    graph = graph_of(tmp_path, {"catalogs/main/sales/a.py": one_task("a", outputs=["Totals", "totals"])})
    assert codes(graph) == ["physical_collision"]


EXEMPTION = """
    [[exemptions]]
    rule = "missing_producer"
    subject = "main.sales.a -> *"
    reason = "producer arrives with the next release"
    expires = {expires}

    [[exemptions]]
    rule = "cycle"
    subject = "main.sales.zzz"
    reason = "never matches"
    expires = 2030-01-01
"""


def test_live_exemption_downgrades_the_finding(tmp_path: Path) -> None:
    files = {"catalogs/main/sales/a.py": one_task("a", reads="later")}
    graph = graph_of(tmp_path, files, EXEMPTION.format(expires="2026-06-01"))
    assert graph.ok
    assert [(item.code, item.severity) for item in graph.diagnostics] == [
        ("missing_producer", "exempt"),
        ("unused_exemption", "warning"),
    ]
    assert "exempt until 2026-06-01: producer arrives" in graph.diagnostics[0].message


def test_expired_exemption_fails(tmp_path: Path) -> None:
    files = {"catalogs/main/sales/a.py": one_task("a", reads="later")}
    graph = graph_of(tmp_path, files, EXEMPTION.format(expires="2026-05-31"))
    assert not graph.ok
    assert codes(graph) == ["expired_exemption", "missing_producer"]


def test_exemption_without_expiry_is_a_configuration_error(tmp_path: Path) -> None:
    toml = '[[exemptions]]\nrule = "cycle"\nsubject = "x"\nreason = "y"\n'
    with pytest.raises(ConfigError, match="expires"):
        graph_of(tmp_path, {}, toml)
    (tmp_path / "bricksmith.toml").write_text("[tasks\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_project(tmp_path)


def test_parameter_variants_have_their_own_identity_and_outputs(tmp_path: Path) -> None:
    variants = [{"region": "north"}, {"region": "south"}]
    files = {
        "catalogs/main/sales/by_region.py": one_task("totals", outputs="totals_{region}", variants=variants),
        "catalogs/main/sales/north.py": one_task("north_report", reads="totals_north"),
        "catalogs/main/sales/shared.py": one_task("shared", variants=variants),
        "catalogs/main/sales/loose.py": one_task("loose", outputs="loose_{window}"),
    }
    graph = graph_of(tmp_path, files)
    assert [node.id for node in graph.tasks][-2:] == [
        "main.sales.totals[region=north]",
        "main.sales.totals[region=south]",
    ]
    assert graph.task("main.sales.totals[region=south]").variant == {"region": "south"}
    assert graph.dependencies("main.sales.north_report") == ("main.sales.totals[region=north]",)
    assert [(item.code, item.subject) for item in graph.errors] == [
        ("output_collision", "main.sales.shared"),
        ("unknown_placeholder", "main.sales.loose"),
    ]


def test_non_literal_and_invalid_declarations_are_diagnosed(tmp_path: Path) -> None:
    files = {
        "catalogs/main/sales/a.py": f"{HEADER}NAME = 'x'\n@task(kind='table', name=NAME)\ndef a(ctx): ...\n",
        "catalogs/main/sales/b.py": f"{HEADER}R = ['a']\n@task(kind='table', reads=R)\ndef b(ctx): ...\n",
        "catalogs/main/sales/c.py": f"{HEADER}@task\ndef c(ctx): ...\n",
        "catalogs/main/sales/d.py": f"{HEADER}@task(kind='table', colour='red')\ndef d(ctx): ...\n",
        "catalogs/main/sales/e.py": f"{HEADER}@task(kind='table'\n",
        "catalogs/main/loose.py": f"{HEADER}@task(kind='table')\ndef loose(ctx): ...\n",
    }
    graph = graph_of(tmp_path, files)
    assert [node.id for node in graph.tasks] == ["main.sales.b"]
    assert sorted((item.code, item.subject) for item in graph.errors) == [
        ("invalid_declaration", "catalogs/main/sales/c.py:c"),
        ("invalid_declaration", "catalogs/main/sales/d.py:d"),
        ("invalid_scope", "catalogs/main/loose.py:loose"),
        ("non_literal_declaration", "catalogs/main/sales/a.py:a"),
        ("non_literal_declaration", "catalogs/main/sales/b.py:b"),
        ("unreadable_module", "catalogs/main/sales/e.py"),
    ]


def test_exclusions_and_custom_root(tmp_path: Path) -> None:
    files = {
        "data/main/sales/keep.py": one_task("keep"),
        "data/main/sales/drafts/skip.py": one_task("skip"),
        "data/main/sales/.hidden/skip.py": one_task("hidden"),
        "catalogs/main/sales/ignored.py": one_task("ignored"),
    }
    toml = '[tasks]\nroot = "data"\nexclude = ["*/drafts/*"]\n'
    project = load_project(write(tmp_path, files, toml))
    found = discover(project)
    assert [item.qualified_name for item in found.definitions] == ["main.sales.keep"]
    assert found.files == ("data/main/sales/keep.py",)


def test_serialization_is_stable_and_round_trips(tmp_path: Path) -> None:
    files = {**MIXED, "catalogs/main/sales/broken.py": one_task("broken", reads="nowhere")}
    graph = graph_of(tmp_path, files)
    text = graph.to_json()
    assert TaskGraph.from_json(text) == graph
    assert TaskGraph.from_json(text).to_json() == text
    assert load_graph(tmp_path, today=TODAY).to_json() == text
    assert json.loads(text)["version"] == 1
    with pytest.raises(ValidationError, match="version"):
        TaskGraph.from_dict({"version": 0})


def test_decorator_attaches_the_same_validated_declaration() -> None:
    @task(kind="table", outputs="totals", reads=["a", "main.sales.b"], variants=[{"window": 7}])
    def build(ctx: object) -> object:
        return ctx

    declaration = declaration_of(build)
    assert declaration is not None
    assert (declaration.name, declaration.outputs, declaration.reads) == ("build", ("totals",), ("a", "main.sales.b"))
    assert build(1) == 1
    assert declaration_of(len) is None
    for bad in ({"reads": "a.b.c.d"}, {"outputs": "other.name"}, {"metadata": {"x": {1, 2}}}, {"enabled": 1}):
        with pytest.raises(ValidationError):
            task(kind="table", **bad)(build)  # type: ignore[arg-type]


def test_cli_lists_graphs_and_validates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write(tmp_path, MIXED)
    root = str(tmp_path)
    assert main(["tasks", "list", "--project", root, "--json"]) == EXIT_OK
    assert [item["kind"] for item in json.loads(capsys.readouterr().out)["tasks"]] == [
        "table",
        "monitor",
        "table",
        "predict",
        "train",
    ]
    assert main(["tasks", "graph", "--project", root, "--json"]) == EXIT_OK
    assert TaskGraph.from_json(capsys.readouterr().out).ok
    assert main(["tasks", "graph", "--project", root]) == EXIT_OK
    assert "main.sales.train_churn <- main.sales.build_features  (customer_features; data, inferred)" in (
        capsys.readouterr().out
    )
    assert main(["tasks", "validate", "--project", root]) == EXIT_OK
    assert capsys.readouterr().out.strip() == "5 tasks, 8 edges, 0 errors"
    write(tmp_path, {"catalogs/main/sales/broken.py": one_task("broken", reads="nowhere")})
    assert main(["--json", "tasks", "validate", "--project", root]) == EXIT_VALIDATION
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert [item["code"] for item in payload["diagnostics"]] == ["missing_producer"]
