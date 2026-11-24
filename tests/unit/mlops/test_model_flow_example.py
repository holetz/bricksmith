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


TASK_OF = {
    "source.py": "accounts_monthly",
    "features.py": "account_features",
    "development.py": "attrition_population",
}


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


DEVELOPMENT_TASKS = (
    "attrition_population",
    "attrition_labels",
    "attrition_training",
    "attrition_selection_ledger",
    "attrition_selection_variables",
)


def test_the_development_tasks_plan_and_training_binds_both_feature_tables() -> None:
    plan = build_plan(load_graph(ROOT))
    assert not plan.errors, plan.errors
    by_task = {entry.task_id: entry for entry in plan.entries}
    assert {f"demo.flow.{t}" for t in DEVELOPMENT_TASKS} <= set(by_task)
    reads = {binding.reference for binding in by_task["demo.flow.attrition_training"].reads}
    assert {"account_features", "customer_features", "attrition_population", "attrition_labels"} <= reads
    from bricksmith.mlops.selection import write_options  # noqa: PLC0415

    for kind in ("ledger", "variables"):
        declared = declaration_of(module_function("development.py", f"attrition_selection_{kind}"))
        assert declared is not None and dict(declared.metadata) == write_options(kind)


def test_the_development_declaration_parses() -> None:
    declared = module_function("development.py", "development")()
    assert declared.model == "attrition" and declared.label is not None and declared.label.horizon == 3
    assert [s.name for s in declared.samples] == ["dev", "oot", "live"]
    assert [r.name for r in declared.exclusions] == ["closed", "new_account"]
    assert [b.name for b in declared.bands] == ["A", "B", "C", "D"]
    assert [(lk.table, lk.offset) for lk in declared.lookups] == [
        ("account_features", 0),
        ("account_features", 3),
        ("customer_features", 0),
    ]


def history() -> list[dict[str, Any]]:
    names = [c.split()[0] for c in module_function("source.py", "DDL").split(", ")]
    return [dict(zip(names, r, strict=True)) for r in module_function("source.py", "source_rows")()]


def label_rows(rows: list[dict[str, Any]], horizon: int, as_of: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """The labels task's rule on rows: the account closes after the closing and within ``horizon`` closings."""
    days = module_function("source.py", "closings")()
    first_closed: dict[int, Any] = {}
    for r in sorted(rows, key=lambda r: r["closing_date"]):
        if r["closed"]:
            first_closed.setdefault(r["account_id"], r["closing_date"])
    out = []
    for r in rows:
        index = days.index(r["closing_date"])
        if index + horizon >= len(days) or days[index + horizon] > as_of:
            continue
        closed_at = first_closed.get(r["account_id"])
        attrited = closed_at is not None and r["closing_date"] < closed_at <= days[index + horizon]
        out.append({"account_id": r["account_id"], "closing_date": r["closing_date"], "attrited": int(attrited)})
    return out


def test_the_development_chain_runs_on_the_row_engines() -> None:
    pytest.importorskip("numpy")
    from bricksmith.features import compute_rows  # noqa: PLC0415 - numpy only when the test runs
    from bricksmith.mlops.selection import select_features  # noqa: PLC0415
    from bricksmith.mlops.stages.rows import apply_cascade, build_abt  # noqa: PLC0415

    declared = module_function("development.py", "development")()
    rows = history()
    columns = module_function("development.py", "POPULATION_COLUMNS")
    population = apply_cascade([{c: r[c] for c in columns} for r in rows], declared)
    assert any(r["excluded"] for r in population) and not all(r["excluded"] for r in population)
    labels = label_rows(rows, declared.label.horizon, declared.as_of)
    assert {r["attrited"] for r in labels} == {0, 1}

    account = compute_rows([{k: v for k, v in r.items() if k != "customer_id"} for r in rows],
                           module_function("features.py", "ACCOUNT_SPEC"))  # fmt: skip
    segments = {(r["account_id"], r["closing_date"]): r["segment"] for r in rows}
    account = [{**r, "segment": segments[r["account_id"], r["closing_date"]]} for r in account]
    totals: dict[tuple[int, Any], dict[str, Any]] = {}
    for r in rows:
        item = totals.setdefault((r["customer_id"], r["closing_date"]), {
            "customer_id": r["customer_id"], "closing_date": r["closing_date"], "customer_balance": 0.0,
            "customer_past_due": 0.0, "open_accounts": 0.0,
        })  # fmt: skip
        item["customer_balance"] += r["balance"]
        item["customer_past_due"] = max(item["customer_past_due"], r["days_past_due"])
        item["open_accounts"] += 0.0 if r["closed"] else 1.0
    customer = compute_rows(list(totals.values()), module_function("features.py", "CUSTOMER_SPEC"))
    opened = {k: v["open_accounts"] for k, v in totals.items()}
    customer = [{**r, "open_accounts": opened[r["customer_id"], r["closing_date"]]} for r in customer]

    definitions = {}
    for task in ("account_features", "customer_features"):
        declared_task = declaration_of(module_function("features.py", task))
        assert declared_task is not None
        definitions[task] = feature_table_of(declared_task.metadata)
    data = {"account_features": account, "customer_features": customer}
    parts = [
        build_abt(population, declared, sample, definitions=definitions, data=data, labels=labels)
        for sample in ("dev", "oot")
    ]
    abt = parts[0].data + parts[1].data
    candidates = set(module_function("development.py", "CANDIDATES"))
    assert candidates <= set(abt[0]) and {"account_features", "customer_features"} <= set(parts[0].reads)
    assert not any(r["excluded"] for r in abt)

    result = select_features(abt, module_function("development.py", "SELECTION"))
    assert result.selected and set(result.selected) <= candidates
    assert {v.variable for v in result.variables} == candidates
