"""Data stability declared on the task that writes a table (same graph, same runner, no separate monitor task).

``sales_monthly`` is a synthetic monthly table. Its ``stability`` declaration makes the runner profile each
month after the write, compare it with the fixed January-March baseline, store the results in the
``stability_*`` tables of this schema and fail the task (blocking ``sales_report``) when a result is red.
Run with the parameter ``stability_drift=yes`` to shift the last month and see the gate fail.
"""

from bricksmith.tasks import task

MONTHS = ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06")
CHANNELS = ("web", "web", "shop", "app")


def sales_rows(drift):
    """Return identical months (so PSI is exactly 0), except a shifted last month when ``drift`` is set."""
    rows = []
    for m, month in enumerate(MONTHS):
        shifted = drift and m == len(MONTHS) - 1
        for i in range(160):
            amount = 0.0 if i % 5 == 0 else float(10 + (i * 7) % 90) + (60.0 if shifted else 0.0)
            channel = "app" if shifted and i % 2 else CHANNELS[i % len(CHANNELS)]
            rows.append((m * 1000 + i, month, channel, amount, i % 3 == 0))
    return rows


@task(
    kind="table",
    metadata={
        "write": "overwrite",
        "stability": {
            "period": "month",
            "exclude": ["sale_id"],
            "segments": ["channel"],
            "baseline": {"kind": "fixed", "start": "2026-01", "end": "2026-03"},
            "limits": {"volume_change": {"green": 0.1, "red": 0.3, "direction": "both"}},
            "persistence": {"yellow": 2, "red": 1},
            "gate": {"fail_on": ["red"]},
        },
    },
)
def sales_monthly(ctx):
    """Synthetic monthly sales: amount (with exact zeros), channel and a promotion flag."""
    drift = ctx.execution.parameters.get("stability_drift") == "yes"
    ddl = "sale_id INT, month STRING, channel STRING, amount DOUBLE, promoted BOOLEAN"
    ctx.write("sales_monthly", ctx.io.spark.createDataFrame(sales_rows(drift), ddl))


@task(kind="table", metadata={"write": "overwrite"})
def sales_report(ctx):
    """A downstream table: it never runs on data whose stability gate failed."""
    sales = ctx.read("sales_monthly")
    ctx.write("sales_report", sales.groupBy("month").count())
