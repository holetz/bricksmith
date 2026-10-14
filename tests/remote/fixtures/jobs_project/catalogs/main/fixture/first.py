"""First task of the ordered job: records that it ran, touches no storage."""

import time

from bricksmith.tasks import task


@task(kind="probe", outputs="first_out")
def first(ctx: object) -> None:
    """Print a marker line."""
    del ctx
    time.sleep(5)
    print("BRICKSMITH_FIXTURE first")  # noqa: T201
