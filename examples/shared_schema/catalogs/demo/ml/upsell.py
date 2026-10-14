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
    outputs=["upsell_population", "upsell_exclusions"],
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "upsell_v1", "role": "population", "spec": "models/upsell_v1.toml"},
    },
)
def upsell_population(ctx):
    spec = dataset_spec(upsell_population)
    population, exclusions = build_population(ctx.read("customer_features"), spec)
    ctx.write("upsell_population", population)
    ctx.write("upsell_exclusions", exclusions)


@task(
    kind="features",
    outputs="upsell_features",
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "upsell_v1", "role": "features", "spec": "models/upsell_v1.toml"},
    },
)
def upsell_features(ctx):
    ctx.write("upsell_features", build_features(ctx.read("customer_features"), dataset_spec(upsell_features)))


@task(
    kind="labels",
    outputs="upsell_labels",
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "upsell_v1", "role": "labels", "spec": "models/upsell_v1.toml"},
    },
)
def upsell_labels(ctx):
    ctx.write("upsell_labels", build_labels(ctx.read("customer_features"), dataset_spec(upsell_labels)))


@task(
    kind="training_dataset",
    outputs=["upsell_training", "upsell_provenance"],
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "upsell_v1", "role": "training", "spec": "models/upsell_v1.toml"},
    },
)
def upsell_training(ctx):
    spec = dataset_spec(upsell_training)
    training = build_training(
        ctx.read("upsell_population"), ctx.read("upsell_features"), ctx.read("upsell_labels"), spec
    )
    validate_frame(training, spec).require()  # leaks and immature labels stop the run before any fitting
    stamp = resolve_fingerprint(ctx, spec, "training")
    ctx.write("upsell_training", training)
    ctx.write("upsell_provenance", provenance_frame(ctx.io.spark, spec, "training", stamp, {"rows": training.count()}))


@task(
    kind="prediction_dataset",
    outputs="upsell_prediction",
    metadata={
        "write": "overwrite",
        "model_dataset": {"model": "upsell_v1", "role": "prediction", "spec": "models/upsell_v1.toml"},
    },
)
def upsell_prediction(ctx):
    spec = dataset_spec(upsell_prediction)
    population = ctx.read("upsell_population")
    ctx.write(
        "upsell_prediction",
        build_prediction(population, ctx.read("upsell_features"), spec, latest_snapshot(population, spec)),
    )
