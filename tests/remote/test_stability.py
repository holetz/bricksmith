"""Data stability on the selected workspace: Spark equals the pure engine, the gate blocks dependents, reruns merge."""
# ruff: noqa: PT018 - dense assertions on run outcomes

from __future__ import annotations

import contextlib
import dataclasses
import shutil
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bricksmith.graph import load_graph
from bricksmith.monitor.stability import MemoryStore, check, parse_config, view_statements
from bricksmith.monitor.stability.profile import fit_rows_binning, profile_rows
from bricksmith.monitor.stability.spark import fit_spark_binning, profile_spark
from bricksmith.monitor.stability.tables import SCHEMAS
from bricksmith.plan import build_plan
from bricksmith.runner import apply_plan
from bricksmith.storage import SparkTableIO

if TYPE_CHECKING:
    from collections.abc import Iterator

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "shared_schema"
DDL = "id INT, month STRING, amount DOUBLE, kind STRING, flag BOOLEAN, region STRING, w DOUBLE"


def synthetic() -> list[tuple[Any, ...]]:
    rows = []
    for m, month in enumerate(("2026-01", "2026-02", "2026-03")):
        for i in range(90):
            amount = None if i % 17 == 0 else 0.0 if i % 6 == 0 else float((i * 13) % 50 + m * 9)
            kind = None if i % 23 == 0 else "abcz"[(i + m) % 4] if m == 2 else "abc"[i % 3]
            rows.append((m * 100 + i, month, amount, kind, i % 4 == 0, "ns"[i % 2], 1.0 + i % 3))
    return rows


def as_dicts(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    names = [c.split()[0] for c in DDL.split(", ")]
    return [dict(zip(names, r, strict=True)) for r in rows]


def same(a: object, b: object) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < 1e-9 * max(1.0, abs(a))
    return a == b


def assert_rows_equal(left: list[dict[str, Any]], right: list[dict[str, Any]], keys: tuple[str, ...]) -> None:
    def index(rows: list[dict[str, Any]]) -> dict[tuple[Any, ...], dict[str, Any]]:
        return {tuple(r[k] for k in keys): r for r in rows}

    a, b = index(left), index(right)
    assert a.keys() == b.keys()
    for key, row in a.items():
        other = b[key]
        assert all(same(row[c], other[c]) for c in row if c != "computed_at"), (key, row, other)


def test_spark_engine_equals_the_pure_engine(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    spark = remote_clients.spark()
    table = f"{remote_context.catalog}.{test_schema}.stability_input"
    spark.createDataFrame(synthetic(), DDL).write.saveAsTable(table)
    frame = spark.table(table)
    rows = as_dicts(synthetic())
    config = parse_config(
        {"period": "month", "exclude": ["id"], "segments": ["region", ["region", "flag"]], "weight": "w",
         "baseline": "previous", "min_rows": 10, "inference": {"numeric_bins": 4}}
    )  # fmt: skip
    pure_bins, pure_skipped = fit_rows_binning(rows, config, ["2026-01"], dataset="d", version=1)
    spark_bins, spark_skipped = fit_spark_binning(frame, config, ["2026-01"], dataset="d", version=1)
    assert spark_bins.to_dict() == pure_bins.to_dict() and spark_skipped == pure_skipped
    assert pure_bins.column("amount").zero and pure_bins.column("kind").kind == "categorical"
    periods = ["2026-01", "2026-02", "2026-03"]
    pure_profile, _ = profile_rows(pure_bins, rows, config, periods, dataset="d")
    spark_profile, _ = profile_spark(spark_bins, frame, config, periods, dataset="d")
    assert_rows_equal(pure_profile, spark_profile, ("period", "segment", "column", "bin"))
    assert {r["segment"] for r in spark_profile} >= {"", "region=n", "region=n,flag=true", "region=s,flag=false"}

    pure = check(rows, config, MemoryStore(), dataset="d", run_id="r", now="t")
    remote = check(frame, config, MemoryStore(), dataset="d", run_id="r", now="t")
    keys = ("period", "segment", "column", "metric")
    assert_rows_equal(pure.tables["results"], remote.tables["results"], keys)
    assert pure.outcome.to_dict() == remote.outcome.to_dict()
    unseen = next(
        r for r in remote.tables["results"]
        if (r["period"], r["segment"], r["column"], r["metric"]) == ("2026-03", "", "kind", "unseen_rate")
    )  # fmt: skip
    assert unseen["value"] > 0.2  # the new category "z" falls outside the fitted bins


def segment_history(latest: dict[str, int]) -> list[tuple[Any, ...]]:
    steady = {"a": 30, "b": 20, "c": 10}
    months = {"2026-01": steady, "2026-02": steady, "2026-03": steady, "2026-04": steady, "2026-05": latest}
    return [(m, ch, float(i % 10)) for m, counts in months.items() for ch, n in counts.items() for i in range(n)]


def test_spark_keeps_the_same_segments_as_the_pure_engine_across_runs(remote_clients: Clients) -> None:
    spark = remote_clients.spark()
    ddl = "month STRING, ch STRING, x DOUBLE"
    config = parse_config(
        {"period": "month", "columns": {"x": {"edges": [0, 5, 10]}}, "segments": ["ch"], "max_segments": 2,
         "baseline": {"kind": "fixed", "start": "2026-01", "end": "2026-03"}, "min_rows": 0,
         "persistence": {"immediate_red": True}, "gate": {"fail_on": ["red"]}}
    )  # fmt: skip
    first_rows = [r for r in segment_history({}) if r[0] != "2026-05"]
    for latest, carried_b in (({"a": 30, "c": 15}, "gone"), ({"a": 30, "b": 1, "c": 100}, "outranked")):
        rows = segment_history(latest)
        frame = spark.createDataFrame(rows, ddl)
        dicts: list[dict[str, Any]] = [dict(zip(("month", "ch", "x"), r, strict=True)) for r in rows]
        pure_bins, _ = fit_rows_binning(dicts, config, ["2026-01", "2026-02", "2026-03"], dataset="d", version=1)
        spark_bins, _ = fit_spark_binning(frame, config, ["2026-01", "2026-02", "2026-03"], dataset="d", version=1)
        assert spark_bins.to_dict() == pure_bins.to_dict()
        loaded = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
        carried = {"ch=b": "2026-01"}
        pure_profile, pure_info = profile_rows(
            pure_bins, dicts, config, ["2026-05"], dataset="d", rank_periods=loaded, carried=carried
        )
        spark_profile, spark_info = profile_spark(
            spark_bins, frame, config, ["2026-05"], dataset="d", rank_periods=loaded, carried=carried
        )
        assert_rows_equal(pure_profile, spark_profile, ("period", "segment", "column", "bin"))
        assert pure_info.to_dict() == spark_info.to_dict()
        assert "ch=b" in {r["segment"] for r in spark_profile}, carried_b

        outcomes = []
        for data in (dicts, frame):
            store = MemoryStore()
            first: list[dict[str, Any]] = [dict(zip(("month", "ch", "x"), r, strict=True)) for r in first_rows]
            earlier = check(
                first if data is dicts else spark.createDataFrame(first_rows, ddl), config, store,
                dataset="d", run_id="r1", now="t",
            )  # fmt: skip
            store.tables = {kind: list(items) for kind, items in earlier.tables.items()}
            outcomes.append(check(data, config, store, dataset="d", run_id="r2", now="t"))
        pure, remote = outcomes
        assert_rows_equal(pure.tables["results"], remote.tables["results"], ("period", "segment", "column", "metric"))
        assert pure.outcome.to_dict() == remote.outcome.to_dict() and remote.outcome.status == "failed"


ACTIONS = ("collect", "count", "first", "toPandas")


@contextlib.contextmanager
def counting_actions(frame: Any) -> Iterator[list[str]]:  # noqa: ANN401
    """Record the Spark actions issued on the frames of ``frame``'s class; nested calls count once."""
    cls = type(frame)
    seen: list[str] = []
    depth = [0]
    originals = {name: getattr(cls, name) for name in ACTIONS if hasattr(cls, name)}

    def wrap(name: str, original: Any) -> Any:  # noqa: ANN401
        def counted(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            if not depth[0]:
                seen.append(name)
            depth[0] += 1
            try:
                return original(*args, **kwargs)
            finally:
                depth[0] -= 1

        return counted

    for name, original in originals.items():
        setattr(cls, name, wrap(name, original))
    try:
        yield seen
    finally:
        for name, original in originals.items():
            setattr(cls, name, original)


def wide_rows(columns: int) -> tuple[str, list[tuple[Any, ...]]]:
    ddl = "month STRING, region STRING, flag BOOLEAN, " + ", ".join(
        f"v{j} DOUBLE" if j % 2 == 0 else f"k{j} STRING" for j in range(columns)
    )
    rows = []
    for m, month in enumerate(("2026-01", "2026-02")):
        for i in range(60):
            values = [float((i * (j + 3)) % 17 + m) if j % 2 == 0 else "abc"[(i + j) % 3] for j in range(columns)]
            rows.append((month, "ns"[i % 2], i % 3 == 0, *values))
    return ddl, rows


def test_spark_action_count_is_the_same_for_narrow_and_wide_segmented_tables(
    remote_clients: Clients,
) -> None:
    spark = remote_clients.spark()
    periods = ["2026-01", "2026-02"]
    small = {"period": "month", "segments": ["region"], "min_rows": 5, "inference": {"numeric_bins": 4}}
    large = {**small, "segments": ["region", "flag", ["region", "flag"]]}
    taken: dict[str, tuple[list[str], list[str]]] = {}
    types: dict[str, set[str]] = {}
    for label, columns, declaration in (("narrow", 2, small), ("wide", 12, large)):
        ddl, rows = wide_rows(columns)
        frame = spark.createDataFrame(rows, ddl)
        config = parse_config(declaration)
        with counting_actions(frame) as fit_actions:
            binning, _ = fit_spark_binning(frame, config, periods[:1], dataset="d", version=1)
        with counting_actions(frame) as profile_actions:
            profile, _ = profile_spark(binning, frame, config, periods, dataset="d")
        taken[label] = (list(fit_actions), list(profile_actions))
        types[label] = {dict(frame.dtypes)[c.column] for c in binning.columns}
        names = [c.split()[0] for c in ddl.split(", ")]
        dicts = [dict(zip(names, r, strict=True)) for r in rows]
        pure_bins, _ = fit_rows_binning(dicts, config, periods[:1], dataset="d", version=1)
        assert binning.to_dict() == pure_bins.to_dict()
        pure_profile, _ = profile_rows(pure_bins, dicts, config, periods, dataset="d")
        assert_rows_equal(pure_profile, profile, ("period", "segment", "column", "bin"))
    assert [len(taken[k][0]) - 1 for k in taken] == [len(types[k]) for k in taken]  # one job per column type
    assert len(types["wide"]) <= 3
    assert len(taken["narrow"][1]) == len(taken["wide"][1]) == 3


def count(spark: Any, name: str) -> int:  # noqa: ANN401
    return int(spark.table(name).count())


def test_declared_stability_gates_the_run_and_feeds_the_views(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    project = tmp_path / "project"
    shutil.copytree(EXAMPLE, project, ignore=shutil.ignore_patterns("__pycache__"))

    def t(name: str) -> str:
        return f"{catalog}.{test_schema}.{name}"

    def run(drift: str) -> Any:  # noqa: ANN401
        context = dataclasses.replace(
            remote_context,
            catalogs={"demo": catalog},
            schemas={"ml": test_schema},
            parameters={**remote_context.parameters, "stability_drift": drift},
        )
        graph = load_graph(project, context=context)
        plan = build_plan(graph, roots=["demo.ml.sales_report"])
        assert not plan.errors
        assert t("stability_results") in {x.physical for e in plan.entries for x in e.targets}
        return apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))

    first = run("no")
    assert first.ok, first.to_json()
    outcome = first.results[0].stability[0]
    assert outcome["status"] == "passed" and outcome["periods_profiled"] == 6 and outcome["version"] == 1
    tables = ("stability_binnings", "stability_profiles", "stability_results", "stability_alerts", "stability_runs")
    counts = {n: count(spark, t(n)) for n in tables}
    assert counts["stability_binnings"] == 1 and counts["stability_results"] > 0 and counts["stability_alerts"] == 0
    psi = spark.table(t("stability_results")).where("metric = 'psi'").collect()
    assert psi and all(r["status"] in ("green", "insufficient_volume") for r in psi)
    assert all(abs(r["value"]) < 1e-12 for r in psi if r["value"] is not None)  # identical months

    again = run("no")  # same data: one refreshed month, merged on the same keys
    assert again.ok and again.results[0].stability[0]["periods_profiled"] == 1
    after = {n: count(spark, t(n)) for n in tables}
    assert after == {**counts, "stability_runs": counts["stability_runs"] + 1}

    drifted = run("yes")
    sales, report = drifted.results[-2], drifted.results[-1]
    assert sales.state == "failed" and sales.error and sales.error["code"] == "stability_gate", drifted.to_json()
    assert report.state == "blocked" and report.blocked_by == (sales.task_id,)
    latest = spark.table(t("stability_results")).where("period = '2026-06' AND segment = '' AND metric = 'psi'")
    assert {r["column"]: r["status"] for r in latest.collect()} == {"amount": "red", "promoted": "green"}
    app = spark.table(t("stability_results")).where(
        "period = '2026-06' AND segment = 'channel=app' AND metric = 'volume_change'"
    )
    assert [(r["value"], r["status"]) for r in app.collect()] == [(1.0, "red")]  # 40 -> 80 rows
    open_alerts = spark.table(t("stability_alerts")).where("state = 'open'").collect()
    assert any(a["severity"] == "critical" and a["column"] == "amount" for a in open_alerts)

    for statement in view_statements(f"{catalog}.{test_schema}"):
        spark.sql(statement)
    status = spark.table(t("stability_dataset_status")).where("period = '2026-06'").collect()
    assert [r["status"] for r in status] == ["red"]
    assert count(spark, t("stability_open_alerts")) == len(open_alerts)
    assert {r["period"] for r in spark.table(t("stability_latest")).collect()} == {"2026-06"}


RULE_COLUMNS = ("rule_id", "rule_version")
RULED_TASKS = """
from bricksmith.tasks import task

DDL = "month STRING, region STRING, amount DOUBLE"


def rows():
    out = []
    for m, month in enumerate(("2026-01", "2026-02", "2026-03")):
        for i in range(60):
            high = (i % 2 == 0) == (m == 2)  # the regions swap in the last month; the whole table stays the same
            out.append((month, "n" if i % 2 == 0 else "s", float((i // 2) % 10 + (20 if high else 0))))
    return out


@task(kind="table", outputs="scores", metadata={{"write": "overwrite", "stability": {declaration!r}}})
def scores(ctx):
    ctx.write("scores", ctx.io.spark.createDataFrame(rows(), DDL))
"""


def test_informational_segments_never_gate_and_old_tables_gain_the_rule_columns(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str, tmp_path: Path
) -> None:
    spark = remote_clients.spark()
    catalog = remote_context.catalog or ""
    base = {"period": "month", "segments": ["region"], "baseline": "previous", "min_rows": 10}
    base |= {"gate": {"fail_on": ["red"]}, "persistence": {"immediate_red": True}}
    rules = [{"id": "pilot", "version": 3, "informational_segments": ["region=n", "region=s"]}]

    def t(prefix: str, name: str) -> str:
        return f"{catalog}.{test_schema}.{prefix}{name}"

    def run(prefix: str, declaration: dict[str, Any]) -> Any:  # noqa: ANN401
        project = tmp_path / prefix
        source = project / "catalogs/demo/ml/scores.py"
        source.parent.mkdir(parents=True)
        text = RULED_TASKS.format(declaration={**declaration, "prefix": prefix})
        source.write_text(textwrap.dedent(text), encoding="utf-8")
        context = dataclasses.replace(remote_context, catalogs={"demo": catalog}, schemas={"ml": test_schema})
        graph = load_graph(project, context=context)
        plan = build_plan(graph)
        assert not plan.errors
        return apply_plan(plan, graph, project_root=project, execution=context, table_io=SparkTableIO(spark))

    plain = run("plain_", base)
    assert plain.results[0].state == "failed" and plain.results[0].error["code"] == "stability_gate"  # type: ignore[index]

    columns = ", ".join(f"`{c}` {k}" for c, k in SCHEMAS["results"].items() if c not in RULE_COLUMNS)
    spark.sql(f"CREATE TABLE {t('ruled_', 'results')} ({columns})")  # a table as it was before the rule columns
    ruled = run("ruled_", {**base, "rules": rules})
    assert ruled.ok, ruled.to_json()
    assert ruled.results[0].stability[0]["red"] == 0 and ruled.results[0].stability[0]["open_alerts"] == 0
    table = spark.table(t("ruled_", "results"))
    assert set(RULE_COLUMNS) <= set(table.columns)
    shown = table.where("status = 'informational' AND metric = 'psi'").collect()
    assert shown and {r["rule_id"] for r in shown} == {"pilot"} and {r["rule_version"] for r in shown} == {3}
    whole = table.where("segment = '' AND metric = 'psi' AND period = '2026-03'").collect()
    assert whole and all(r["status"] != "red" and r["rule_id"] in ("", None) for r in whole)
    assert count(spark, t("ruled_", "alerts")) == 0
