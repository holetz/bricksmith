from bricksmith.tasks import task

ROWS = [  # customer_id, age, active, tenure_months, spend, plan, churned, upsold
    (1, 34, True, 12, 50.0, "basic", 0, 0),
    (2, 17, True, 3, 20.0, "basic", 0, 0),
    (3, 45, False, 40, 150.0, "pro", 1, 0),
    (4, 29, True, 8, 120.0, "pro", 1, 1),
    (5, 61, True, 60, 300.0, "pro", 0, 1),
    (6, 16, False, 2, 10.0, "basic", 0, 0),
    (7, 38, True, 24, 99.0, "basic", 0, 1),
    (8, 52, True, 18, 100.0, "pro", 1, 0),
]
SNAPSHOTS = ("2026-01-31", "2026-03-31")
DDL = (
    "customer_id INT, snapshot_date STRING, age INT, active BOOLEAN, tenure_months INT, spend DOUBLE, "
    "plan STRING, churned INT, upsold INT"
)


@task(kind="table", metadata={"write": "overwrite"})
def customer_snapshots(ctx):
    """Synthetic raw snapshots: two dates for each of eight customers."""
    rows = [(r[0], day, *r[1:]) for day in SNAPSHOTS for r in ROWS]
    ctx.write("customer_snapshots", ctx.io.spark.createDataFrame(rows, DDL))
