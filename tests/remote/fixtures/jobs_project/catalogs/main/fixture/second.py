"""Second task: depends on the first task's output through the graph."""

from bricksmith.tasks import task


@task(kind="probe", outputs="second_out", reads="first_out")
def second(ctx: object) -> None:
    """Print a marker line."""
    del ctx
    print("BRICKSMITH_FIXTURE second")  # noqa: T201
