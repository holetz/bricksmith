"""Model tasks for churn_v1: train, evaluate and predict live next to its dataset tasks in the same schema.

Evaluate and predict pin an explicit alias; nothing resolves "latest" and nothing retrains on a missing model.
Registering a run and moving the alias are deliberate steps: `bricksmith model register|promote`.
"""

from bricksmith.mlops.tasks import evaluate_task, predict_task, train_task
from bricksmith.tasks import task


@task(
    kind="train",
    outputs="churn_training_run",
    metadata={
        "write": "overwrite",
        "model_task": {
            "model": "churn_v1",
            "role": "train",
            "spec": "models/churn_v1.toml",
            "params": {"C": 1.0, "max_iter": 200, "random_state": 0},
            "card": {
                "summary": "Synthetic logistic-regression churn classifier on tenure and spend.",
                "intended_use": "Demonstration of the model lifecycle; not for decisions about real customers.",
                "limitations": "Trained on a handful of synthetic rows; scores carry no real-world meaning.",
                "owner": "example-team",
            },
        },
    },
)
def churn_train(ctx):
    train_task(ctx, churn_train, ctx.read("churn_training"), ctx.read("churn_provenance"), output="churn_training_run")


@task(
    kind="evaluate",
    outputs="churn_evaluation",
    metadata={
        "write": "overwrite",
        "model_task": {"model": "churn_v1", "role": "evaluate", "spec": "models/churn_v1.toml", "alias": "champion"},
    },
)
def churn_evaluate(ctx):
    evaluate_task(ctx, churn_evaluate, ctx.read("churn_training"), output="churn_evaluation")


@task(
    kind="predict",
    outputs="churn_scores",
    metadata={
        "write": "overwrite",
        "model_task": {"model": "churn_v1", "role": "predict", "spec": "models/churn_v1.toml", "alias": "champion"},
    },
)
def churn_predict(ctx):
    predict_task(ctx, churn_predict, ctx.read("churn_prediction"), output="churn_scores")
