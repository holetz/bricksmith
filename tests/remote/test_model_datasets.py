"""Model datasets on the selected workspace: two model identities in one schema built through the runner."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.graph import load_graph
from bricksmith.mlops import DatasetValidationError, load_spec_file
from bricksmith.mlops.spark import build_population, validate_frame
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "shared_schema"


def rows(spark: Any, name: str, order: str) -> list[dict[str, Any]]:  # noqa: ANN401
    return [r.asDict() for r in spark.table(name).orderBy(*order.split(",")).collect()]


def test_two_models_in_one_schema_build_through_the_runner(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE, project, ignore=shutil.ignore_patterns("__pycache__"))
    context = dataclasses.replace(remote_context, catalogs={"demo": catalog}, schemas={"ml": test_schema})
    graph = load_graph(project, context=context)
    assert graph.ok
    plan = build_plan(graph)
    assert not plan.errors
    report = apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))
    assert report.ok, report.to_json()
    assert len(report.ids("succeeded")) == 13

    def t(name: str) -> str:
        return f"{catalog}.{test_schema}.{name}"

    # Churn: customers 2 and 6 are minors, 3 is inactive; each in both snapshots (reason order decides 6).
    excluded = rows(spark, t("churn_exclusions"), "customer_id,snapshot_date")
    assert [(r["customer_id"], str(r["snapshot_date"]), r["exclusion_reason"]) for r in excluded] == [
        (2, "2026-01-31", "minor"), (2, "2026-03-31", "minor"),
        (3, "2026-01-31", "inactive"), (3, "2026-03-31", "inactive"),
        (6, "2026-01-31", "minor"), (6, "2026-03-31", "minor"),
    ]  # fmt: skip
    assert spark.table(t("churn_population")).count() == 10
    training = rows(spark, t("churn_training"), "customer_id,snapshot_date")
    assert (
        len(training) == 10
        and sum(r["split"] == "train" for r in training) == 5
        and sum(r["churned"] for r in training) == 4
    )
    assert spark.table(t("churn_prediction")).count() == 5  # snapshot 2026-03-31, five admitted customers
    # Upsell: seeded sample keeps customers 3, 4, 7, 8; customer 4 has low tenure.
    up_excluded = rows(spark, t("upsell_exclusions"), "snapshot_date")
    assert [(r["customer_id"], r["exclusion_reason"]) for r in up_excluded] == [(4, "low_tenure")] * 2
    up_training = rows(spark, t("upsell_training"), "customer_id,snapshot_date")
    assert sorted({r["customer_id"] for r in up_training}) == [3, 7, 8] and len(up_training) == 6
    assert {r["customer_id"]: r["segment"] for r in up_training} == {3: "pro", 7: "basic", 8: "pro"}
    # Provenance: one row per model, with fingerprint and pinned input versions.
    for model, short in (("churn_v1", "churn"), ("upsell_v1", "upsell")):
        (prov,) = rows(spark, t(f"{short}_provenance"), "model")
        assert prov["model"] == model and prov["dataset"] == "training" and len(prov["fingerprint"]) == 64
        inputs = json.loads(prov["inputs"])
        assert (
            len(inputs) == 3 and all(isinstance(v, int) for v in inputs.values()) and json.loads(prov["unpinned"]) == []
        )
        assert json.loads(prov["counts"]) == {"rows": 10 if short == "churn" else 6}
    # Spark and the pure reference sample identically (hash parity).
    spec = load_spec_file(project / "models/upsell_v1.toml")
    population, _ = build_population(spark.table(t("customer_features")), spec)
    assert sorted({r["customer_id"] for r in population.collect()}) == [3, 7, 8]
    # Immature labels are diagnosed on the Spark training dataset before fitting.
    immature = dataclasses.replace(load_spec_file(project / "models/churn_v1.toml"), reference_date=date(2026, 4, 15))
    verdict = validate_frame(spark.table(t("churn_training")), immature)
    assert verdict.codes() == ["immature_label", "label_after_reference"]
    assert next(f for f in verdict.findings if f.code == "immature_label").count == 5
    with pytest.raises(DatasetValidationError):
        verdict.require()
    assert validate_frame(spark.table(t("churn_training")), load_spec_file(project / "models/churn_v1.toml")).ok
