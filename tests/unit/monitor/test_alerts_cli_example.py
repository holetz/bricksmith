"""Alert rules, unavailable states, persistence rows, the outside-project adapter, the CLI and the example graph."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import csv
import importlib.util
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.cli.main import main
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION
from bricksmith.graph import load_graph
from bricksmith.monitor import (
    AlertRule,
    MonitorError,
    alert_events,
    evaluate,
    evaluate_stored,
    fit_reference,
    load_reference,
    measure,
    measurement_rows,
    monitor_frames,
    reference_row,
    resolve_window,
)
from bricksmith.ops import AlertEvent, AlertNotifier, ListSink, classify_error  # noqa: F401
from bricksmith.ops.notify import InMemoryAlertStore

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.monitor import Reference

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "shared_schema"
POLICY = [
    {"name": "drift", "metric": "psi", "op": ">", "threshold": 0.25, "severity": "critical", "min_count": 4},
    {"name": "weak", "metric": "auc", "op": "<", "threshold": 0.6, "severity": "warning"},
    {"name": "seasoned", "metric": "auc", "op": "<", "threshold": 0.9, "min_history": 2},
]


def reference() -> Reference:
    fit = [{"s": v, "y": int(v > 0.5), "d": "2026-01-10", "g": "a" if v < 0.5 else "b"} for v in (0.1, 0.3, 0.6, 0.9)]
    return fit_reference(
        fit, {"s": {"edges": [0, 0.5, 1]}}, name="m", version=2, score="s", label="y", time="d", segment="g",
        alert_policy=POLICY, evaluation_window={"column": "d", "days": 7},
    )  # fmt: skip


SHIFTED = [{"s": 0.9, "y": 1, "d": "2026-02-03", "g": "b"}, {"s": 0.95, "y": 0, "d": "2026-02-04", "g": "b"}] * 3


def states(evals: list) -> dict[str, str]:
    return {e.rule: e.state for e in evals if e.segment == ""}


def test_synthetic_shift_raises_the_expected_alert_with_ops_contract() -> None:
    ref = reference()
    window = {"column": "d", "start": "2026-02-01", "end": "2026-02-08"}
    report = monitor_frames(SHIFTED, reference=ref, window=window, segments=False)
    # Reference (.5, .5); current (0, 1): (1e-4-.5) ln(1e-4/.5) + (1-.5) ln 2 -> far above 0.25.
    drift = next(m for m in report.measurements if m.metric == "psi")
    assert drift.value == pytest.approx((1e-4 - 0.5) * math.log(2e-4) + 0.5 * math.log(2))
    (event,) = [a for a in report.alerts if "psi" in a.subject]
    assert isinstance(event, AlertEvent) and event.error_class == "statistical_alert" and event.severity == "critical"
    assert event.kind == "monitor_threshold" and event.target == "m" and event.occurred_at == "2026-02-08"
    assert classify_error({"class": event.error_class}) == "statistical_alert"
    # The ops notifier accepts it unchanged and deduplicates on the stable key.
    delivered: list[dict[str, Any]] = []

    class Sink:
        name = "list"

        def deliver(self, payload: Mapping[str, Any]) -> None:
            delivered.append(dict(payload))

    notifier = AlertNotifier([Sink()], InMemoryAlertStore())
    assert [r.status for r in notifier.notify(event)] == ["delivered"]
    assert [r.status for r in notifier.notify(event)] == ["suppressed"]
    assert len(delivered) == 1
    assert states(list(report.evaluations))["drift"] == "alert"


def test_no_shift_means_no_alert_and_boundary_is_strict() -> None:
    ref = reference()
    same = [{"s": v, "y": int(v > 0.5), "d": "2026-02-03", "g": "a"} for v in (0.1, 0.3, 0.6, 0.9)]
    report = monitor_frames(same, reference=ref, window={"column": "d", "start": "2026-02-01", "end": "2026-02-08"})
    assert states(list(report.evaluations))["drift"] == "ok" and not report.alerts
    rule = AlertRule("r", "psi", ">", 0.5)
    assert not rule.breached(0.5) and AlertRule("r", "psi", ">=", 0.5).breached(0.5)


def test_missing_labels_and_insufficient_states_are_visible_never_alerts() -> None:
    ref = reference()
    unlabeled = [{"s": 0.9, "y": None, "d": "2026-02-03", "g": "b"}] * 3
    report = monitor_frames(unlabeled, reference=ref, segments=False)
    got = {e.rule: e for e in report.evaluations}
    assert got["weak"].state == "unavailable" and got["weak"].reason == "missing_labels" and got["weak"].value is None
    assert got["drift"].state == "insufficient_count" and "3 < min_count 4" in got["drift"].reason
    assert not report.alerts
    labeled = [{"s": 0.55, "y": 1, "d": "d", "g": "a"}, {"s": 0.45, "y": 0, "d": "d", "g": "a"}]
    measured = measure(ref, labeled, segments=False)
    assert states(evaluate(measured, ref.alert_policy, {}))["seasoned"] == "insufficient_history"
    history = {("auc", "s", ""): [1.0, 1.0]}
    assert states(evaluate(measured, ref.alert_policy, history))["seasoned"] == "ok"  # AUC 1.0 is not < 0.9
    assert "unavailable" in report.to_text() and "missing_labels" in report.to_text()


def test_segments_are_opt_in_for_rules() -> None:
    ref = reference()
    out = measure(ref, [*SHIFTED, {"s": 0.1, "y": 0, "d": "2026-02-03", "g": "a"}])
    assert {m.segment for m in out} == {"", "a", "b"}
    assert {e.segment for e in evaluate(out, ref.alert_policy)} == {""}
    wide = [AlertRule("w", "row_count", "<", 100, segment="*")]
    assert {e.segment for e in evaluate(out, wide)} == {"", "a", "b"}


def test_rule_validation_and_windows() -> None:
    with pytest.raises(MonitorError):
        AlertRule("x", "psi", "!=", 1)
    with pytest.raises(MonitorError, match="unknown alert rule keys"):
        AlertRule.from_dict({"name": "x", "metric": "psi", "op": ">", "threshold": 1, "bogus": 1})
    assert resolve_window(reference(), "2026-02-08") == {"column": "d", "end": "2026-02-08", "start": "2026-02-01"}
    assert alert_events(reference(), []) == []


def test_rows_roundtrip_and_stored_evaluation_uses_earlier_windows_as_history() -> None:
    ref = reference()
    stored_refs = [reference_row(ref)]
    assert load_reference(stored_refs, "m").fingerprint == ref.fingerprint
    with pytest.raises(MonitorError, match="no stored reference"):
        load_reference(stored_refs, "nope")
    rows: list[dict[str, Any]] = []
    good = [{"s": v, "y": int(v > 0.5), "d": "2026-02-03", "g": "a"} for v in (0.1, 0.3, 0.6, 0.9)]
    for start, end, data in (("2026-02-01", "2026-02-08", good), ("2026-02-08", "2026-02-15", good)):
        window = {"column": "d", "start": start, "end": end}
        rows += measurement_rows(ref, window, measure(ref, [{**r, "d": start} for r in data], window, segments=False))
    assert len({tuple(r[k] for k in ("window_start", "metric", "subject", "segment")) for r in rows}) == len(rows)
    evaluations, alerts = evaluate_stored(ref, rows)
    seasoned = {r["window_start"]: r["state"] for r in evaluations if r["rule"] == "seasoned"}
    assert seasoned == {
        "2026-02-01": "insufficient_history",
        "2026-02-08": "insufficient_history",
    }  # 0 and 1 priors < 2
    assert alerts == []
    again = evaluate_stored(ref, rows)
    assert again == (evaluations, alerts)  # deterministic: reruns produce identical rows
    shifted_rows = measurement_rows(
        ref, {"column": "d", "start": "2026-03-01", "end": "2026-03-08"}, measure(ref, SHIFTED, segments=False)
    )
    _, fired = evaluate_stored(ref, rows + shifted_rows)
    # The shifted window has AUC 0 (the positive scores below the negative) and two earlier windows of history.
    assert sorted((a["rule"], a["severity"], a["error_class"], a["occurred_at"]) for a in fired) == [
        ("drift", "critical", "statistical_alert", "2026-03-08"),
        ("seasoned", "warning", "statistical_alert", "2026-03-08"),
        ("weak", "warning", "statistical_alert", "2026-03-08"),
    ]


def test_outside_project_adapter_fits_and_compares_lists() -> None:
    baseline = [{"x": v, "plan": "a"} for v in range(1, 9)]
    report = monitor_frames(
        [{"x": 100, "plan": "z"}] * 4,
        baseline=baseline,
        columns={"x": {"bins": 2}, "plan": {"type": "categorical"}},
        name="adhoc",
        version=1,
        alert_policy=[{"name": "c", "metric": "csi", "op": ">", "threshold": 0.1}],
    )
    assert {a.details["rule"] for a in report.alerts} == {"c"} and len(report.alerts) == 2
    with pytest.raises(MonitorError, match="pass a reference"):
        monitor_frames(baseline)
    assert json.loads(json.dumps(report.to_dict()))["reference"]["name"] == "adhoc"


def test_cli_fit_compare_evaluate_text_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "fit.csv"
    with data.open("w", newline="", encoding="utf-8") as handle:
        out = csv.writer(handle)
        out.writerow(["x", "plan", "d"])
        out.writerows((v, "a", "2026-01-05") for v in range(1, 9))
        out.writerow(["", "a", "2026-01-06"])
    config = tmp_path / "cfg.json"
    config.write_text(
        json.dumps({
            "name": "cli", "version": 1, "time": "d",
            "columns": {"x": {"bins": 4}, "plan": {"type": "categorical"}},
            "alert_policy": [{"name": "d", "metric": "csi", "op": ">", "threshold": 0.25}],
        }),
        encoding="utf-8",
    )  # fmt: skip
    ref_path = tmp_path / "ref.json"
    assert main(["monitor", "fit", "--config", str(config), "--data", str(data), "--out", str(ref_path)]) == EXIT_OK
    saved = json.loads(ref_path.read_text(encoding="utf-8"))
    assert saved["columns"][0]["counts"]["null"] == 1 and saved["fit_rows"] == 9 and saved["fit_source"] == "fit.csv"
    capsys.readouterr()
    cur = tmp_path / "cur.jsonl"
    cur.write_text("\n".join(json.dumps({"x": 99, "plan": "b", "d": "2026-02-01"}) for _ in range(4)), encoding="utf-8")
    base = ["monitor", "compare", "--reference", str(ref_path), "--data", str(cur)]
    assert main([*base, "--window-start", "2026-02-01", "--window-end", "2026-03-01"]) == EXIT_OK
    assert "csi" in capsys.readouterr().out
    assert main(["--json", *base]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["alerts"] == []  # compare never evaluates rules
    evaluate_args = ["monitor", "evaluate", "--reference", str(ref_path), "--data", str(cur)]
    assert main(["--json", *evaluate_args]) == EXIT_OK
    assert len(json.loads(capsys.readouterr().out)["alerts"]) == 2
    assert main([*evaluate_args, "--fail-on-alert"]) == EXIT_VALIDATION
    assert main([*evaluate_args, "--table", "t"]) == 2  # exactly one source


def load_example_module() -> Any:  # noqa: ANN401
    spec = importlib.util.spec_from_file_location("example_monitoring", EXAMPLE / "catalogs/demo/ml/monitoring.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_example_graph_has_feature_model_and_monitor_tasks_in_one_schema() -> None:
    graph = load_graph(EXAMPLE)
    assert graph.ok
    by_name = {t.name: t for t in graph.tasks}
    assert {t.schema for t in graph.tasks} == {"ml"} and {t.catalog for t in graph.tasks} == {"demo"}
    assert by_name["customer_features"].kind == "feature" and by_name["churn_training"].kind == "training_dataset"
    assert {n for n, t in by_name.items() if t.kind == "monitor"} == {
        "monitor_references",
        "monitor_measurements",
        "monitor_alerts",
    }

    def deps(name: str) -> set[str]:
        return {graph.task(d).name for d in graph.dependencies(by_name[name].id)}

    assert deps("monitor_references") == {"customer_features", "monitor_predictions"}
    assert deps("monitor_measurements") == {"customer_features", "monitor_predictions", "monitor_references"}
    assert deps("monitor_alerts") == {"monitor_references", "monitor_measurements"}
    assert {o.rsplit(".", 1)[-1] for o in by_name["monitor_alerts"].outputs} == {
        "monitor_evaluations",
        "monitor_alerts",
    }


def test_example_data_raises_the_expected_alerts_with_the_pure_engine() -> None:
    mod = load_example_module()
    rows = [
        {"snapshot_date": day, "score": s, "churned": y, "plan": mod.PLANS[i]}
        for day, scores, labels in (
            ("2026-01-31", mod.JAN_SCORES, mod.JAN_LABELS),
            ("2026-03-31", mod.MAR_SCORES, mod.MAR_LABELS),
        )
        for i, (s, y) in enumerate(zip(scores, labels, strict=True))
    ]
    columns = {"score": {"edges": [0, 0.25, 0.5, 0.75, 1.0]}}
    ref = fit_reference(
        rows, columns, name="churn_scores", version=1, score="score", label="churned", segment="plan",
        time="snapshot_date", fit_window=mod.JAN, alert_policy=mod.SCORE_RULES, evaluation_window=mod.MARCH,
    )  # fmt: skip
    # March bins (right-closed): .5 -> bin_1; .6 and .7 -> bin_2; .8 .85 .9 .95 .99 -> bin_3: counts [0, 1, 2, 5].
    assert ref.column("score").counts["bin_0"] == 2
    report = monitor_frames(rows, reference=ref, window=mod.MARCH)
    got = {e.rule: e for e in report.evaluations if e.segment == ""}
    assert got["score_drift"].state == "alert" and got["weak_ranking"].state in {"ok", "alert"}
    assert got["auc_trend"].state == "insufficient_history"
    auc = next(m for m in report.measurements if m.metric == "auc" and m.segment == "")
    assert auc.count == 4  # only the four matured labels
