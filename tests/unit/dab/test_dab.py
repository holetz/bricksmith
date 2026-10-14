"""Native resources and job assembly on synthetic projects written to ``tmp_path``; no Spark, no workspace."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.cli.groups import GROUPS
from bricksmith.cli.main import main
from bricksmith.dab import ResourceSet, build_resources, task_key
from bricksmith.dab.spec import parse_settings
from bricksmith.errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path

HEADER = "from bricksmith.tasks import task\n"

TASKS = {
    "catalogs/main/sales/raw.py": """
        @task(kind="table", outputs="raw_orders")
        def raw(ctx): ...
    """,
    "catalogs/main/sales/clean.py": """
        @task(kind="table", outputs="clean_orders", reads="raw_orders")
        def clean(ctx): ...
    """,
    "catalogs/main/sales/agg.py": """
        @task(kind="table", outputs="order_totals", reads="clean_orders")
        def agg(ctx): ...
    """,
    "catalogs/main/ref/codes.py": """
        @task(kind="table", outputs="codes", static=True)
        def codes(ctx): ...
    """,
    "catalogs/main/sales/uses_codes.py": """
        @task(kind="table", outputs="coded", reads=["ref.codes", "raw_orders"])
        def coded(ctx): ...
    """,
    "catalogs/main/sales/dormant.py": """
        @task(kind="table", outputs="old", enabled=False)
        def dormant(ctx): ...
    """,
    "catalogs/main/sales/needs_old.py": """
        @task(kind="table", outputs="late", reads="old")
        def late(ctx): ...
    """,
}

BASE = """
[bundle]
wheel = "dist/bricksmith-0.0.0-py3-none-any.whl"
"""


def project(root: Path, toml: str, tasks: dict[str, str] | None = None) -> Path:
    for relative, text in (TASKS if tasks is None else tasks).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HEADER + textwrap.dedent(text), encoding="utf-8")
    (root / "bricksmith.toml").write_text(BASE + textwrap.dedent(toml), encoding="utf-8")
    return root


def codes(root: Path) -> set[str]:
    return {item.code for item in build_resources(root).errors}


def test_pure_model_needs_neither_spark_nor_databricks_bundles(tmp_path: Path) -> None:
    root = project(tmp_path, '[jobs.nightly]\nselect = ["main.sales.raw", "main.sales.clean"]\n')
    script = (
        "import sys\n"
        "sys.modules['databricks'] = None\n"  # makes any 'import databricks...' fail
        "from bricksmith.dab import build_resources\n"
        f"a = build_resources({str(root)!r})\n"
        "assert not a.errors, a.errors\n"
        "assert not any(m.startswith(('pyspark', 'databricks.')) for m in sys.modules)\n"
        "print(a.resources.fingerprint)\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr[-800:]
    assert done.stdout.strip() == build_resources(root).resources.fingerprint


def test_mixed_task_kinds_are_ordered_with_graph_edges_mirrored(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        """
        [jobs.nightly]
        select = ["main.sales.raw", "main.sales.clean", "main.sales.agg"]
        max_concurrent_runs = 2
        timeout_seconds = 3600
        [[jobs.nightly.steps]]
        key = "gate"
        kind = "condition"
        left = "1"
        right = "1"
        after = ["main.sales.agg"]
        [[jobs.nightly.steps]]
        key = "export"
        kind = "script"
        path = "src/export.py"
        after = ["gate:true"]
        [[jobs.nightly.steps]]
        key = "report"
        kind = "notebook"
        path = "src/report.py"
        parameters = ["day=monday"]
        after = ["export"]
        """,
    )
    assembly = build_resources(root)
    assert assembly.ok, assembly.diagnostics
    job = assembly.resources.job("nightly")
    keys = [t.key for t in job.tasks]
    assert keys == ["main_sales_raw", "main_sales_clean", "main_sales_agg", "gate", "export", "report"]
    by_key = {t.key: t for t in job.tasks}
    assert [d.key for d in by_key["main_sales_clean"].depends_on] == ["main_sales_raw"]
    assert [(d.key, d.outcome) for d in by_key["export"].depends_on] == [("gate", "true")]
    assert by_key["main_sales_agg"].kind == "bricksmith"
    assert by_key["main_sales_agg"].parameters[-3:] == ("main.sales.agg", "--no-upstream", "--partial")
    assert (job.max_concurrent_runs, job.timeout_seconds) == (2, 3600)
    assert job.environment.dependencies == ("dist/bricksmith-0.0.0-py3-none-any.whl",)


def test_a_filter_cannot_silently_remove_a_required_producer(tmp_path: Path) -> None:
    root = project(tmp_path, '[jobs.a]\nselect = ["main.sales.clean", "main.sales.agg"]\n')
    assembly = build_resources(root)
    assert {i.code for i in assembly.errors} == {"missing_producer"}
    assert "main.sales.raw" in assembly.errors[0].message
    excluded = project(tmp_path, '[jobs.a]\nselect = ["main.sales.*"]\nexclude = ["main.sales.raw"]\n')
    assert "missing_producer" in codes(excluded)
    acknowledged = project(
        tmp_path,
        '[jobs.a]\nselect = ["main.sales.clean"]\n[jobs.a.contracts]\n"main.sales.raw" = "external"\n',
    )
    assert not codes(acknowledged)


def test_disabled_and_static_producers(tmp_path: Path) -> None:
    root = project(tmp_path, '[jobs.a]\nselect = ["main.sales.late"]\n')
    assert codes(root) == {"disabled_producer"}
    static_ok = project(tmp_path, '[jobs.a]\nselect = ["main.sales.raw", "main.sales.coded"]\n')
    assert not codes(static_ok)


def test_cross_job_edge_needs_an_orchestration_contract(tmp_path: Path) -> None:
    split = '[jobs.ingest]\nselect = ["main.sales.raw"]\n[jobs.publish]\nselect = ["main.sales.clean"]\n'
    assert codes(project(tmp_path, split)) == {"cross_job_edge"}
    run_job = split + '[jobs.publish.contracts]\n"main.sales.raw" = "run_job:ingest"\n'
    assembly = build_resources(project(tmp_path, run_job))
    assert assembly.ok, assembly.diagnostics
    publish = assembly.resources.job("publish")
    assert [t.key for t in publish.tasks] == ["run_ingest", "main_sales_clean"]
    assert publish.tasks[0].job == "ingest"
    assert [d.key for d in publish.tasks[1].depends_on] == ["run_ingest"]
    wrong = split + '[jobs.publish.contracts]\n"main.sales.raw" = "run_job:publish"\n'
    assert "contract_invalid" in codes(project(tmp_path, wrong))
    external = split + '[jobs.publish.contracts]\n"main.sales.raw" = "external"\n'
    assert codes(project(tmp_path, external)) == {"cross_job_edge"}
    loop = '[[jobs.ingest.steps]]\nkey = "again"\nkind = "run_job"\njob = "publish"\n'
    cycle = run_job + loop
    assert "job_cycle" in codes(project(tmp_path, cycle))


def test_trigger_contract_needs_a_matching_table_trigger(tmp_path: Path) -> None:
    jobs = '[jobs.ingest]\nselect = ["main.sales.raw"]\n[jobs.publish]\nselect = ["main.sales.clean"]\n'
    contract = '[jobs.publish.contracts]\n"main.sales.raw" = "trigger"\n'
    assert codes(project(tmp_path, jobs + contract)) == {"contract_invalid"}
    trigger = 'trigger_tables = ["main.sales.raw_orders"]\n'
    fixed = jobs.replace("[jobs.publish]\n", "[jobs.publish]\n" + trigger) + contract
    assembly = build_resources(project(tmp_path, fixed))
    assert assembly.ok, assembly.diagnostics
    assert assembly.resources.job("publish").trigger is not None


def test_collisions_are_rejected(tmp_path: Path) -> None:
    twin = (
        '[jobs.a]\nname = "same"\nselect = ["main.sales.raw"]\n[jobs.b]\nname = "same"\nselect = ["main.sales.agg"]\n'
    )
    assert "name_collision" in codes(project(tmp_path, twin))
    shared = '[jobs.a]\nselect = ["main.sales.raw"]\n[jobs.b]\nselect = ["main.sales.raw"]\n'
    assert "duplicate_task" in codes(project(tmp_path, shared))
    merged = '[mappings.schemas]\nsales = "shared"\nref = "shared"\n'
    assert "resource_collision" in codes(project(tmp_path, merged))
    preserved = '[bundle]\npreserved = ["schemas.main_sales"]\n'.replace("[bundle]\n", "")
    root = project(tmp_path, '[jobs.a]\nselect = ["main.sales.raw"]\n')
    (root / "bricksmith.toml").write_text(
        BASE + 'preserved = ["schemas.main_sales", "jobs.legacy"]\n[jobs.a]\nselect = ["main.sales.raw"]\n',
        encoding="utf-8",
    )
    assembly = build_resources(root)
    assert {i.code for i in assembly.errors} == {"preserved_collision"}
    assert assembly.resources.preserved == ("jobs.legacy", "schemas.main_sales")
    assert preserved


def test_unsupported_kinds_and_compute_options_fail_before_deployment(tmp_path: Path) -> None:
    sql = '[jobs.a]\nselect = ["main.sales.raw"]\n[[jobs.a.steps]]\nkey = "q"\nkind = "spark_jar"\n'
    assert "unsupported_task_kind" in codes(project(tmp_path, sql))
    root = project(tmp_path, '[jobs.a]\nselect = ["main.sales.raw"]\n')
    text = (root / "bricksmith.toml").read_text(encoding="utf-8")
    (root / "bricksmith.toml").write_text(
        text.replace("[bundle]", '[bundle]\ncompute = "cluster:abc"'), encoding="utf-8"
    )
    assert "unsupported_compute" in codes(root)
    both = (
        '[jobs.a]\nselect = ["main.sales.raw"]\nschedule = "0 0 2 * * ?"\ntrigger_tables = ["main.sales.raw_orders"]\n'
    )
    assert "incompatible_trigger" in codes(project(tmp_path, both))
    bad_cron = '[jobs.a]\nselect = ["main.sales.raw"]\nschedule = "daily"\n'
    assert "invalid_schedule" in codes(project(tmp_path, bad_cron))
    no_wheel = project(tmp_path, '[jobs.a]\nselect = ["main.sales.raw"]\n')
    (no_wheel / "bricksmith.toml").write_text('[jobs.a]\nselect = ["main.sales.raw"]\n', encoding="utf-8")
    assert "missing_wheel" in codes(no_wheel)
    loop = (
        '[jobs.a]\nselect = ["main.sales.raw"]\n[[jobs.a.steps]]\nkey = "x"\nkind = "script"\npath = "p.py"\n'
        'after = ["y"]\n[[jobs.a.steps]]\nkey = "y"\nkind = "script"\npath = "p.py"\nafter = ["x"]\n'
    )
    assert "step_cycle" in codes(project(tmp_path, loop))
    with pytest.raises(ConfigError, match="unknown key"):
        parse_settings({"jobs": {"a": {"select": ["x"], "cron": "x"}}})


def test_schedule_identity_notifications_and_target_scoping(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        """
        [bundle.schemas."main.sales"]
        owner = "analysts"
        targets = ["prod"]
        [bundle.volumes.landing]
        schema = "main.sales"
        [bundle.catalogs.main]
        managed = true
        [jobs.a]
        select = ["main.sales.raw"]
        schedule = "0 0 2 * * ?"
        timezone = "UTC"
        paused = true
        run_as_service_principal = "sp-1"
        notify_failure = ["ops@example.com"]
        parameters = { window = 7 }
        targets = ["prod"]
        """,
    )
    everything = build_resources(root)
    assert everything.ok, everything.diagnostics
    job = everything.resources.job("a")
    assert job.schedule is not None
    assert job.schedule.paused
    assert job.run_as is not None
    assert job.run_as.service_principal == "sp-1"
    assert job.notifications is not None
    assert job.notifications.on_failure == ("ops@example.com",)
    assert job.parameters == {"window": "7"}
    dev = build_resources(root, target="dev").resources
    assert (dev.jobs, [s.key for s in dev.schemas], len(dev.volumes), len(dev.catalogs)) == ((), ["main_ref"], 1, 1)
    prod = build_resources(root, target="prod").resources
    assert [j.key for j in prod.jobs] == ["a"]
    assert {s.key: s.owner for s in prod.schemas}["main_sales"] == "analysts"
    both = '[jobs.a]\nselect = ["main.sales.raw"]\nrun_as_user = "u@example.com"\nrun_as_service_principal = "sp"\n'
    assert "incompatible_identity" in codes(project(tmp_path, both))


def test_snapshot_is_stable_and_round_trips(tmp_path: Path) -> None:
    root = project(tmp_path, '[jobs.a]\nselect = ["main.sales.*"]\nexclude = ["main.sales.coded"]\n')
    first = build_resources(root).resources
    again = build_resources(root).resources
    assert first.to_json() == again.to_json()
    assert ResourceSet.from_json(first.to_json()) == first
    data = json.loads(first.to_json())
    assert list(data) == sorted(data)
    assert data["version"] == 1
    assert [s["key"] for s in data["schemas"]] == ["main_ref", "main_sales"]
    assert [c["managed"] for c in data["catalogs"]] == [False]
    assert task_key("main.sales.t[region=north,window=7]") == "main_sales_t_region_north_window_7"


def test_cli_validate_and_resources(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert "bricksmith.dab.cli" in GROUPS
    good = project(tmp_path / "good", '[jobs.a]\nselect = ["main.sales.raw"]\n')
    assert main(["bundle", "validate", "--project", str(good)]) == 0
    assert main(["bundle", "resources", "--project", str(good)]) == 0
    text = capsys.readouterr().out
    assert "job a (a)" in text
    assert "bricksmith main_sales_raw  <- -" in text
    assert main(["--json", "bundle", "resources", "--project", str(good)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["jobs"][0]["tasks"][0]["task_id"] == "main.sales.raw"
    bad = project(tmp_path / "bad", '[jobs.a]\nselect = ["main.sales.clean"]\n')
    assert main(["bundle", "validate", "--project", str(bad), "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["diagnostics"][0]["code"] == "missing_producer"


def test_adapter_builds_native_objects_and_refuses_invalid_projects(tmp_path: Path) -> None:
    pytest.importorskip("databricks.bundles")
    from databricks.bundles.core import Bundle  # noqa: PLC0415

    from bricksmith.dab import load_resources  # noqa: PLC0415
    from bricksmith.errors import ValidationError  # noqa: PLC0415

    toml = (
        '[jobs.a]\nselect = ["main.sales.raw", "main.sales.clean"]\nschedule = "0 0 2 * * ?"\n'
        'notify_failure = ["ops@example.com"]\nparameters = { window = 7 }\n'
        '[[jobs.a.steps]]\nkey = "gate"\nkind = "condition"\nleft = "1"\nright = "1"\nafter = ["main.sales.clean"]\n'
        '[[jobs.a.steps]]\nkey = "go"\nkind = "script"\npath = "src/go.py"\nafter = ["gate:true"]\n'
    )
    root = project(tmp_path / "ok", toml)
    bundle = Bundle(target="dev")
    resources = load_resources(bundle, root)
    job: Any = resources.jobs["a"]
    assert [t.task_key for t in job.tasks] == ["main_sales_raw", "main_sales_clean", "gate", "go"]
    wheel_task = job.tasks[0].python_wheel_task
    assert wheel_task.entry_point == "bricksmith-run"
    assert job.schedule.quartz_cron_expression == "0 0 2 * * ?"
    assert job.tasks[3].depends_on[0].outcome == "true"
    assert "main_sales" in resources.schemas
    bad = project(tmp_path / "bad", '[jobs.a]\nselect = ["main.sales.clean"]\n')
    with pytest.raises(ValidationError, match="missing_producer"):
        load_resources(bundle, bad)
