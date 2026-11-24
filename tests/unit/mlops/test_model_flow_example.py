"""The end-to-end model flow example: it discovers and plans offline, and its declarations agree with its code.

Spark, scikit-learn and MLflow are absent offline. The Free Edition proof (``tests/remote/test_model_flow.py``) runs
the tasks; here the row engines check what can run without them.
"""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from bricksmith.graph import load_graph
from bricksmith.mlops.featurestore import feature_table_of
from bricksmith.plan import build_plan
from bricksmith.runner.loader import load_task_function
from bricksmith.tasks import declaration_of

ROOT = Path(__file__).resolve().parents[3] / "examples" / "model_flow"
FLOW = "catalogs/demo/flow"


TASK_OF = {"source.py": "accounts_monthly", "features.py": "account_features"}


def module_function(file: str, name: str) -> Any:  # noqa: ANN401 - a task, a helper or a constant of the module
    task = load_task_function(ROOT, f"{FLOW}/{file}", TASK_OF[file])
    return getattr(sys.modules[task.__module__], name)


def test_the_example_plans_without_errors() -> None:
    plan = build_plan(load_graph(ROOT))
    assert not plan.errors, plan.errors
    tasks = {entry.task_id for entry in plan.entries}
    assert {"demo.flow.accounts_monthly", "demo.flow.account_features", "demo.flow.customer_features"} <= tasks


def test_feature_tasks_plan_replace_by_closing_and_stability_tables() -> None:
    plan = build_plan(load_graph(ROOT))
    by_task = {entry.task_id: entry for entry in plan.entries}
    for task, key in (("account_features", "account_id"), ("customer_features", "customer_id")):
        targets = by_task[f"demo.flow.{task}"].targets
        main = next(t for t in targets if t.name.endswith(f".{task}"))
        assert main.options["write"] == "replace_partitions"
        assert main.options["keys"] == [key, "closing_date"]
        assert any("stability_" in t.name for t in targets)


def test_the_source_is_deterministic_and_monthly() -> None:
    rows = module_function("source.py", "source_rows")
    closings = module_function("source.py", "closings")()
    first, second = rows(), rows()
    assert first == second
    assert {r[2] for r in first} <= set(closings) and len(closings) == 24
    assert len({(r[0], r[2]) for r in first}) == len(first)  # one row per account and closing
    assert {r[1] for r in first} == {(a + 1) // 2 for a in {r[0] for r in first}}
    closed_accounts = {r[0] for r in first if r[8]}
    assert 0.1 < len(closed_accounts) / len({r[0] for r in first}) < 0.6


def declared_features(task: str) -> set[str]:
    declared = declaration_of(module_function("features.py", task))
    assert declared is not None
    definition = feature_table_of(declared.metadata)
    assert definition is not None
    return {feature.name for feature in definition.features}


def test_declared_feature_columns_match_what_the_specs_compute() -> None:
    pytest.importorskip("numpy")
    from bricksmith.features import compute_rows  # noqa: PLC0415 - numpy only when the test runs

    names = ("account_id", "customer_id", "closing_date", "balance", "payments", "days_past_due", "status")
    records = [dict(zip(names, r[:7], strict=True)) for r in module_function("source.py", "source_rows")()]
    account = module_function("features.py", "ACCOUNT_SPEC")
    computed = compute_rows([{k: v for k, v in r.items() if k != "customer_id"} for r in records], account)
    columns = set(module_function("features.py", "ACCOUNT_COLUMNS"))
    assert columns <= set(computed[0]) and declared_features("account_features") == columns | {"segment"}
    customer = set(module_function("features.py", "CUSTOMER_COLUMNS"))
    spec_outputs = {o.name for o in module_function("features.py", "CUSTOMER_SPEC").outputs}
    assert declared_features("customer_features") == customer and spec_outputs <= customer
