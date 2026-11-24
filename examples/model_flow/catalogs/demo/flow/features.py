"""Two closing-based feature tables at two entity grains, with windowed features and data stability.

``account_features`` holds one row per account and closing; ``customer_features`` one row per customer and closing,
from the accounts the customer holds. Both compute their windowed aggregates with ``compute_features`` under
``engine="auto"``: the JVM kernel, or native Spark when the compute refuses the kernel. The run report records which
engine ran and why. Set the task parameter ``kernel_function`` to an installed Unity Catalog kernel function (see the
feature engineering guide) to let serverless compute use the kernel.

Both tables declare ``stability``: after each write the runner profiles every closing against the development
closings and stores the results in the ``stability_*`` tables of this schema (warn only).
"""

from bricksmith.features import CategoricalCount, FeatureSpec, Ratio, WindowFeature, compute_features
from bricksmith.tasks import task

ACCOUNT_SPEC = FeatureSpec(
    keys=("account_id",),
    closing="closing_date",
    features=(
        WindowFeature(("balance",), (3, 6), ("mean", "slope")),
        WindowFeature(("payments",), (3, 6), ("mean",)),
        WindowFeature(("days_past_due",), (3, 6), ("max",)),
    ),
    ratios=(Ratio("payment_ratio_3p", "payments_mean_3p", "balance_mean_3p"),),
    categorical=(CategoricalCount("status", ("late",), (6,)),),
)
CUSTOMER_SPEC = FeatureSpec(
    keys=("customer_id",),
    closing="closing_date",
    features=(
        WindowFeature(("customer_balance",), (6,), ("mean", "slope")),
        WindowFeature(("customer_past_due",), (6,), ("max",)),
    ),
)
ACCOUNT_COLUMNS = (
    "balance_mean_3p", "balance_mean_6p", "balance_slope_3p", "balance_slope_6p", "payments_mean_3p",
    "payments_mean_6p", "days_past_due_max_3p", "days_past_due_max_6p", "payment_ratio_3p", "status_count_late_6p",
)  # fmt: skip
CUSTOMER_COLUMNS = (
    "customer_balance_mean_6p",
    "customer_balance_slope_6p",
    "customer_past_due_max_6p",
    "open_accounts",
)


def windowed(frame, spec, kernel_function):
    """Compute ``spec`` on Spark with the engine ``auto`` resolves to; the choice lands in the run report."""
    return compute_features(frame, spec, kernel_function=kernel_function or None)


def kernel(ctx):
    """Return the installed kernel function named by the task parameter, or None."""
    return ctx.execution.parameters.get("kernel_function")


@task(
    kind="features",
    outputs="account_features",
    metadata={
        "feature_table": {
            "entity_keys": ["account_id"],
            "closing": "closing_date",
            "frequency": "monthly",
            "features": {
                "balance_mean_3p": {"description": "Mean balance over three closings", "type": "double", "owner": "example-team"},
                "balance_mean_6p": {"description": "Mean balance over six closings", "type": "double", "owner": "example-team"},
                "balance_slope_3p": {"description": "Balance trend over three closings", "type": "double", "owner": "example-team"},
                "balance_slope_6p": {"description": "Balance trend over six closings", "type": "double", "owner": "example-team"},
                "payments_mean_3p": {"description": "Mean payment over three closings", "type": "double", "owner": "example-team"},
                "payments_mean_6p": {"description": "Mean payment over six closings", "type": "double", "owner": "example-team"},
                "days_past_due_max_3p": {"description": "Worst days past due over three closings", "type": "double", "owner": "example-team", "default": 0.0},
                "days_past_due_max_6p": {"description": "Worst days past due over six closings", "type": "double", "owner": "example-team", "default": 0.0},
                "payment_ratio_3p": {"description": "Mean payment over mean balance, three closings", "type": "double", "owner": "example-team"},
                "status_count_late_6p": {"description": "Closings in late status among the last six", "type": "double", "owner": "example-team", "default": 0.0},
                "segment": {"description": "Account segment", "type": "string", "owner": "example-team", "default": "unknown"},
            },
        },
        "stability": {
            "period": "closing_date",
            "exclude": ["account_id"],
            "baseline": {"kind": "fixed", "start": "2024-07-31", "end": "2024-12-31"},
        },
    },
)  # fmt: skip
def account_features(ctx):
    """Windowed account features, stored at the closing they describe (available at that closing)."""
    from pyspark.sql import functions as f  # noqa: PLC0415 - Spark loads only when the task runs

    history = ctx.read("accounts_monthly")
    computed = windowed(history, ACCOUNT_SPEC, kernel(ctx))
    columns = [f.col(c).cast("double").alias(c) for c in ACCOUNT_COLUMNS]
    out = computed.join(history.select("account_id", "closing_date", "segment"), ["account_id", "closing_date"])
    ctx.write("account_features", out.select("account_id", f.col("closing_date").cast("date"), *columns, "segment"))


@task(
    kind="features",
    outputs="customer_features",
    metadata={
        "feature_table": {
            "entity_keys": ["customer_id"],
            "closing": "closing_date",
            "frequency": "monthly",
            "features": {
                "customer_balance_mean_6p": {"description": "Mean total balance of the customer over six closings", "type": "double", "owner": "example-team"},
                "customer_balance_slope_6p": {"description": "Trend of the customer's total balance over six closings", "type": "double", "owner": "example-team"},
                "customer_past_due_max_6p": {"description": "Worst days past due of any account of the customer over six closings", "type": "double", "owner": "example-team", "default": 0.0},
                "open_accounts": {"description": "Open accounts of the customer at the closing", "type": "double", "owner": "example-team", "default": 0.0},
            },
        },
        "stability": {
            "period": "closing_date",
            "exclude": ["customer_id"],
            "baseline": {"kind": "fixed", "start": "2024-07-31", "end": "2024-12-31"},
        },
    },
)  # fmt: skip
def customer_features(ctx):
    """Windowed customer features over the accounts each customer holds at each closing."""
    from pyspark.sql import functions as f  # noqa: PLC0415 - Spark loads only when the task runs

    totals = (
        ctx.read("accounts_monthly")
        .groupBy("customer_id", "closing_date")
        .agg(
            f.sum("balance").alias("customer_balance"),
            f.max("days_past_due").alias("customer_past_due"),
            f.sum(f.when(~f.col("closed"), 1).otherwise(0)).cast("double").alias("open_accounts"),
        )
    )
    computed = windowed(totals, CUSTOMER_SPEC, kernel(ctx))
    out = computed.join(totals.select("customer_id", "closing_date", "open_accounts"), ["customer_id", "closing_date"])
    columns = [f.col(c).cast("double").alias(c) for c in CUSTOMER_COLUMNS]
    ctx.write("customer_features", out.select("customer_id", f.col("closing_date").cast("date"), *columns))
