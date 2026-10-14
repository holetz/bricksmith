# Tasks and the dependency graph

A task is a Python function with a `@task` declaration, in a file under
`catalogs/<catalog>/<schema>/`. The two folders give the task its logical scope; anything below
them is free-form organization. Feature, training, prediction and monitoring tasks use the same
declaration and may share one schema.

## Declare a task

```python
# catalogs/main/sales/models/churn/predict.py
from bricksmith.tasks import task


@task(
    kind="predict",  # behaviour label; never decides location
    name="predict_churn",  # defaults to the function name
    outputs="churn_scores",  # defaults to the task name
    reads="churn_runs",  # another task's output, same scope
    external="vendor.public.rates",  # produced outside the project
    state="churn_scores",  # an earlier window of an output
    metadata={"write": "merge", "keys": ["customer_id"]},
)
def run(ctx):
    features = ctx.read("customer_features")  # inferred: no need to declare it
    previous = ctx.read_state("churn_scores")
    ...
```

Declaration values must be literals, because discovery parses the file and never imports it.

## References

| Form | Resolves to |
|---|---|
| `name` | an output in the task's own catalog and schema |
| `schema.name` | an output in another schema of the same catalog |
| `catalog.schema.name` | an output anywhere in the project |

A reference names an output, not a task. A short name never matches an output of another scope;
qualify it instead.

## What discovery infers

Literal calls to `ctx.read`, `ctx.read_external` and `ctx.read_state` on the task's first
parameter are picked up, including inside helpers that receive the context when they are defined
in the same module or imported relatively (`from .helpers import load`). Everything else must be
declared:

- A computed name (`ctx.read(f"totals_{region}")`) is reported as `dynamic_read`. It is an error
  until the task declares its reads.
- A context passed to a function discovery cannot follow is reported as `unfollowed_helper`.

## Variants

```python
@task(kind="table", outputs="totals_{region}", variants=[{"region": "north"}, {"region": "south"}])
def totals(ctx): ...
```

This yields `main.sales.totals[region=north]` and `main.sales.totals[region=south]`, each owning
its own output.

## Project file

```toml
# bricksmith.toml
[tasks]
root = "catalogs"
exclude = ["*/drafts/*"]

[mappings.catalogs]
main = "dev_main"

[mappings.schemas]
sales = "sales_dev"

[[exemptions]]
rule = "missing_producer"
subject = "main.sales.report -> *"
reason = "producer lands with the next release"
expires = 2027-01-31
```

Mappings turn logical names into physical ones; they never change task identities. An exemption
needs an expiry date, and an expired exemption fails validation.

## Inspect and validate

```bash
bricksmith tasks list --project .
bricksmith tasks graph --project . --json    # the complete graph, stable JSON
bricksmith tasks validate --project .        # exit code 4 when the graph has errors
```

In Python, `bricksmith.graph.load_graph(".")` returns the same `TaskGraph`. It keeps every task,
output, edge and diagnostic even when the project is invalid, and `order()` gives a deterministic
run order. See the [specification](../specs/bs-02-discovery.md) for the diagnostic codes.
