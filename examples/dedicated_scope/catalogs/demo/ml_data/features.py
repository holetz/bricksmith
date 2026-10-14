from pyspark.sql import functions as f

from bricksmith.tasks import task


@task(kind="feature", metadata={"write": "overwrite"})
def customer_features(ctx):
    """Raw columns plus the time each feature value was observed (here: the snapshot date)."""
    snapshots = ctx.read("customer_snapshots")
    ctx.write("customer_features", snapshots.withColumn("feature_time", f.col("snapshot_date")))
