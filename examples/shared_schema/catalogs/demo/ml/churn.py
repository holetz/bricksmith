from bricksmith.mlops import dataset_spec
from bricksmith.mlops.spark import (
    build_features,
    build_labels,
    build_population,
    build_prediction,
    build_training,
    latest_snapshot,
    provenance_frame,
    resolve_fingerprint,
    validate_frame,
)
from bricksmith.tasks import task


@task(
    kind="population",
    outputs=["churn_population", "churn_exclusions"],
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "churn_v1", "role": "population", "spec": "models/churn_v1.toml"},
    },
)
def churn_population(ctx):
    spec = dataset_spec(churn_population)
    population, exclusions = build_population(ctx.read("customer_features"), spec)
    ctx.write("churn_population", population)
    ctx.write("churn_exclusions", exclusions)


@task(
    kind="features",
    outputs="churn_features",
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "churn_v1", "role": "features", "spec": "models/churn_v1.toml"},
    },
)
def churn_features(ctx):
    ctx.write("churn_features", build_features(ctx.read("customer_features"), dataset_spec(churn_features)))


@task(
    kind="labels",
    outputs="churn_labels",
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "churn_v1", "role": "labels", "spec": "models/churn_v1.toml"},
    },
)
def churn_labels(ctx):
    ctx.write("churn_labels", build_labels(ctx.read("customer_features"), dataset_spec(churn_labels)))


@task(
    kind="training_dataset",
    outputs=["churn_training", "churn_provenance"],
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "churn_v1", "role": "training", "spec": "models/churn_v1.toml"},
    },
)
def churn_training(ctx):
    spec = dataset_spec(churn_training)
    training = build_training(ctx.read("churn_population"), ctx.read("churn_features"), ctx.read("churn_labels"), spec)
    validate_frame(training, spec).require()  # leaks and immature labels stop the run before any fitting
    stamp = resolve_fingerprint(ctx, spec, "training")
    ctx.write("churn_training", training)
    ctx.write("churn_provenance", provenance_frame(ctx.io.spark, spec, "training", stamp, {"rows": training.count()}))


@task(
    kind="prediction_dataset",
    outputs="churn_prediction",
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "churn_v1", "role": "prediction", "spec": "models/churn_v1.toml"},
    },
)
def churn_prediction(ctx):
    spec = dataset_spec(churn_prediction)
    population = ctx.read("churn_population")
    ctx.write(
        "churn_prediction",
        build_prediction(population, ctx.read("churn_features"), spec, latest_snapshot(population, spec)),
    )
