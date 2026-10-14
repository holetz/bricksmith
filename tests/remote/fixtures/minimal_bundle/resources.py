"""Python-defined bundle resources: one serverless job running a trivial script."""

from databricks.bundles.core import Bundle, Resources
from databricks.bundles.jobs import Environment, Job, JobEnvironment, SparkPythonTask, Task


def load_resources(bundle: Bundle) -> Resources:  # noqa: ARG001 - signature fixed by databricks-bundles
    """Return the fixture job."""
    job = Job(
        name="bricksmith_fixture_job",
        max_concurrent_runs=1,
        tasks=[
            Task(
                task_key="hello",
                spark_python_task=SparkPythonTask(python_file="src/hello.py"),
                environment_key="default",
            )
        ],
        environments=[JobEnvironment(environment_key="default", spec=Environment(environment_version="3"))],
    )
    resources = Resources()
    resources.add_job("fixture_job", job)
    return resources
