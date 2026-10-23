"""Operations: events, reports, diagnosis, readiness, alert delivery, budgets and the ``ops`` CLI (offline)."""
# ruff: noqa: PT018, EM101, ANN401, ARG002, PYI034 - dense assertions and tiny fakes

from __future__ import annotations

import io
import json
import logging
import textwrap
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.cli.main import main
from bricksmith.context import ExecutionContext
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION, ConfigError
from bricksmith.graph import load_graph
from bricksmith.ops import (
    ERROR_CLASSES,
    AlertEvent,
    AlertNotifier,
    BudgetExceededError,
    FileAdapter,
    InMemoryAlertStore,
    InputSpec,
    JsonLinesSink,
    ListSink,
    LoggingSink,
    OpsConfigError,
    StatisticalAlertError,
    TableStats,
    WebhookAdapter,
    build_ops_report,
    classify_error,
    diagnose,
    evaluate_readiness,
    explain_plan,
    load_ops_config,
    notify_report,
    recovery_guidance,
    redact_text,
    redact_value,
    render_ops_report,
    resource_usage,
)
from bricksmith.ops.notify import bound_payload
from bricksmith.plan import build_plan
from bricksmith.runner import InMemoryTableIO, RetryPolicy, RunReport, run_plan

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

CTX = ExecutionContext(environment="test", parameters={})
FILES = {
    "bricksmith.toml": "",
    "catalogs/main/sales/raw.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="raw_orders")
        def raw(ctx):
            ctx.write("raw_orders", [1])
            raise RuntimeError("boom password=hunter2 Row(id=7, name='ann')")
    """,
    "catalogs/main/sales/clean.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="clean_orders")
        def clean(ctx):
            ctx.read("raw_orders")
            ctx.write("clean_orders", [1])
    """,
    "catalogs/main/sales/mart.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="mart")
        def mart(ctx):
            ctx.read("clean_orders")
            ctx.write("mart", [1])
    """,
    "catalogs/main/sales/side.py": """
        from bricksmith.tasks import task

        @task(kind="table", outputs="side")
        def side(ctx):
            ctx.write("side", [1])
    """,
}


def project(root: Path, files: Mapping[str, str] = FILES) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")


@pytest.fixture
def failed_run(tmp_path: Path) -> tuple[Path, Any, RunReport, ListSink]:
    project(tmp_path)
    graph = load_graph(tmp_path, context=CTX)
    plan = build_plan(graph)
    sink = ListSink()
    report = run_plan(
        plan, project_root=tmp_path, execution=CTX, table_io=InMemoryTableIO(), events=sink, run_id="run-1",
        retry=RetryPolicy(2, 0.0), sleep=lambda _s: None,
    )  # fmt: skip
    return tmp_path, plan, report, sink


def test_events_carry_identity_attempts_classes_and_no_secrets(
    failed_run: tuple[Path, Any, RunReport, ListSink],
) -> None:
    _, plan, report, sink = failed_run
    kinds = [e.kind for e in sink.events]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"
    assert kinds.count("task_started") == 3 and kinds.count("task_retry") == 1  # raw twice, side once
    assert {e.run_id for e in sink.events} == {"run-1"} and {e.plan_fingerprint for e in sink.events} == {
        plan.fingerprint
    }
    done = {e.task_id: e for e in sink.events if e.kind == "task_finished"}
    raw = done["main.sales.raw"]
    assert raw.state == "failed" and raw.attempt == 2 and raw.error is not None
    assert raw.error["class"] == "task_failure" and raw.targets and raw.seconds is not None
    assert done["main.sales.clean"].state == "blocked" and done["main.sales.clean"].fields["blocked_by"]
    assert done["main.sales.side"].state == "succeeded"
    text = "\n".join(e.to_json_line() for e in sink.events)
    assert "hunter2" not in text and "ann" not in text and "<redacted>" in text
    assert not report.ok


def test_sinks_json_lines_logging_and_a_broken_sink_never_fails_the_run(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    project(tmp_path)
    graph = load_graph(tmp_path, context=CTX)
    plan = build_plan(graph, roots=["main.sales.side"])
    stream = io.StringIO()
    run_plan(plan, project_root=tmp_path, execution=CTX, table_io=InMemoryTableIO(), events=JsonLinesSink(stream))
    lines = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [line["kind"] for line in lines] == ["run_started", "task_started", "task_finished", "run_finished"]
    with caplog.at_level(logging.INFO, logger="bricksmith.ops"):
        run_plan(plan, project_root=tmp_path, execution=CTX, table_io=InMemoryTableIO(), events=LoggingSink())
    assert any(json.loads(r.getMessage())["kind"] == "run_finished" for r in caplog.records)

    class Broken:
        def emit(self, event: object) -> None:
            raise OSError(str(event))

    report = run_plan(plan, project_root=tmp_path, execution=CTX, table_io=InMemoryTableIO(), events=Broken())
    assert report.ok


def test_error_classes_are_distinct() -> None:
    cases = {
        "task_failure": RuntimeError("bad arithmetic"),
        "missing_input": FileNotFoundError("nope"),
        "configuration": ConfigError("no profile"),
        "platform": TimeoutError("slow"),
        "statistical_alert": StatisticalAlertError("psi above threshold"),
    }
    assert {k: classify_error(v) for k, v in cases.items()} == {k: k for k in cases}
    assert set(ERROR_CLASSES) == set(cases)
    assert (
        classify_error(
            {"code": "TABLE_OR_VIEW_NOT_FOUND", "type": "AnalysisException", "message": "table or view not found: x"}
        )
        == "missing_input"
    )
    assert (
        classify_error({"code": "quality_check_failed", "type": "CheckFailedError", "message": "x"})
        == "statistical_alert"
    )
    assert classify_error({"code": "io_transient", "type": "TransientIoError", "message": "x"}) == "platform"


@pytest.mark.parametrize(
    "text",
    ["pass" * 50_000, "a" * 200_000, "rows=[ " * 30_000, "token='" * 30_000],
    ids=["key-prefix", "word", "open-bracket", "open-quote"],
)
def test_redaction_of_long_input_stays_bounded_in_time(text: str) -> None:
    # The unbounded patterns took minutes at this size; the ceiling is ~10x the measured worst case.
    start = time.perf_counter()
    out = redact_text(text)
    assert time.perf_counter() - start < 5.0
    assert len(out) <= 500


def test_redaction_masks_secrets_rows_and_bounds_text() -> None:
    text = "token=abc123 Authorization: Bearer xyz987654 url=https://u:pw@host/x Row(a=1, b='q') dapi0123456789abcdef"
    out = redact_text(text, secrets=["literal-secret"])
    for leaked in ("abc123", "xyz987654", ":pw@", "b='q'", "dapi0123456789abcdef"):
        assert leaked not in out
    assert "literal-secret" not in redact_text("see literal-secret here", secrets=["literal-secret"])
    assert len(redact_text("x" * 5000)) <= 500
    value = redact_value({"rows": [[1, 2]], "api_key": "k", "note": "password=zzz", "n": 3})
    assert value == {"rows": "<redacted>", "api_key": "<redacted>", "note": "password=<redacted>", "n": 3}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('password="synthetic alpha beta"', 'password="<redacted>"'),
        ("token='synthetic alpha beta' next step", "token='<redacted>' next step"),
        ('{"api_key": "synthetic alpha beta", "ok": 1}', '{"api_key": "<redacted>", "ok": 1}'),
        (r'secret="alpha \"beta\" gamma" tail', 'secret="<redacted>" tail'),
        ("password='alpha \"beta\" gamma' tail", "password='<redacted>' tail"),
        ('password="synthetic alpha beta', 'password="<redacted>"'),
        ('password="alpha\nbeta gamma"', 'password="<redacted>"'),
        ('password="alpha beta\\', 'password="<redacted>"'),
    ],
)
def test_quoted_secret_assignments_are_redacted_whole(text: str, expected: str) -> None:
    out = redact_text(text)
    assert out == expected
    assert "alpha" not in out and "beta" not in out
    assert redact_value({"note": text})["note"] == expected


def test_quoted_secret_redaction_keeps_bounds_and_unquoted_behaviour() -> None:
    assert redact_text('password="alpha ' + "x" * 5000 + '" ok') == 'password="<redacted>" ok'
    assert len(redact_text('password="' + "a b " * 5000)) <= 500
    assert redact_text("password=zzz other=1") == "password=<redacted> other=1"
    assert redact_text('password="a b", user=ann') == 'password="<redacted>", user=ann'
    assert "alpha" not in redact_text("pw alpha", secrets=["alpha"])


def test_injected_failure_is_diagnosable_from_the_report_with_upstream_context(
    failed_run: tuple[Path, Any, RunReport, ListSink],
) -> None:
    _, plan, report, _ = failed_run
    failed = diagnose(report, "main.sales.raw", plan)
    assert failed.error_class == "task_failure" and "RuntimeError" in failed.cause and "hunter2" not in failed.cause
    assert failed.partial_writes == (("main.sales.raw_orders", "write"),) or failed.partial_writes
    blocked = diagnose(report, "main.sales.mart", plan)
    assert blocked.root_causes == ("main.sales.raw",)
    assert [u.task_id for u in blocked.upstream] == ["main.sales.clean", "main.sales.raw"]
    assert "run replay" in blocked.replay_command
    data = build_ops_report(report, plan)
    assert [d["task_id"] for d in data["diagnoses"]] == ["main.sales.raw", "main.sales.clean", "main.sales.mart"]
    text = render_ops_report(data)
    assert "root cause: main.sales.raw" in text and "hunter2" not in text and "cost unavailable" in text
    assert json.loads(json.dumps(data)) == data
    guide = recovery_guidance(report)
    assert guide.done == ("main.sales.side",) and set(guide.redo) == {
        "main.sales.raw",
        "main.sales.clean",
        "main.sales.mart",
    }
    assert any("replay" in step for step in guide.steps) and guide.at_risk
    with pytest.raises(KeyError):
        diagnose(report, "nope")


def test_cost_is_labelled_unavailable_and_never_estimated(failed_run: tuple[Path, Any, RunReport, ListSink]) -> None:
    _, _, report, _ = failed_run
    usage = resource_usage(report)
    assert all(t["cost"]["status"] == "unavailable" and t["cost"]["reason"] for t in usage["tasks"])  # type: ignore[index]

    class Provider:
        def usage(self, task_id: str) -> dict[str, Any] | None:
            return {"value": 2.5, "unit": "dbu", "source": "synthetic"} if task_id.endswith("side") else None

    costs = {t["task_id"]: t["cost"] for t in resource_usage(report, Provider())["tasks"]}  # type: ignore[index]
    assert costs["main.sales.side"]["status"] == "reported" and costs["main.sales.raw"]["status"] == "unavailable"


NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


class FakeInspector:
    def __init__(self, tables: Mapping[str, TableStats]) -> None:
        self.tables = tables

    def inspect(self, name: str, *, null_columns: Sequence[str] = ()) -> TableStats:
        if name == "boom":
            raise RuntimeError("secret=abc")
        return self.tables.get(name, TableStats(name, exists=False))


def test_readiness_is_pure_and_distinguishes_missing_stale_incomplete_unavailable() -> None:
    inspector = FakeInspector(
        {
            "fresh": TableStats(
                "fresh",
                row_count=10,
                last_modified=NOW - timedelta(minutes=5),
                null_counts={"k": 1},
                columns=("k", "v"),
            ),
            "old": TableStats("old", row_count=10, last_modified=NOW - timedelta(days=2), columns=("k",)),
            "empty": TableStats("empty", row_count=0, last_modified=NOW, columns=("k",)),
            "nomod": TableStats("nomod", row_count=3, columns=("k",), note="history is not available"),
        }
    )
    specs = [
        InputSpec("fresh", 3600, 5, ("k", "v"), {"k": 0.2}),
        InputSpec("old", 3600),
        InputSpec("empty", None, 1),
        InputSpec("gone", 3600),
        InputSpec("nomod", 60),
        InputSpec("boom", 60),
    ]
    report = evaluate_readiness(specs, inspector, NOW)
    status = {(f.table, f.check): f.status for f in report.findings}
    assert (
        status[("fresh", "freshness")]
        == status[("fresh", "min_rows")]
        == status[("fresh", "null_fraction[k]")]
        == "ready"
    )
    assert status[("old", "freshness")] == "stale" and status[("empty", "min_rows")] == "incomplete"
    assert status[("gone", "freshness")] == "missing" and status[("nomod", "freshness")] == "unavailable"
    assert status[("boom", "inspect")] == "unavailable" and not report.ready
    assert all("abc" not in f.message for f in report.findings)
    classes = {f.table: f.error_class for f in report.findings if f.status != "ready"}
    assert classes["old"] == "missing_input" and classes["nomod"] == "platform"


class Recorder:
    def __init__(self, name: str = "rec", fail: int = 0) -> None:
        self.name, self.fail, self.payloads = name, fail, []

    def deliver(self, payload: Mapping[str, Any]) -> None:
        if self.fail:
            self.fail -= 1
            msg = "down token=abc"
            raise ConnectionError(msg)
        self.payloads.append(dict(payload))


def test_notification_failure_is_recorded_retried_and_leaves_the_run_result_intact(
    failed_run: tuple[Path, Any, RunReport, ListSink],
) -> None:
    _, _, report, _ = failed_run
    before = report.to_json()
    dead = Recorder("dead", fail=99)
    sleeps: list[float] = []
    notifier = AlertNotifier([dead], max_attempts=3, retry_delay=1.0, sleep=sleeps.append)
    records = notify_report(report, notifier, run_id="run-1")
    assert [r.status for r in records] == ["failed"] and records[0].attempts == 3 and sleeps == [1.0, 2.0]
    assert "abc" not in records[0].error and "ConnectionError" in records[0].error
    assert report.to_json() == before and not report.ok
    flaky = AlertNotifier([Recorder(fail=1)], max_attempts=2)
    assert [r.status for r in notify_report(report, flaky)] == ["delivered"] and flaky.records[0].attempts == 2


def test_alert_delivery_is_deduplicated_with_a_stable_key_and_a_window() -> None:
    clock = [1000.0]
    store = InMemoryAlertStore()
    adapter = Recorder()
    notifier = AlertNotifier([adapter], store, dedup_window_seconds=60, clock=lambda: clock[0])
    first = AlertEvent("psi_drift", "scores", "psi 0.4", error_class="statistical_alert", run_id="a", occurred_at="t1")
    same = AlertEvent("psi_drift", "scores", "psi 0.5", error_class="statistical_alert", run_id="b", occurred_at="t2")
    other = AlertEvent("psi_drift", "other", "psi 0.4", error_class="statistical_alert")
    assert first.dedup_key == same.dedup_key != other.dedup_key
    assert notifier.notify(first)[0].status == "delivered"
    assert notifier.notify(same)[0].status == "suppressed"
    assert notifier.notify(other)[0].status == "delivered"
    clock[0] += 61
    assert notifier.notify(same)[0].status == "delivered" and len(adapter.payloads) == 3

    class BrokenStore:
        def last_delivered(self, key: str) -> float | None:
            raise OSError(key)

        def record(self, key: str, at: float) -> None:
            raise OSError(key)

    assert AlertNotifier([Recorder()], BrokenStore()).notify(first)[0].status == "failed"


def test_payloads_are_bounded_and_redacted() -> None:
    alert = AlertEvent(
        "task_failed", "t" * 50, "m" * 20_000 + " password=hunter2", details={"rows": [[1]], "k" * 30: "v" * 9000}
    )
    payload = bound_payload(alert, 1024)
    assert (
        len(json.dumps(payload).encode()) <= 1024 and payload["truncated"] and payload["dedup_key"] == alert.dedup_key
    )
    assert "hunter2" not in json.dumps(bound_payload(AlertEvent("k", "s", "password=hunter2"), 1024))
    with pytest.raises(ValueError, match="max_payload_bytes"):
        AlertNotifier([], max_payload_bytes=10)


def test_generic_adapters_file_and_webhook(tmp_path: Path) -> None:
    path = tmp_path / "out" / "alerts.jsonl"
    notifier = AlertNotifier([FileAdapter(path)])
    notifier.notify(AlertEvent("k", "s", "m"))
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["subject"] == "s"
    seen: list[Any] = []

    class Response:
        status = 204

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def opener(request: Any, timeout: float) -> Response:
        seen.append((request.full_url, json.loads(request.data), timeout))
        return Response()

    AlertNotifier([WebhookAdapter("https://example.invalid/hook?key=s3", opener=opener)]).notify(
        AlertEvent("k", "s", "m")
    )
    assert seen[0][1]["subject"] == "s"

    def failing(_request: Any, timeout: float) -> Response:  # noqa: ARG001
        raise urllib.error.URLError("https://example.invalid/hook?key=s3")

    record = AlertNotifier(
        [WebhookAdapter("https://example.invalid/hook?key=s3", opener=failing)], max_attempts=1
    ).notify(AlertEvent("k", "s", "m"))[0]
    assert record.status == "failed" and "s3" not in record.error and "example" not in record.error
    with pytest.raises(ValueError, match="https"):
        WebhookAdapter("http://example.invalid")


def test_budget_violation_and_expensive_operations_are_shown_in_the_dry_run(tmp_path: Path) -> None:
    files = {
        "bricksmith.toml": """
            [ops.budgets]
            max_concurrency = 2
            max_scan_bytes = 1000
            max_maintenance_ops = 1

            [ops.estimates."main.sales.big"]
            scan_bytes = 5000
        """,
        "catalogs/main/sales/big.py": """
            from bricksmith.tasks import task

            @task(kind="table", outputs="big", metadata={"write": "overwrite"})
            def big(ctx):
                ctx.write("big", [1], operation="overwrite")
        """,
        "catalogs/main/sales/merged.py": """
            from bricksmith.tasks import task

            @task(kind="table", outputs="merged", metadata={"write": "merge"})
            def merged(ctx):
                ctx.write("merged", [1], operation="merge")
        """,
        "catalogs/main/sales/tidy.py": """
            from bricksmith.tasks import task

            @task(kind="table", outputs="tidy", metadata={"operations": ["optimize", "vacuum"]})
            def tidy(ctx):
                ctx.write("tidy", [1], operation="optimize")
        """,
    }
    project(tmp_path, files)
    plan = build_plan(load_graph(tmp_path, context=CTX))
    config = load_ops_config(tmp_path)
    explanation = explain_plan(plan, config, concurrency=4)
    assert not explanation.ok
    assert {v.budget for v in explanation.violations} == {"max_concurrency", "max_scan_bytes", "max_maintenance_ops"}
    assert {e.operation for e in explanation.expensive} >= {"overwrite", "merge", "optimize", "vacuum"}
    assert explanation.unchecked == ("main.sales.merged",)
    text = explanation.to_text()
    assert "VIOLATION max_scan_bytes" in text and "budgets VIOLATED" in text
    with pytest.raises(BudgetExceededError):
        explanation.enforce()
    assert explain_plan(plan, config, concurrency=2).totals["scan_bytes"] == 5000


def test_ops_config_rejects_bad_budgets(tmp_path: Path) -> None:
    (tmp_path / "bricksmith.toml").write_text("[ops.budgets]\nmax_concurrency = 0\n", encoding="utf-8")
    with pytest.raises(OpsConfigError, match="positive"):
        load_ops_config(tmp_path)
    (tmp_path / "bricksmith.toml").write_text("[ops.budgets]\nspeed = 1\n", encoding="utf-8")
    with pytest.raises(OpsConfigError, match="accepts only"):
        load_ops_config(tmp_path)


def test_cli_report_diagnose_readiness_and_explain(
    failed_run: tuple[Path, Any, RunReport, ListSink],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, plan, report, _ = failed_run
    (root / "report.json").write_text(report.to_json(), encoding="utf-8")
    (root / "plan.json").write_text(plan.to_json(), encoding="utf-8")
    base = ["--env", "free", "ops"]
    args = ["--report-in", str(root / "report.json"), "--plan", str(root / "plan.json")]
    assert main([*base, "report", *args]) == EXIT_OK
    assert "root cause: main.sales.raw" in capsys.readouterr().out
    assert main(["--json", *base, "diagnose", *args, "--task", "main.sales.mart"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["root_causes"] == ["main.sales.raw"]
    assert main([*base, "diagnose", *args, "--task", "ghost"]) == 2

    inspector = FakeInspector({"t": TableStats("t", row_count=5, last_modified=datetime.now(UTC), columns=("a",))})
    monkeypatch.setattr("bricksmith.ops.cli.default_inspector", lambda _a: inspector)
    assert (
        main([*base, "readiness", "--project", str(root), "--table", "t", "--max-age", "60", "--min-rows", "1"])
        == EXIT_OK
    )
    capsys.readouterr()
    assert (
        main(["--json", *base, "readiness", "--project", str(root), "--table", "t", "--min-rows", "9"])
        == EXIT_VALIDATION
    )
    assert json.loads(capsys.readouterr().out)["ready"] is False
    (root / "bricksmith.toml").write_text("[ops.budgets]\nmax_concurrency = 1\n", encoding="utf-8")
    assert main([*base, "explain", "--project", str(root), "--concurrency", "3"]) == EXIT_VALIDATION
    assert "max_concurrency" in capsys.readouterr().out
    assert main([*base, "explain", "--project", str(root), "--concurrency", "1"]) == EXIT_OK


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("password='it''s my secret' z", "password='<redacted>' z"),
        ('password="a""b c" z', 'password="<redacted>" z'),
        ('password="""a b""" z', 'password="""<redacted>""" z'),
        ("password=`a b` z", "password=`<redacted>` z"),
        ("password=\u201ca b\u201d z", "password=\u201c<redacted>\u201d z"),
        ("{'password': b'x y'}", "{'password': '<redacted>'}"),
        ("password => 'x y'", "password => '<redacted>'"),
        ("password := 'x y' z", "password := '<redacted>' z"),
        ("password == 'x y'", "password == '<redacted>'"),
        ('"API Key": "x y"', '"API Key": "<redacted>"'),
        (r"{\"password\": \"x y\"}", r"{\"password\": \"<redacted>\"}"),
        ("https://user:pa/ss@host/x", "https://user:<redacted>@host/x"),
        ("https://user:p@ss@host/x u", "https://user:<redacted>@host/x u"),
        ("http://host:8080/path", "http://host:8080/path"),
        ("jdbc:x://h;user=a;password=abc def;db=1", "jdbc:x://h;user=a;password=<redacted>;db=1"),
        ("password: |\n  multi line secret\n  more\nnext: 1", "password: <redacted>\nnext: 1"),
        ("compass: 'north east'", "compass: 'north east'"),
        ("bypass='keep this text'", "bypass='keep this text'"),
        ("tokens: 'the quick brown fox'", "tokens: 'the quick brown fox'"),
        (
            "access_token=t DB_PASSWORD=p clientSecret=s dbpassword=q",
            "access_token=<redacted> DB_PASSWORD=<redacted> clientSecret=<redacted> dbpassword=<redacted>",
        ),
        ("api_key=k private-key=k", "api_key=<redacted> private-key=<redacted>"),
    ],
)
def test_secret_values_are_masked_across_quote_styles_and_separators(text: str, expected: str) -> None:
    out = redact_text(text)
    assert out == expected
    assert redact_text(out) == expected


@pytest.mark.parametrize(
    "key",
    ["clientsecret", "accesstoken", "authtoken", "sessiontoken", "refreshtoken", "apitoken", "secretkey", "dbpass"],
)
@pytest.mark.parametrize("case", [str.lower, str.upper, str.title])
def test_compound_secret_keys_are_masked_in_any_case(key: str, case: object) -> None:
    name = case(key)  # type: ignore[operator]
    assert redact_text(f"{name}=s3cretval") == f"{name}=<redacted>"
    assert redact_text(f"{name}: 's3 cret'") == f"{name}: '<redacted>'"


def test_many_assignments_are_masked_in_linear_time() -> None:
    start = time.perf_counter()
    out = redact_text("password='a b' " * 30_000, limit=100_000)
    assert time.perf_counter() - start < 5.0
    assert "a b" not in out


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        (r"{\"password\": \"a\\\" b c\"}", "b c"),
        ("credentials:\n  password: s3cretvalue", "s3cretvalue"),
        ("password: |2\n  s3cretvalue", "s3cretvalue"),
        ("password: >2-\n  s3cretvalue", "s3cretvalue"),
        ("password: |-1\n  s3cretvalue", "s3cretvalue"),
        ("jdbc:x://h;user=a; password=abc def;db=1", "def"),
        ("jdbc:x://h;user=a;\t password=abc def;db=1", "def"),
    ],
)
def test_spelling_variants_leave_no_secret_fragment(text: str, leaked: str) -> None:
    out = redact_text(text)
    assert leaked not in out
    assert "<redacted>" in out


def test_nested_yaml_keeps_the_parent_key_and_the_sibling_after_the_value() -> None:
    out = redact_text("credentials:\n  password: s3cretvalue\n  user: bob")
    assert out == "credentials:\n  password: <redacted>\n  user: bob"
    assert redact_text("jdbc:x://h;user=a; password=abc def;db=1") == "jdbc:x://h;user=a; password=<redacted>;db=1"


def test_escaped_backslash_runs_are_linear_and_masked() -> None:
    start = time.perf_counter()
    out = redact_text('{\\"password\\": \\"' + "\\\\" * 20_000 + "x", limit=100_000)
    assert time.perf_counter() - start < 5.0
    assert "xx" not in out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("passwords=s3cretval tail", "passwords=<redacted> tail"),
        ('secrets: "s3 cret" tail', 'secrets: "<redacted>" tail'),
        ("smtppass=s3cretval tail", "smtppass=<redacted> tail"),
        ("ldappass: s3cretval", "ldappass: <redacted>"),
        ("mypass=s3cretval", "mypass=<redacted>"),
        ("dbpass=s3cretval", "dbpass=<redacted>"),
        ("passphrases=s3cretval", "passphrases=<redacted>"),
    ],
)
def test_plural_and_suffix_secret_keys_are_masked(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    ["compass=north", "bypass=on", "overpass=1", "tokens=12", "passing=1", "secretsmanager=1"],
)
def test_lookalike_keys_are_not_masked(text: str) -> None:
    assert redact_text(text) == text


def test_repeated_plural_and_suffix_keys_stay_bounded_in_time() -> None:
    start = time.perf_counter()
    out = redact_text("passwords=a smtppass=b secrets=c " * 20_000)
    assert time.perf_counter() - start < 5.0
    assert len(out) <= 500
