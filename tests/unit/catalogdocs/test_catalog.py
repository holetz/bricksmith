"""Catalog report: independent expectations, determinism, incomplete metadata and offline behavior."""

from __future__ import annotations

import json
import random
import subprocess
import sys
import textwrap
from dataclasses import replace
from typing import TYPE_CHECKING

from bricksmith.catalogdocs import build_catalog, to_dict, to_json, to_markdown
from bricksmith.cli.main import main
from bricksmith.errors import EXIT_CONFIG, EXIT_OK
from bricksmith.graph import load_graph
from bricksmith.registry import parse_registry

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

FILES = {
    "catalogs/main/sales/orders.py": """
        import pyspark  # heavy: must never be imported
        from bricksmith.tasks import task

        raise RuntimeError("task modules must not be executed")

        @task(kind="table", outputs="orders")
        def orders(ctx):
            return None
    """,
    "catalogs/main/sales/features.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="features")
        def features(ctx):
            return ctx.read("orders")
    """,
    "catalogs/main/sales/churn.py": """
        from bricksmith.tasks import task

        @task(
            kind="train",
            outputs="churn_runs",
            metadata={"model_task": {"model": "churn_v1", "role": "train", "secret": "do-not-dump"}},
        )
        def train(ctx):
            return ctx.read("features")

        @task(
            kind="predict",
            outputs="churn_scores",
            metadata={"model_task": {"model": "churn_v1", "role": "predict", "alias": "champion"}},
        )
        def predict(ctx):
            return ctx.read("features")
    """,
}

REGISTRY = {
    "version": 1,
    "columns": [
        {"name": "id", "type": "bigint", "description": "Key | with `pipe`", "sensitivity": "public"},
        {"name": "email", "type": "string", "description": "Mail", "sensitivity": "unclassified"},
    ],
    "tables": {
        "main.sales.orders": {
            "description": "Orders *table*",
            "grain": ["id"],
            "columns": {"id": {}, "email": {}, "ghost": {}},
        },
        "main.sales.orphan": {"columns": {"id": {}}},
    },
}


def project(root: Path) -> Path:
    for relative, text in FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
    return root


def registry() -> object:
    return parse_registry(REGISTRY)


def test_expected_lineage_models_and_coverage(tmp_path: Path) -> None:
    graph = load_graph(project(tmp_path))
    report = to_dict(build_catalog(graph, registry()))  # type: ignore[arg-type]
    assert [t["name"] for t in report["tables"]] == [  # type: ignore[index]
        "main.sales.churn_runs",
        "main.sales.churn_scores",
        "main.sales.features",
        "main.sales.orders",
    ]
    lineage = {(x["consumer"], x["producer"]) for x in report["lineage"]}  # type: ignore[attr-defined]
    assert lineage == {
        ("main.sales.features", "main.sales.orders"),
        ("main.sales.train", "main.sales.features"),
        ("main.sales.predict", "main.sales.features"),
    }
    assert report["models"] == [  # type: ignore[comparison-overlap]
        {
            "name": "churn_v1",
            "tasks": [
                {"alias": "", "id": "main.sales.train", "role": "train"},
                {"alias": "champion", "id": "main.sales.predict", "role": "predict"},
            ][::-1],
        }
    ]
    assert report["coverage"] == {  # type: ignore[comparison-overlap]
        "classified": ["main.sales.orders.id"],
        "unclassified": ["main.sales.orders.email"],
        "unknown": ["main.sales.orders.ghost"],
    }
    assert report["registry_only"] == ["main.sales.orphan"]
    assert report["incomplete"] == ["main.sales.churn_runs", "main.sales.churn_scores", "main.sales.features"]
    orders = next(t for t in report["tables"] if t["name"] == "main.sales.orders")  # type: ignore[attr-defined]
    assert orders["grain"] == ["id"]
    assert "do-not-dump" not in json.dumps(report)


def test_shuffled_input_is_stable(tmp_path: Path) -> None:
    graph = load_graph(project(tmp_path))
    expected = to_json(build_catalog(graph, registry()))  # type: ignore[arg-type]
    expected_md = to_markdown(build_catalog(graph, registry()))  # type: ignore[arg-type]
    rng = random.Random(7)  # noqa: S311 - deterministic shuffle, not security
    for _ in range(5):
        parts = {name: list(getattr(graph, name)) for name in ("tasks", "outputs", "edges", "diagnostics")}
        for items in parts.values():
            rng.shuffle(items)
        shuffled = replace(graph, **{k: tuple(v) for k, v in parts.items()})
        report = build_catalog(shuffled, registry())  # type: ignore[arg-type]
        assert to_json(report) == expected
        assert to_markdown(report) == expected_md


def test_missing_registry_is_visibly_incomplete(tmp_path: Path) -> None:
    report = build_catalog(load_graph(project(tmp_path)))
    assert not report.registry_loaded
    assert len(report.incomplete) == 4
    assert "column metadata is incomplete" in to_markdown(report)
    assert report.classified == report.unclassified == report.unknown == ()


def test_markdown_escapes_cells(tmp_path: Path) -> None:
    text = to_markdown(build_catalog(load_graph(project(tmp_path)), registry()))  # type: ignore[arg-type]
    assert "Key \\| with \\`pipe\\`" in text
    assert "Orders \\*table\\*" in text


def test_cli_stdout_file_and_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path / "p")
    reg = tmp_path / "registry.toml"
    reg.write_text(
        'version = 1\n[[columns]]\nname = "id"\ntype = "bigint"\nsensitivity = "public"\n'
        '[tables."main.sales.orders".columns.id]\n',
        encoding="utf-8",
    )
    base = ["--env", "free", "--env-file", str(tmp_path / "none.env")]
    (tmp_path / "none.env").write_text("", encoding="utf-8")
    assert main([*base, "catalog", "render", "--project", str(root), "--registry", str(reg), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["registry_loaded"] is True
    out = tmp_path / "catalog.md"
    assert main([*base, "catalog", "render", "--project", str(root), "--output", str(out)]) == EXIT_OK
    assert capsys.readouterr().out == ""
    assert out.read_text(encoding="utf-8").startswith("# Catalog")
    bad = main([*base, "catalog", "render", "--project", str(root), "--registry", str(tmp_path / "missing.toml")])
    assert bad != EXIT_OK
    assert "cannot read registry" in capsys.readouterr().err
    blocked = main([*base, "catalog", "render", "--project", str(root), "--output", str(tmp_path)])
    assert blocked != EXIT_OK
    assert "cannot write" in capsys.readouterr().err
    assert EXIT_CONFIG != EXIT_OK


def test_render_never_executes_or_imports_heavy_modules(tmp_path: Path) -> None:
    root = project(tmp_path)
    script = (
        "import sys\n"
        "from bricksmith.catalogdocs import build_catalog, to_json\n"
        "from bricksmith.graph import load_graph\n"
        "to_json(build_catalog(load_graph(sys.argv[1])))\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in ('pyspark', 'mlflow', 'databricks')))\n"
    )
    done = subprocess.run([sys.executable, "-c", script, str(root)], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


EXEMPTION_TOML = '[[exemptions]]\nrule = "cycle"\nsubject = "x"\nreason = "r"\nexpires = {expires}\n'


def test_catalog_output_does_not_depend_on_exemption_expiry(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    base = ["--env", "free", "--env-file", str(tmp_path / "none.env")]
    (tmp_path / "none.env").write_text("", encoding="utf-8")
    for expires in ("2020-01-01", "2099-01-01"):
        (root / "bricksmith.toml").write_text(EXEMPTION_TOML.format(expires=expires), encoding="utf-8")
        assert main([*base, "catalog", "render", "--project", str(root), "--format", "json"]) == 0
        text = capsys.readouterr().out
        assert "unused_exemption" in text
        assert "expired_exemption" not in text
