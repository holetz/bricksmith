"""The model monitoring report: derived period axes, reference values, trusted rows, escaped self-contained output."""
# ruff: noqa: PT018 - dense assertions on report rows

from __future__ import annotations

import re
from typing import Any

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.actions import action_row
from bricksmith.monitor.model.config import parse_config
from bricksmith.monitor.model.report import build_report, render_html, render_markdown

RUNS = [
    {"run_id": "r1", "model": "m", "status": "success", "finished_at": "2026-04-01", "gate": "pass"},
    {"run_id": "r2", "model": "m", "status": "partial", "finished_at": "2026-04-02", "gate": "pass"},
    {"run_id": "r3", "model": "m", "status": "failed", "finished_at": "2026-04-03", "gate": "fail"},
    {"run_id": "o1", "model": "other", "status": "success", "finished_at": "2026-04-01", "gate": "pass"},
]


def row(run: str, period: str, metric: str, value: float | None, light: str, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    family = {"psi": "score_psi", "gini": "discrimination", "row_count": "volumetry"}[metric]
    return {"model": "m", "model_version": "1", "run_id": run, "monitoring_period": period, "data_period": period,
            "segment": "", "column": "" if metric == "row_count" else "score", "metric": metric, "family": family,
            "value": value, "light": light, "rows": 500, "reason": "",
            "development_value": 0.0 if metric == "psi" else 0.6,
            "out_of_time_value": 0.02 if metric == "psi" else 0.55, **extra}  # fmt: skip


RESULTS = [
    row("r1", "2026-03", "psi", 0.05, "green"),
    row("r1", "2026-01", "psi", 0.30, "red"),  # unordered on purpose; February is missing
    row("r2", "2026-04", "psi", 0.15, "yellow"),
    row("r3", "2026-04", "psi", 0.90, "red"),  # failed run: never read
    row("r1", "2026-03", "gini", 0.58, "green"),
    row("r1", "2026-03", "row_count", 500, "green"),
    {**row("o1", "2026-03", "psi", 0.5, "red"), "model": "other"},
]
STATE = {"results": RESULTS, "runs": RUNS}


def test_the_period_axis_is_the_sorted_union_found_in_the_data() -> None:
    report = build_report(STATE, "m", generated="2026-05-01T00:00:00")
    assert report.periods == ["2026-01", "2026-03", "2026-04"]  # no hard-coded months, the gap stays a gap
    assert report.latest_period == "2026-04"
    psi = next(s for sec in report.sections for s in sec.series if s.metric == "psi")
    assert sorted(psi.points) == report.periods and psi.points["2026-04"].value == 0.15  # r2 beats r3 (failed)


def test_sections_follow_the_family_order_and_series_carry_both_references() -> None:
    report = build_report(STATE, "m")
    assert [s.family for s in report.sections] == ["score_psi", "discrimination", "volumetry"]
    psi = report.sections[0].series[0]
    assert (psi.development, psi.out_of_time) == (0.0, 0.02)
    assert report.lights == {"green": 0, "yellow": 1, "red": 0}


def test_other_models_rows_and_unknown_models() -> None:
    assert all("other" not in str(s) for s in build_report(STATE, "m").sections)
    with pytest.raises(MonitorError, match=r"no finished-run results for model 'zzz'.*m, other"):
        build_report(STATE, "zzz")


def test_alerts_show_their_human_status() -> None:
    first = build_report(STATE, "m", as_of="2026-04-10")
    target = next(a for a in first.alerts if a.severity == "critical")
    accept = action_row(model="m", alert_id=target.alert_id, action="risk_accepted", reason="known", author="ana",
                        created_at="2026-04-05T00:00:00", valid_until="2026-12-31")  # fmt: skip
    report = build_report({**STATE, "actions": [accept]}, "m", as_of="2026-04-10")
    found = next(a for a in report.alerts if a.alert_id == target.alert_id)
    assert (found.state, found.action, found.author, found.valid_until) == (
        "accepted",
        "risk_accepted",
        "ana",
        "2026-12-31",
    )
    opened = lambda r: sum(a.state == "open" for a in r.alerts)  # noqa: E731
    assert opened(report) == opened(first) - 1


def test_html_is_self_contained_and_escapes_data() -> None:
    hostile = {
        **STATE,
        "results": [*RESULTS, {**row("r1", "2026-03", "psi", 0.1, "green"), "column": "<script>x</script>"}],
    }
    text = render_html(build_report(hostile, "m", generated="2026-05-01T00:00:00"))
    assert text.startswith("<!DOCTYPE html>")
    assert "<script" not in text and "&lt;script&gt;" in text
    assert not re.search(r"(src|href)=|@import|url\(", text)  # no external asset
    assert "<svg" in text and "stroke-dasharray" in text  # reference lines
    assert text.count('type="radio"') == 5  # overview, three families, alerts
    for period in ("2026-01", "2026-03", "2026-04"):
        assert period in text and "2026-02" not in text


def test_markdown_lists_the_same_series_with_dev_and_oot_columns() -> None:
    report = build_report(STATE, "m")
    text = render_markdown(report)
    assert "| series | dev | oot | 2026-01 | 2026-03 | 2026-04 |" in text
    assert "| psi · score | 0 | 0.02 | 0.3 red |  | " not in text  # the gap is blank, not shifted
    labels = [s.label for sec in report.sections for s in sec.series]
    assert all(label.replace("|", "\\|") in text for label in labels)
    assert "## Alerts" in text


def test_a_series_without_numbers_renders_no_chart() -> None:
    state = {"results": [row("r1", "2026-01", "psi", None, "insufficient_volume", reason="too few rows")], "runs": RUNS}
    text = render_html(build_report(state, "m"))
    assert "<svg" not in text and "too few rows" in text


def _drifting_state(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    """Two red months of one metric: the default policy (red=2) opens an alert a stricter run did not."""
    results = [
        row("r1", "2026-02", "psi", 0.3, "red", period_date="2026-02-01", direction="both"),
        row("r1", "2026-03", "psi", 0.31, "red", period_date="2026-03-01", direction="both"),
    ]
    return {"results": results, "runs": RUNS[:1], **extra}


def test_report_lists_the_alerts_the_run_stored_not_a_default_policy_replay() -> None:
    replayed = build_report(_drifting_state(), "m")  # no stored alerts: the default policy replays two reds
    assert replayed.critical_alerts > 0
    stored = build_report(_drifting_state(alerts=[]), "m")  # the run, under persistence red=4, stored none
    assert (stored.alerts, stored.open_alerts, stored.critical_alerts) == ([], 0, 0)


def test_report_counts_equal_the_stored_alerts_of_the_latest_run() -> None:
    alert = {"model": "m", "run_id": "r1", "alert_id": "a1", "severity": "critical", "kind": "persistence",
             "state": "open", "monitoring_period": "2026-03", "metric": "psi"}  # fmt: skip
    stale = {**alert, "run_id": "r0", "alert_id": "old"}
    foreign = {**alert, "model": "other", "alert_id": "x"}
    report = build_report(_drifting_state(alerts=[alert, stale, foreign]), "m")
    assert [a.alert_id for a in report.alerts] == ["a1"] and report.critical_alerts == 1
    close = action_row(model="m", alert_id="a1", action="closed", reason="done", author="ana",
                       created_at="2026-04-05T00:00:00")  # fmt: skip
    closed = build_report(_drifting_state(alerts=[alert], actions=[close]), "m")
    assert closed.alerts[0].state == "closed" and closed.critical_alerts == 0


def test_report_replays_with_the_config_persistence_when_no_alerts_are_stored() -> None:
    def declared(red: int) -> Any:  # noqa: ANN401
        return parse_config({
            "model": "m", "period": "month", "score": "score",
            "references": {"development": {"start": "a", "end": "b"}},
            "limits": {"psi": {"green": 0.1, "red": 0.2}}, "persistence": {"yellow": 4, "red": red},
        })  # fmt: skip

    quiet = build_report(_drifting_state(), "m", config=declared(4))
    assert quiet.critical_alerts == 0 and not [a for a in quiet.alerts if a.kind == "persistence"]
    assert build_report(_drifting_state(), "m", config=declared(2)).critical_alerts > 0
