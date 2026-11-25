"""A relative rules file resolves against the project root, never the working directory (synthetic)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from bricksmith.context import ExecutionContext
from bricksmith.graph import load_graph
from bricksmith.monitor import MonitorError
from bricksmith.monitor.model import parse_config as parse_model
from bricksmith.monitor.rules import load_rules_file, parse_rules
from bricksmith.monitor.stability import parse_config
from bricksmith.plan import build_plan
from bricksmith.runner import InMemoryTableIO, apply_plan
from tests.unit.monitor.model.test_model_engine import DECLARATION
from tests.unit.monitor.stability.test_stability_run import STABILITY, project

if TYPE_CHECKING:
    from pathlib import Path

RULES = '[[rules]]\nid = "r1"\nmetric = "psi"\nlimits = {green = 0.3, red = 0.5}\n'


def write_rules(root: Path, relative: str = "monitoring/rules.toml") -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RULES, encoding="utf-8")
    return relative


@pytest.fixture
def elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    return other


def test_relative_path_resolves_against_the_root_not_the_cwd(tmp_path: Path, elsewhere: Path) -> None:
    root = tmp_path / "proj"
    relative = write_rules(root)
    assert len(load_rules_file(relative, root=root)) == 1
    assert len(parse_rules(relative, root=str(root))) == 1
    assert len(parse_config({"period": "p", "rules": relative}, root).rules) == 1
    with pytest.raises(MonitorError, match="cannot be read"):
        parse_config({"period": "p", "rules": relative})
    assert elsewhere.is_dir()


def test_a_same_named_file_in_the_cwd_is_not_used(tmp_path: Path, elsewhere: Path) -> None:
    root = tmp_path / "proj"
    write_rules(root)
    (elsewhere / "monitoring").mkdir()
    (elsewhere / "monitoring/rules.toml").write_text("rules = []\n[[x]]\n", encoding="utf-8")
    assert [r.id for r in parse_config({"period": "p", "rules": "monitoring/rules.toml"}, root).rules] == ["r1"]


def test_an_absolute_path_keeps_working(tmp_path: Path, elsewhere: Path) -> None:
    root = tmp_path / "proj"
    write_rules(root)
    absolute = str(root / "monitoring/rules.toml")
    assert len(parse_config({"period": "p", "rules": absolute}, tmp_path / "unrelated").rules) == 1
    assert len(parse_config({"period": "p", "rules": absolute}).rules) == 1
    assert elsewhere.is_dir()


def test_model_monitoring_resolves_against_the_root(tmp_path: Path, elsewhere: Path) -> None:
    root = tmp_path / "proj"
    relative = write_rules(root, "monitoring/model.json")
    (root / relative).write_text(
        json.dumps({"rules": [{"id": "m1", "metric": "psi", "limits": {"green": 0.3, "red": 0.5}}]}), encoding="utf-8"
    )
    declared = {**DECLARATION, "rules": relative}
    assert [r.id for r in parse_model(declared, root).rules] == ["m1"]
    with pytest.raises(MonitorError, match="cannot be read"):
        parse_model(declared)
    assert elsewhere.is_dir()


@pytest.mark.usefixtures("elsewhere")
def test_plan_build_and_worker_use_the_project_root(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    stability = {**STABILITY, "rules": write_rules(root)}
    project(root, stability)
    (root / "bricksmith.toml").write_text("", encoding="utf-8")
    context = ExecutionContext(environment="test", parameters={"drift": "no"})
    graph = load_graph(root, context=context)
    plan = build_plan(graph)
    assert not plan.errors
    result = apply_plan(plan, graph, project_root=root, execution=context, table_io=InMemoryTableIO())
    assert result.ok
    assert result.results[0].stability[0]["status"] == "passed"
    assert "error" not in result.results[0].stability[0] or not result.results[0].stability[0]["error"]
