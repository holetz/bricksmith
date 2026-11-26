"""Operations: events, reports, diagnosis, readiness, alert delivery, budgets and the ``ops`` CLI (offline)."""
# ruff: noqa: PT018, EM101, ANN401, ARG002, PYI034 - dense assertions and tiny fakes

from __future__ import annotations

import io
import itertools
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
        ("GET http://host:8080/api?email=bob@example.com 200", "GET http://host:8080/api?email=bob@example.com 200"),
        ("https://host:443/users/@alice", "https://host:443/users/@alice"),
        ("https://u:pw@host:8080/api?e=a@b.com", "https://u:<redacted>@b.com"),
        ("https://u:pw@host/users/@alice", "https://u:<redacted>@alice"),
        ("https://user:p@ss/w@host/x", "https://user:<redacted>@host/x"),
        ("postgresql://admin:p@ss@db.example.com", "postgresql://admin:<redacted>@db.example.com"),
        ("smtp://user:P@ss.w0rd@mail.example.com", "smtp://user:<redacted>@mail.example.com"),
        ("https://user:1234@host/x", "https://user:<redacted>@host/x"),
        ("https://user:p?x@host/y", "https://user:<redacted>@host/y"),
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


@pytest.mark.parametrize("indicator", ["|", ">-", "|2", "|+"])
def test_block_scalar_with_a_trailing_comment_is_masked(indicator: str) -> None:
    out = redact_text(f"password: {indicator} # note\n  s3cretvalue\nuser: bob")
    assert out == "password: <redacted>\nuser: bob"


def test_hash_glued_to_the_indicator_is_treated_as_a_comment_and_leaks_nothing() -> None:
    out = redact_text("password: |#x\n  s3cretvalue\nuser: bob")
    assert "s3cretvalue" not in out
    assert "<redacted>" in out


def test_long_blank_run_after_a_block_indicator_stays_bounded_in_time() -> None:
    start = time.perf_counter()
    out = redact_text("password: |" + " " * 20_000 + "\n  s3cretvalue", limit=100_000)
    out += redact_text("password: |" + " \t" * 10_000 + "# c\n  s3cretvalue\n", limit=100_000)
    assert time.perf_counter() - start < 5.0
    assert "s3cretvalue" not in out


def test_url_credential_masking_stays_bounded_in_time() -> None:
    started = time.perf_counter()
    redact_text("https://user:" + "a@/" * 40_000, limit=200_000)
    redact_text("https://user:" + "@/" * 40_000 + "?x", limit=200_000)
    redact_text("https://user:12/" + "@" * 40_000, limit=200_000)
    assert time.perf_counter() - started < 5


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            '{"health":"http://host:8080/ok","db":"postgres://u:pw@db/x"}',
            '{"health":"http://host:8080/ok","db":"postgres://u:<redacted>@db/x"}',
        ),
        ("http://h:80/,postgres://u:secret@db/x", "http://h:80/,postgres://u:<redacted>@db/x"),
        ("https://user:1234/secret@host/x", "https://user:<redacted>@host/x"),
        ("https://u:a@b?c@host/x", "https://u:<redacted>@host/x"),
        ("https://user:p@ss?word@host/x", "https://user:<redacted>@host/x"),
        ("https://u:pa/@host/x", "https://u:<redacted>@host/x"),
        ("https://u:pw@host/api/users/bob@example.com", "https://u:<redacted>@example.com"),
        ("https://u:pw@host:8080/api/users/bob@example.com", "https://u:<redacted>@example.com"),
        ("https://host:8080/api/users/bob@example.com", "https://host:8080/api/users/bob@example.com"),
        ("https://u:pw@example.com", "https://u:<redacted>@example.com"),
        ("postgres://bob:p/ss@word@db.example.com", "postgres://bob:<redacted>@db.example.com"),
        ("postgres://bob:p?ss@word@db.example.com", "postgres://bob:<redacted>@db.example.com"),
        ("postgres://bob:p#ss@word@db.example.com", "postgres://bob:<redacted>@db.example.com"),
        ("postgres://bob:a/b@c.d@e.f", "postgres://bob:<redacted>@e.f"),
        ("postgres://bob:p/ss@word@db.example.com/x", "postgres://bob:<redacted>@db.example.com/x"),
        ("postgres://bob:p/ss@word@db.example.com/users/a@b.com", "postgres://bob:<redacted>@b.com"),
        ("postgres://bob:p/s/s@word@db.example.com", "postgres://bob:<redacted>@db.example.com"),
        ("postgres://bob:p/ss@wo@rd@db.example.com", "postgres://bob:<redacted>@db.example.com"),
        ("see postgres://bob:p/ss@word@db.example.com now", "see postgres://bob:<redacted>@db.example.com now"),
        ("https://u:pw@host/a/bob@x.com", "https://u:<redacted>@x.com"),
        ("postgres://admin:p@ss/word@db.example.com", "postgres://admin:<redacted>@db.example.com"),
        ("postgres://admin:p@ss?word@db.example.com", "postgres://admin:<redacted>@db.example.com"),
        ("redis://u:x@y/z@cache.example.com", "redis://u:<redacted>@cache.example.com"),
        ("postgres://u:p@ss#w@db.example.com:5432/x", "postgres://u:<redacted>@db.example.com:5432/x"),
        ("redis://u:p@ss@[::1]:6379/0", "redis://u:<redacted>@[::1]:6379/0"),
        ("x://u:pw@", "x://u:<redacted>@"),
        ("x://u:5432/p@ss/w@h.example.com/x", "x://u:<redacted>@h.example.com/x"),
        ('{"d":"postgres://u:p@ss/w@db.example.com"}', '{"d":"postgres://u:<redacted>@db.example.com"}'),
    ],
)
def test_url_credentials_are_masked_after_the_separator_rules(text: str, expected: str) -> None:
    out = redact_text(text)
    assert out == expected
    assert redact_text(out) == expected


def test_url_credential_masking_with_many_urls_and_addresses_stays_bounded_in_time() -> None:
    started = time.perf_counter()
    out = redact_text("http://h:80/,a://u:p@x.y/z " * 20_000, limit=1_000_000)
    redact_text("https://u:" + "a@b.c" * 40_000, limit=300_000)
    redact_text("x://a:1/" * 40_000, limit=500_000)
    assert time.perf_counter() - started < 5
    assert "p@x" not in out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("lowpass=100 highpass=200 bandpass=300 multipass=4", "lowpass=100 highpass=200 bandpass=300 multipass=4"),
        ("clientsecrets=s3cretval", "clientsecrets=<redacted>"),
        ("appsecrets: s3cretval", "appsecrets: <redacted>"),
        (
            "dbpass=a ldappass=b mypass=c smtppass=d",
            "dbpass=<redacted> ldappass=<redacted> mypass=<redacted> smtppass=<redacted>",
        ),
        ("passwords=a secrets=b", "passwords=<redacted> secrets=<redacted>"),
        ("apikeys=abc compass=n tokens=1 secretsmanager=1", "apikeys=<redacted> compass=n tokens=1 secretsmanager=1"),
    ],
)
def test_pass_suffix_is_allow_listed_and_compound_plurals_are_masked(text: str, expected: str) -> None:
    assert redact_text(text) == expected


def test_open_pass_suffix_masks_keystore_and_database_credentials() -> None:
    assert (
        redact_text("keytool -storepass=changeit -keypass=hunter2")
        == "keytool -storepass=<redacted> -keypass=<redacted>"
    )
    assert (
        redact_text("postgrespass=hunter2 vaultpass=s3cr3t jdbcpass=x")
        == "postgrespass=<redacted> vaultpass=<redacted> jdbcpass=<redacted>"
    )
    assert (
        redact_text("bypass=1 surpass=2 trespass=3 impass=4 underpass=5")
        == "bypass=1 surpass=2 trespass=3 impass=4 underpass=5"
    )


def test_allow_listed_suffix_keeps_linear_time() -> None:
    out = redact_text("dbpass=a lowpass=1 clientsecrets=b " * 20_000, limit=100_000)
    assert "lowpass=1" in out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('passwords=["alpha", "beta"]', "passwords=<redacted>"),
        ("secret=['alpha', 'beta'] next=1", "secret=<redacted> next=1"),
        ('{"token": ["a", "b"], "ok": 1}', '{"token": <redacted>, "ok": 1}'),
        ("secrets: [alpha, beta]", "secrets: <redacted>"),
        ("password=[[a, b], [c]] tail", "password=<redacted> tail"),
        ('password=["a]b", "c"] tail', "password=<redacted> tail"),
        ("password=[a, b\nnext=1", "password=<redacted>\nnext=1"),
        ("password=[a, b", "password=<redacted>"),
        ('"passwords": [\n  "hunter2",\n  "swordfish"\n]', '"passwords": <redacted>'),
        ("secrets: [\n  a,\n  [b, c],\n  'd]'\n] tail", "secrets: <redacted> tail"),
    ],
)
def test_bracketed_list_values_under_secret_keys_are_masked_whole(text: str, expected: str) -> None:
    assert redact_text(text) == expected


def test_bracketed_list_masking_keeps_linear_time() -> None:
    out = redact_text("password=[a, [b, c], 'd'] ok=1 " * 20_000 + "secret=[" * 20_000, limit=100_000)
    assert "ok=1" in out


def test_multiline_bracketed_list_masking_keeps_linear_time() -> None:
    out = redact_text("secret=[\n  a,\n" * 20_000 + "password=[x, y] ok=1", limit=1_000_000)
    assert "x, y" not in out


_HUNTER_LIST = 'password: [\n  "hunter2"\n]\n'


@pytest.mark.parametrize(
    "prefix",
    [
        "token=[\n" * 5,
        "token=[\n" * 500,
        "secret=[a,\n" * 50,
        "password=[\n[\n[\n",
        "token=[ 'x\n" * 20,
        "token=[\n" * 5 + "unclosed text\n" * 20,
    ],
    ids=["five", "many", "items", "nested", "single-quote", "with-text"],
)
def test_earlier_unclosed_brackets_do_not_unmask_a_later_secret_list(prefix: str) -> None:
    out = redact_text(prefix + _HUNTER_LIST + "x" * 20_000, limit=100_000)
    assert "hunter2" not in out
    assert "password: <redacted>" in out


def test_earlier_unclosed_double_quote_brackets_still_mask_the_later_secret() -> None:
    out = redact_text('token=[ "x\n' * 20 + _HUNTER_LIST + "x" * 20_000, limit=100_000)
    assert "hunter2" not in out
    assert out.startswith("token=<redacted>")


def test_unclosed_prefix_reproduction_masks_the_later_list() -> None:
    out = redact_text("token=[\n" * 5 + 'password: [\n  "hunter2"\n]\n' + "x" * 20000)
    assert "hunter2" not in out


def test_bracket_masking_leaves_ordinary_text_unchanged() -> None:
    text = "items = [1, 2, 3]\nnames: [alpha, beta]\nmatrix=[[1, 2],\n[3, 4]]\nnote: see [docs] and [more\nend"
    assert redact_text(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('secrets: [\n  "alpha",\n  it\'s-beta\n]', "secrets: <redacted>"),
        ("password: [abc'def,\n ghi]", "password: <redacted>"),
        ("password: [ 'abc,\n ghi]", "password: <redacted>"),
        ("secrets: [\n  james' book,\n  beta\n]", "secrets: <redacted>"),
        ("secrets: [don't, can't,\r\n  won't]\r\nok=1", "secrets: <redacted>\r\nok=1"),
        ("secrets: [\n  [it's, a],\n  [b]\n] tail", "secrets: <redacted> tail"),
        ('secrets: [\n  it\'s\n]\npassword: [\n  "h"\n]', "secrets: <redacted>\npassword: <redacted>"),
        ("secrets: [\n  'it''s',\n  \"it's\"\n]", "secrets: <redacted>"),
        ("'tis: 1\nsecrets: [\n  a,\n  b\n]", "'tis: 1\nsecrets: <redacted>"),
        ('secrets: [\n  "a",\n  say "hi\n]', "secrets: <redacted>"),
        ("secrets: [\n  `a`,\n  say `hi\n]", "secrets: <redacted>"),
        ('password: [ "abc,\n ghi]', "password: <redacted>"),
        ("password: [ `abc,\n ghi]", "password: <redacted>"),
        ('secrets: [\n  2" pipe,\n  beta\n]', "secrets: <redacted>"),
        (
            'secrets: [\n  "it\'s",\n  `x`,\n  say "hi\n]\npassword: [\n  b\n]',
            "secrets: <redacted>\npassword: <redacted>",
        ),
    ],
)
def test_multiline_secret_lists_with_lone_quotes_are_masked_whole(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "don't leak [it's fine], won't you? the dog's bone",
        "names: [it's, o'neil,\n ghi]",
        "items = ['a', 'b']\nnote: 'quoted' and it's here",
        "tokenize: [it's,\n  not]\nthe dogs' bowl",
        "I can't say: 'maybe' [or\nnot]",
        'say "hi [or\nnot] ok',
        "run `ls [or\nnot] ok",
        'names: [say "hi,\n ghi]',
        "names: [`x,\n ghi]",
        'a 2" pipe, b: "ok"',
    ],
)
def test_ordinary_apostrophe_text_is_returned_unchanged(text: str) -> None:
    assert redact_text(text) == text


def test_secret_list_masking_leaves_following_apostrophe_prose_unchanged() -> None:
    assert redact_text("secrets: [a, b]\nthe dog's bone, don't") == "secrets: <redacted>\nthe dog's bone, don't"


@pytest.mark.parametrize(
    "unit",
    [
        "secret=[ it's\n",
        "token=[ 'x\n",
        "token=[a'b,\n",
        "token=[ james'\n[",
        "token=['\n",
        "secret=[ it's, 'x',\n",
        'token=[ "x\n',
        'token=[ say "hi,\n',
        'token=["\n',
        "token=[ `x\n",
        "token=[ say `hi,\n",
        "token=[`\n",
    ],
    ids=[
        "mid-word",
        "lone-leading",
        "mid-comma",
        "trailing",
        "bare",
        "mixed",
        "dq",
        "dq-mid",
        "dq-bare",
        "bt",
        "bt-mid",
        "bt-bare",
    ],
)
def test_lone_apostrophe_list_scan_is_linear_in_time(unit: str) -> None:
    for size in (4_096, 65_536):
        text = unit * (size // len(unit)) + _HUNTER_LIST
        started = time.perf_counter()
        out = redact_text(text, limit=1_000_000)
        assert time.perf_counter() - started < 2.0
        assert "hunter2" not in out


def test_bracket_scan_budget_is_not_exhausted_by_early_input_at_scale() -> None:
    for size in (4_000, 64_000):
        out = redact_text("token=[\n" * (size // 8) + _HUNTER_LIST, limit=1_000_000)
        assert "hunter2" not in out


def test_bracket_scan_stays_bounded_in_time_including_quote_mismatched_brackets() -> None:
    def run(n: int) -> float:
        started = time.perf_counter()
        redact_text("token=[\n" * n + _HUNTER_LIST, limit=1_000_000)
        redact_text("token=['[\n" * n + _HUNTER_LIST, limit=1_000_000)
        redact_text('token=["[\n' * n + _HUNTER_LIST, limit=1_000_000)
        redact_text("token=[ 'a\n[ " * n + _HUNTER_LIST, limit=1_000_000)
        redact_text("token=['[' [\n" * n + _HUNTER_LIST, limit=1_000_000)
        return time.perf_counter() - started

    small, large = run(500), run(8_000)
    assert large < 5
    assert large < max(small, 0.01) * 16 * 8


@pytest.mark.parametrize(
    "key",
    [
        "firewallpass",
        "simpass",
        "callpass",
        "smallpass",
        "xlowpass",
        "mybypass",
        "dbcompass",
        "tunderpass",
        "ximpass",
        "Firewallpass",
    ],
)
def test_qualifier_ending_in_exempt_word_is_still_a_credential_key(key: str) -> None:
    out = redact_text(f"{key}=hunter2 tail")
    assert "hunter2" not in out
    assert out == f"{key}=<redacted> tail"


@pytest.mark.parametrize(
    "text",
    [
        "lowpass=1",
        "highpass=1",
        "bandpass=1",
        "allpass=1",
        "multipass=1",
        "compass=1",
        "bypass=1",
        "overpass=1",
        "surpass=1",
        "trespass=1",
        "underpass=1",
        "impass=1",
        "my_lowpass=1",
        "cfg.bypass=1",
    ],
)
def test_whole_word_exempt_pass_keys_stay_unmasked(text: str) -> None:
    assert redact_text(text) == text


def test_whole_qualifier_pass_denylist_stays_linear() -> None:
    timings = []
    for size in (4_000, 64_000):
        started = time.perf_counter()
        redact_text("firewallpass=a lowpass=b " * (size // 24), limit=500_000)
        timings.append(time.perf_counter() - started)
    assert timings[1] < 5.0


def test_bare_host_url_password_with_slash_and_at_stays_bounded_in_time() -> None:
    def run(n: int) -> float:
        started = time.perf_counter()
        redact_text("postgres://bob:p/ss" + "@w" * n + "@db.example.com", limit=1_000_000)
        redact_text("postgres://bob:p/ss" + "@w.x" * n, limit=1_000_000)
        return time.perf_counter() - started

    assert run(4_000) < 5
    assert run(64_000) < 5


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/users/a@b.com",
        "https://example.com/p/ss@word",
        "https://example.com?mail=a@b.com",
        "https://example.com/a/@alice/x",
        "https://example.com/a?x=1&y=p/q@r.s",
        "https://user@example.com/a/b@c.com",
        "ping me at a@b.com? or c@d.org/now",
        "mail a@b.com/c@d.org today",
    ],
)
def test_url_masking_does_not_over_redact_bare_host_paths_and_prose(text: str) -> None:
    assert redact_text(text) == text


_PASSWORD_PARTS = ("@", "/", "?", "#")
_ORDERED_PASSWORDS = [
    "Zq9" + "Kw7".join(order)
    for size in range(1, len(_PASSWORD_PARTS) + 1)
    for order in itertools.permutations(_PASSWORD_PARTS, size)
]


@pytest.mark.parametrize("host", ["db", "db.example.com", "[::1]"])
@pytest.mark.parametrize("tail", ["", "/x", "/x/y", ":5432", ":5432/x", "?sslmode=require", "#frag"])
@pytest.mark.parametrize("password", _ORDERED_PASSWORDS)
def test_url_password_is_masked_whole_for_every_ordering_of_separator_characters(
    password: str, tail: str, host: str
) -> None:
    out = redact_text(f"postgres://bob:{password}@{host}{tail} end")
    assert out == f"postgres://bob:<redacted>@{host}{tail} end"
    assert "Zq9" not in out
    assert "Kw7" not in out
    assert redact_text(out) == out


@pytest.mark.parametrize(
    "text",
    [
        "https://example.com/users/a@b.com",
        "https://host:8080/api/users/bob@example.com",
        "https://host:8080/api?email=a@b.com",
        "https://host:443/users/@alice/x",
        "ping me at a@b.com or c@d.org/now",
        "postgres://u@db.example.com/x",
        "see https://example.com:8080/a b@c.d",
        "user:name@host is not a url",
        "mailto:bob@example.com",
    ],
)
def test_url_masking_leaves_text_without_a_url_credential_exactly_unchanged(text: str) -> None:
    assert redact_text(text) == text


def test_url_userinfo_separator_scan_is_linear_at_4k_and_64k() -> None:
    def run(n: int) -> float:
        started = time.perf_counter()
        for body in ("@a/" * n, "@a.b" * n, "@" * n, "@w?#/" * n, "/" * n + "@h"):
            redact_text("postgres://bob:p" + body, limit=2_000_000)
            redact_text("postgres://bob:1234" + body, limit=2_000_000)
        return time.perf_counter() - started

    assert run(4_000) < 5
    assert run(64_000) < 5


_QUOTES = ['"', "'", "`"]


@pytest.mark.parametrize(
    "text",
    [
        'passwords: [\n  abc"def,\n  s3cr3t\n]\nname: "app"\n',
        "passwords = [\n  'lone,\n  s3cr3t\n]\nINFO user='bob' done",
        "token=[ 'a\n" * 10 + "password: [\n  hunter2,\n  s3cr3t\n]\n" + "x" * 3000 + " 'bob' ",
    ],
    ids=["double-quote", "single-quote", "repeated-prefix"],
)
def test_lone_quote_with_a_later_closer_never_unmasks_the_secret_list(text: str) -> None:
    out = redact_text(text, limit=1_000_000)
    assert "s3cr3t" not in out
    assert "hunter2" not in out


@pytest.mark.parametrize("quote", _QUOTES)
@pytest.mark.parametrize("prefixes", [0, 1, 7])
@pytest.mark.parametrize(
    "closer", ["", 'name: "app"\n', "INFO user='bob' done\n", "run `ls`\n", "a b\n" * 3 + "'z' \"y\" `x`"]
)
def test_generated_quote_variants_close_the_secret_list_class(quote: str, prefixes: int, closer: str) -> None:
    prefix = f"token=[ {quote}a\n" * prefixes
    text = f"{prefix}password: [\n  {quote}lone,\n  s3cr3t\n]\n{closer}"
    out = redact_text(text, limit=1_000_000)
    assert "s3cr3t" not in out
    assert "lone" not in out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("password: [ 'x,\n  a\n  b\nname: 1\n", "password: <redacted>\nname: 1\n"),
        ("  password: [ 'x,\n    a\n  b: 1\n", "  password: <redacted>\n  b: 1\n"),
        ("password: [ 'x,\n  a\n\n  b\n", "password: <redacted>\n\n  b\n"),
        ("password: [ 'x,\n  a", "password: <redacted>"),
        ("password: [ 'x,\r\n  a\r\nname: 1\r\n", "password: <redacted>\r\nname: 1\r\n"),
        ("password: [a, b]\n  note\n", "password: <redacted>\n  note\n"),
        ("items: [ 'x,\n  a\n", "items: [ 'x,\n  a\n"),
        ("x = 'q' and password: [a]\nname: 'z'\n", "x = 'q' and password: <redacted>\nname: 'z'\n"),
    ],
)
def test_unresolved_secret_bracket_masks_only_its_indented_continuation(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize("unit", ["token=[ 'a\n", 'token=[ "a\n', "token=[ `a\n", "token=['[' [\n", "token=[\\\n"])
def test_secret_list_scan_is_one_linear_pass(unit: str) -> None:
    def run(size: int) -> float:
        text = unit * (size // len(unit)) + _HUNTER_LIST + "x" * 3000 + " 'bob' "
        started = time.perf_counter()
        out = redact_text(text, limit=1_000_000)
        elapsed = time.perf_counter() - started
        assert "hunter2" not in out
        return elapsed

    small, large = run(4_096), run(65_536)
    assert large < 3.0
    assert large < max(small, 0.01) * 16 * 4


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "postgresql://app:Pa@ss!@%2Fvar%2Frun%2Fpostgresql/app",
            "postgresql://app:<redacted>@%2Fvar%2Frun%2Fpostgresql/app",
        ),
        (
            "postgresql://admin:Adm1n@2024!@%2Fcloudsql%2Fproj%3Aregion%3Ainst/db",
            "postgresql://admin:<redacted>@%2Fcloudsql%2Fproj%3Aregion%3Ainst/db",
        ),
        ("postgres://u:p@ss!w@[fe80::1%25eth0]:5432/db", "postgres://u:<redacted>@[fe80::1%25eth0]:5432/db"),
        ("sftp://deploy:p@ss!@files.example.com:/srv/data", "sftp://deploy:<redacted>@files.example.com:/srv/data"),
    ],
)
def test_url_userinfo_reviewer_reproductions_keep_no_password_tail(text: str, expected: str) -> None:
    assert redact_text(text) == expected


_SEPARATOR_SYMBOLS = ["!", "$", "*", "%", "&", "+", "=", "~", "a", "7"]
_HOST_KINDS = [
    "db",
    "db.example.com",
    "10.0.0.5",
    "[::1]",
    "[fe80::1%25eth0]",
    "%2Fvar%2Frun%2Fpostgresql",
    "files.example.com:/srv/data",
]


@pytest.mark.parametrize("host", _HOST_KINDS)
@pytest.mark.parametrize("tail", ["", "/app", ":5432/app", "?sslmode=require"])
@pytest.mark.parametrize("symbol", _SEPARATOR_SYMBOLS)
def test_url_password_with_at_then_symbol_is_masked_whole_for_every_host_kind(
    symbol: str, tail: str, host: str
) -> None:
    out = redact_text(f"postgres://bob:Zq9@{symbol}Kw7@{host}{tail} end")
    assert out == f"postgres://bob:<redacted>@{host}{tail} end"
    assert "Zq9" not in out
    assert "Kw7" not in out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://u:pw@host/x?mail=a@b.com", "https://u:<redacted>@b.com"),
        ("https://u:pw@host/users/@alice", "https://u:<redacted>@alice"),
        ("https://u@host/x and https://v:q@h/y", "https://u@host/x and https://v:<redacted>@h/y"),
        ("https://host:8080/api?e=a@b.com", "https://host:8080/api?e=a@b.com"),
        ("postgres://u@db/x p@ss", "postgres://u@db/x p@ss"),
        (
            "see https://a.example.com/x@y then https://u:p@h/z",
            "see https://a.example.com/x@y then https://u:<redacted>@h/z",
        ),
    ],
)
def test_url_userinfo_rightmost_separator_exact_outputs(text: str, expected: str) -> None:
    assert redact_text(text) == expected


def test_url_userinfo_rightmost_separator_is_linear_at_4k_and_64k() -> None:
    def run(n: int) -> float:
        started = time.perf_counter()
        for body in ("@%2F" * n, "@[::1%25z]" * n, "@h:/p" * n, "@!" * n):
            redact_text("postgres://bob:p" + body, limit=2_000_000)
        return time.perf_counter() - started

    assert run(4_000) < 5
    assert run(64_000) < 5


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "redis://:S3cretPw@cache.example.com:6379/0",
            "redis://:<redacted>@cache.example.com:6379/0",
        ),
        ("REDIS_URL=redis://:S3cretPw@cache:6379/0", "REDIS_URL=redis://:<redacted>@cache:6379/0"),
        ("rediss://:S3cretPw@cache:6380", "rediss://:<redacted>@cache:6380"),
    ],
)
def test_url_empty_user_reviewer_reproductions_mask_the_password(text: str, expected: str) -> None:
    assert redact_text(text) == expected


@pytest.mark.parametrize("host", ["cache", "cache.example.com", "10.0.0.5", "[::1]", "%2Fvar%2Frun%2Fredis"])
@pytest.mark.parametrize("tail", ["", "/0", ":6379/0", "?ssl=true"])
@pytest.mark.parametrize("password", ["Zq9", "Zq9@Kw7", "Zq9/Kw7", "Zq9!Kw7"])
@pytest.mark.parametrize("user", ["", "bob"])
def test_url_password_is_masked_for_empty_and_non_empty_user(user: str, password: str, tail: str, host: str) -> None:
    out = redact_text(f"redis://{user}:{password}@{host}{tail} end")
    assert out == f"redis://{user}:<redacted>@{host}{tail} end"
    assert "Zq9" not in out
    assert "Kw7" not in out


@pytest.mark.parametrize(
    "text",
    [
        "scheme://:8080",
        "scheme://:8080/x",
        "scheme://:8080?x=1",
        "scheme://:8080#frag",
        "scheme://:8080/users/@alice",
        "scheme://:8080/a/b.c",
        "tcp://::1",
        "see redis://: and more",
        "redis://cache:6379/0",
    ],
)
def test_url_without_an_empty_user_credential_is_exactly_unchanged(text: str) -> None:
    assert redact_text(text) == text


def test_url_empty_user_scan_is_linear_at_4k_and_64k() -> None:
    def run(n: int) -> float:
        started = time.perf_counter()
        for text in (
            "redis://" + ":" * n,
            "redis://:" * n,
            "redis://:p" + "@a/" * n,
            "redis://:" + "x" * n,
            "redis://:8080" + "/x" * n,
        ):
            redact_text(text, limit=2_000_000)
        return time.perf_counter() - started

    small, large = run(4_000), run(64_000)
    assert large < 5
    assert large < max(small, 0.01) * 16 * 4
