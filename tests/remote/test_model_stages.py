"""Model development stages on the selected workspace: Spark equals the row engine; monitoring reads the predictions."""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

import dataclasses
import textwrap
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.graph import load_graph
from bricksmith.mlops.errors import DatasetError, DatasetValidationError
from bricksmith.mlops.featurestore import parse_feature_table
from bricksmith.mlops.stages import parse_development
from bricksmith.mlops.stages import rows as engine
from bricksmith.mlops.stages import spark as spark_engine
from bricksmith.monitor.model import parse_config
from bricksmith.monitor.model.engine import MemoryStore, check
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from pathlib import Path

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

MONTHS = [date(2024, m, d) for m, d in ((1, 31), (2, 29), (3, 31), (4, 30), (5, 31), (6, 30), (7, 31))]
POPULATION_DDL = "account_id BIGINT, closing_date DATE, status STRING, tenure INT"
LABEL_DDL = "account_id BIGINT, closing_date DATE, bad BOOLEAN"
FEATURE_DDL = "account_id BIGINT, closing_date DATE, balance DOUBLE"
FEATURES: dict[str, Any] = {
    "entity_keys": ["account_id"],
    "features": {"balance": {"description": "Closing balance", "type": "double", "owner": "team-a", "default": 0.0}},
}
DEVELOPMENT: dict[str, Any] = {
    "model": "churn",
    "entity_keys": ["account_id"],
    "as_of": "2024-12-31",
    "samples": {
        "dev": {"role": "train", "start": "2024-01-31", "end": "2024-04-30"},
        "holdout": {"role": "test", "start": "2024-03-31", "end": "2024-04-30"},
        "oot": {"role": "out_of_time", "start": "2024-05-31", "end": "2024-06-30"},
        "ttd": {"role": "through_the_door", "start": "2024-07-31", "end": "2024-07-31"},
    },
    "label": {"name": "bad", "horizon": 3, "definition": "any severe delinquency in the next three closings"},
    "split": {"seed": 7, "test_fraction": 0.3},
    "stratify": {"seed": 11, "fractions": {"0": 0.5}},
    "exclusions": [
        {"name": "closed", "where": {"column": "status", "op": "eq", "value": "closed"}},
        {"name": "young", "where": {"column": "tenure", "op": "lt", "value": 3}},
    ],
    "bands": [{"name": "A", "upper": 0.25}, {"name": "B", "upper": 0.6}, {"name": "C"}],
    "lookups": [{"table": "balances"}],
}
MONITORING: dict[str, Any] = {
    "model": "churn",
    "period": "closing_date",
    "score": "score",
    "grade": "grade",
    "sample": "sample",
    "references": {"development": {"sample": "dev"}, "out_of_time": {"sample": "oot"}},
    "model_version_column": "model_version",
    "min_rows": 10,
}


def population() -> list[dict[str, Any]]:
    return [
        {"account_id": a, "closing_date": c, "status": "closed" if a % 10 == 0 else "open", "tenure": (a % 7) + m}
        for a in range(1, 61)
        for m, c in enumerate(MONTHS)
    ]


def labels() -> list[dict[str, Any]]:
    return [{"account_id": r["account_id"], "closing_date": r["closing_date"], "bad": r["account_id"] % 3 == 0}
            for r in population()]  # fmt: skip


def features() -> list[dict[str, Any]]:
    return [{"account_id": a, "closing_date": c, "balance": float(a * 100 + i)}
            for a in range(1, 59) for i, c in enumerate(MONTHS)]  # fmt: skip


def frame(spark: Any, rows: list[dict[str, Any]], ddl: str) -> Any:  # noqa: ANN401
    names = [c.split()[0] for c in ddl.split(", ")]
    return spark.createDataFrame([tuple(r[n] for n in names) for r in rows], ddl)


def plain(rows: list[Any], keys: tuple[str, ...] = ("account_id", "closing_date")) -> dict[Any, dict[str, Any]]:
    found = {}
    for row in rows:
        data = row.asDict(recursive=True) if hasattr(row, "asDict") else dict(row)
        found[tuple(data[k] for k in keys)] = data
    return found


def scored(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**r, "score": (r["account_id"] * 37 % 40) / 40} for r in rows]


def test_spark_engine_equals_the_row_engine(remote_clients: Clients) -> None:
    spark = remote_clients.spark()
    dev = parse_development(DEVELOPMENT)
    definitions = {"balances": parse_feature_table(FEATURES)}
    pop = frame(spark, population(), POPULATION_DDL)
    lab = frame(spark, labels(), LABEL_DDL)
    frames = {"balances": frame(spark, features(), FEATURE_DDL)}

    cascaded = engine.apply_cascade(population(), dev)
    remote = spark_engine.apply_cascade(pop, dev)
    assert plain(remote.collect()) == plain(cascaded)
    assert sum(r["excluded"] for r in cascaded) > 0

    keys = ("closing_date", "rule")
    assert plain(spark_engine.composition(pop, dev).collect(), keys) == plain(engine.composition(cascaded, dev), keys)

    for name in ("dev", "holdout", "oot", "ttd"):
        expected = engine.select_sample(cascaded, dev, name)
        assert plain(spark_engine.select_sample(remote, dev, name).collect()) == plain(expected), name

    for name in ("dev", "holdout", "oot"):
        expected = engine.build_abt(population(), dev, name, definitions=definitions,
                                    data={"balances": features()}, labels=labels())  # fmt: skip
        built = spark_engine.build_abt(pop, dev, name, definitions=definitions, frames=frames, labels=lab)
        assert list(built.data.columns) == list(expected.data[0]), name
        assert plain(built.data.collect()) == plain(expected.data), name
        assert not any(r["excluded"] for r in expected.data)

    for name in ("oot", "ttd"):
        expected = engine.build_abt(population(), dev, name, purpose="scoring", definitions=definitions,
                                    data={"balances": features()})  # fmt: skip
        built = spark_engine.build_abt(pop, dev, name, purpose="scoring", definitions=definitions, frames=frames)
        assert plain(built.data.collect()) == plain(expected.data), name
        rows = engine.prediction_rows(scored(expected.data), dev, model_version=2, model_alias="champion")
        score = (spark_engine._f().col("account_id") * 37 % 40) / 40  # noqa: SLF001 - the same synthetic score
        predicted = spark_engine.prediction_frame(
            built.data.withColumn("score", score), dev, model_version=2, model_alias="champion"
        )
        assert list(predicted.columns) == list(rows[0])
        assert plain(predicted.collect()) == plain(rows), name

    late = dataclasses.replace(dev, as_of=date(2024, 8, 31))
    with pytest.raises(DatasetValidationError, match="matures after as_of 2024-08-31"):
        spark_engine.attach_labels(spark_engine.select_sample(pop, late, "oot"), late, lab)
    with pytest.raises(DatasetValidationError, match="repeat the key"):
        spark_engine.attach_labels(pop, dev, lab.unionByName(lab.limit(1)))
    with pytest.raises(DatasetError, match="not a monthly closing"):
        spark_engine.select_sample(frame(spark, [{**population()[0], "closing_date": date(2024, 1, 15)}],
                                         POPULATION_DDL), dev, "dev")  # fmt: skip


TASK = """
from bricksmith.mlops.featurestore import parse_feature_table
from bricksmith.mlops.stages import parse_development
from bricksmith.mlops.stages.spark import build_abt, prediction_frame
from bricksmith.tasks import task

POPULATION_DDL = POPULATION_DDL_VALUE
FEATURE_DDL = FEATURE_DDL_VALUE


@task(kind="table", outputs="churn_predictions", metadata={"write": "overwrite", "model_monitoring": MONITORING_VALUE})
def churn_predictions(ctx):
    spark = ctx.io.spark
    development = parse_development(DEVELOPMENT_VALUE)
    definitions = {"balances": parse_feature_table(FEATURES_VALUE)}
    population = spark.createDataFrame(POPULATION_VALUE, POPULATION_DDL)
    frames = {"balances": spark.createDataFrame(FEATURE_ROWS_VALUE, FEATURE_DDL)}
    out = None
    for sample in ("dev", "oot", "ttd"):
        abt = build_abt(population, development, sample, purpose="scoring", definitions=definitions, frames=frames)
        scored = abt.data.withColumn("score", (abt.data["account_id"] * 37 % 40) / 40)
        part = prediction_frame(scored, development, model_version=2)
        out = part if out is None else out.unionByName(part)
    ctx.write("churn_predictions", out)
"""


def test_a_task_writes_predictions_that_model_monitoring_reads(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    project = tmp_path / "project"
    path = project / "catalogs/demo/ml/predictions.py"
    path.parent.mkdir(parents=True)
    values = {
        "POPULATION_DDL_VALUE": POPULATION_DDL,
        "FEATURE_DDL_VALUE": FEATURE_DDL,
        "MONITORING_VALUE": MONITORING,
        "DEVELOPMENT_VALUE": DEVELOPMENT,
        "FEATURES_VALUE": FEATURES,
        "POPULATION_VALUE": [tuple(r.values()) for r in population()],
        "FEATURE_ROWS_VALUE": [tuple(r.values()) for r in features()],
    }
    text = textwrap.dedent(TASK)
    for name, value in values.items():
        text = text.replace(name, repr(value))
    path.write_text("import datetime\n" + text, encoding="utf-8")
    context = dataclasses.replace(remote_context, catalogs={"demo": catalog}, schemas={"ml": test_schema})
    graph = load_graph(project, context=context)
    plan = build_plan(graph)
    assert not plan.errors, plan.errors
    result = apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))
    assert result.ok, result.to_json()
    outcome = next(r for r in result.results if r.model_monitoring).model_monitoring[0]
    assert outcome["run_status"] == "success" and outcome["latest_period"] == "2024-07-31", outcome

    table = f"{catalog}.{test_schema}.churn_predictions"
    stored = spark.table(table)
    assert {r["sample"] for r in stored.select("sample").distinct().collect()} == {"dev", "oot", "ttd"}
    assert stored.where("excluded").count() > 0  # excluded rows are scored and flagged
    dev = parse_development(DEVELOPMENT)
    definitions = {"balances": parse_feature_table(FEATURES)}
    rows = []
    for sample in ("dev", "oot", "ttd"):
        abt = engine.build_abt(population(), dev, sample, purpose="scoring", definitions=definitions,
                               data={"balances": features()})  # fmt: skip
        rows += engine.prediction_rows(scored(abt.data), dev, model_version=2)
    config = parse_config(MONITORING)
    pure = check(rows, config, MemoryStore(), run_id="r", now="t")
    remote = check(stored, config, MemoryStore(), run_id="r", now="t")
    assert remote.outcome.to_dict() == pure.outcome.to_dict()
    composition = spark_engine.composition(stored, dev, by=["sample"])
    assert composition.count() == len(engine.composition(rows, dev, by=["sample"]))
