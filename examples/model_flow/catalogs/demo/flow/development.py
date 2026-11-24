"""Development stages of the attrition model: population, labels, the training ABT and feature selection.

The declaration lives in ``models/attrition.toml``. The population is the spine: every account and closing, with the
exclusion cascade flagged (excluded rows stay, only the training ABT drops them). The training ABT joins both feature
tables by exact closing for the labelled samples and records the Delta version of each table it read.
"""

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

from bricksmith.mlops.featurestore import feature_table_of
from bricksmith.mlops.stages import load_development_file
from bricksmith.tasks import declaration_of, task

from .features import account_features, customer_features

FEATURE_TASKS = (account_features, customer_features)
CANDIDATES = (
    "balance_mean_3p", "balance_mean_6p", "balance_slope_3p", "balance_slope_6p", "payments_mean_3p",
    "payments_mean_6p", "days_past_due_max_3p", "days_past_due_max_6p", "payment_ratio_3p", "status_count_late_6p",
    "segment", "prev3_balance_mean_3p", "prev3_days_past_due_max_3p", "customer_balance_mean_6p",
    "customer_balance_slope_6p", "customer_past_due_max_6p", "open_accounts",
)  # fmt: skip
SELECTION = {
    "name": "attrition_selection",
    "closing": "closing_date",
    "target": "attrited",
    "features": list(CANDIDATES),
    "development": ["2024-07-31", "2024-12-31"],
    "out_of_time": ["2025-02-28", "2025-04-30"],
    "expected_signs": {"days_past_due_max_3p": "+"},
}
POPULATION_COLUMNS = ("account_id", "customer_id", "closing_date", "closed", "tenure_months")


def development():
    """Return the parsed development declaration of the project."""
    root = Path(__file__).resolve().parents[3]
    return load_development_file(root / "models" / "attrition.toml")


def feature_inputs(ctx):
    """Return the development bound to this run's physical feature tables, and their declarations."""
    physical = {binding.reference: binding.physical for binding in ctx.entry.reads}
    definitions = {}
    for function in FEATURE_TASKS:
        declared = declaration_of(function)
        definitions[physical[declared.name]] = feature_table_of(declared.metadata)
    declared = development()
    lookups = tuple(dataclasses.replace(lookup, table=physical[lookup.table]) for lookup in declared.lookups)
    return dataclasses.replace(declared, lookups=lookups), definitions


@task(kind="population", outputs="attrition_population", metadata={"write": "overwrite"})
def attrition_population(ctx):
    """The spine: every account and closing, flagged by the exclusion cascade."""
    from bricksmith.mlops.stages.spark import apply_cascade  # noqa: PLC0415 - Spark loads only when the task runs

    history = ctx.read("accounts_monthly").select(*POPULATION_COLUMNS)
    ctx.write("attrition_population", apply_cascade(history, development()))


@task(kind="labels", outputs="attrition_labels", metadata={"write": "overwrite"})
def attrition_labels(ctx):
    """One label per account and closing whose three-closing horizon is observed: does the account close in it?"""
    from pyspark.sql import functions as f  # noqa: PLC0415 - Spark loads only when the task runs

    history = ctx.read("accounts_monthly")
    first_closed = history.where("closed").groupBy("account_id").agg(f.min("closing_date").alias("closed_at"))
    horizon_end = f.last_day(f.add_months("closing_date", development().label.horizon))
    labels = (
        history.select("account_id", "closing_date")
        .join(first_closed, "account_id", "left")
        .where(horizon_end <= f.lit(development().as_of))
        .select(
            "account_id",
            "closing_date",
            (f.col("closed_at").isNotNull() & (f.col("closed_at") > f.col("closing_date")) & (f.col("closed_at") <= horizon_end))
            .cast("int")
            .alias("attrited"),
        )
    )  # fmt: skip
    ctx.write("attrition_labels", labels)


@task(
    kind="training_dataset",
    outputs="attrition_training",
    reads=["account_features", "customer_features"],
    metadata={"write": "overwrite"},
)
def attrition_training(ctx):
    """The training ABT of the labelled samples, with the Delta version of every feature table it read."""
    from pyspark.sql import functions as f  # noqa: PLC0415 - Spark loads only when the task runs

    from bricksmith.mlops.stages.spark import build_abt  # noqa: PLC0415

    bound, definitions = feature_inputs(ctx)
    population, labels = ctx.read("attrition_population"), ctx.read("attrition_labels")
    spark = ctx.io.spark
    parts = [
        build_abt(population, bound, s, definitions=definitions, spark=spark, labels=labels) for s in ("dev", "oot")
    ]
    reads = json.dumps({table: version for part in parts for table, version in part.reads.items()}, sort_keys=True)
    abt = parts[0].data.unionByName(parts[1].data)
    ctx.write("attrition_training", abt.withColumn("feature_reads", f.lit(reads)))


@task(
    kind="table",
    outputs="attrition_selection_ledger",
    metadata={
        "write": "merge",
        "keys": ["selection", "run_id", "variable", "step", "metric"],
        "on_empty": "allow_empty",
    },
)
def attrition_selection_ledger(ctx):
    """Run the selection funnel on the training ABT and store why every candidate was kept or dropped."""
    from bricksmith.mlops.selection import select_features, table_rows, to_frame  # noqa: PLC0415

    training = ctx.read("attrition_training")
    result = select_features(training, SELECTION)
    rows = table_rows(result, "ledger", run_id=development().as_of.isoformat(), now=datetime.now(UTC).isoformat())
    ctx.write("attrition_selection_ledger", to_frame(ctx.io.spark, "ledger", rows))
