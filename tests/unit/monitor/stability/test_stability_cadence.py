"""Expected cadence: the declaration, the calendar, missing and late periods, baseline gaps and settling."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.stability import MemoryStore, check, parse_config
from bricksmith.monitor.stability.cadence import (
    build_calendar,
    expected_latest,
    gap_between,
    is_due,
    missing_starts,
    period_end,
    period_text_format,
    settling_periods,
    step,
)
from bricksmith.monitor.stability.tables import merge_rows


def _rows(months: list[str], count: int = 40) -> list[dict[str, Any]]:
    return [{"month": m, "x": float(i % 4)} for m in months for i in range(count)]


def _cfg(**overrides: object):  # noqa: ANN202 - test helper
    base = {"period": "month", "columns": {"x": {"edges": [0, 1.5, 3]}}, "min_rows": 0, "cadence": "month"}
    return parse_config({**base, **overrides})


def _run(rows, cfg, store=None, as_of="2026-04-10"):  # noqa: ANN001, ANN202 - test helper
    store = store or MemoryStore()
    found = check(rows, cfg, store, dataset="d", run_id="r1", now="2026-04-10T00:00:00+00:00", as_of=as_of)
    return found, {(r["period"], r["segment"], r["column"], r["metric"]): r for r in found.tables["results"]}


def test_config_defaults_keep_serialized_forms() -> None:
    plain = parse_config({"period": "month"})
    declared = parse_config({"period": "month", "cadence": "month", "expected_by": 5, "settle_periods": 1})
    assert plain.cadence == "none" and plain.expected_by == 0
    for key in ("cadence", "expected_by", "settle_periods", "settle_days"):
        assert key not in plain.to_dict()
    assert declared.to_dict()["cadence"] == "month" and declared.to_dict()["expected_by"] == 5
    assert plain.binning_fingerprint() == declared.binning_fingerprint()


def test_config_rejects_bad_cadence_values() -> None:
    with pytest.raises(MonitorError, match="cadence"):
        parse_config({"period": "month", "cadence": "hourly"})
    with pytest.raises(MonitorError, match="expected_by"):
        parse_config({"period": "month", "expected_by": 3})
    with pytest.raises(MonitorError, match="expected_by"):
        parse_config({"period": "month", "cadence": "day", "expected_by": -1})


def test_calendar_steps_and_period_ends() -> None:
    assert step(date(2026, 1, 31), "month") == date(2026, 2, 28)
    assert step(date(2026, 12, 1), "month") == date(2027, 1, 1)
    assert step(date(2026, 3, 1), "week", -1) == date(2026, 2, 22)
    assert period_end(date(2026, 2, 1), "month") == date(2026, 2, 28)
    assert period_end(date(2026, 2, 2), "week") == date(2026, 2, 8)
    assert period_end(date(2026, 2, 2), "day") == date(2026, 2, 2)


def test_calendar_writes_missing_periods_in_the_data_format() -> None:
    for sample, text in (("2026-01", "2026-02"), ("202601", "202602"), ("2026-01-01", "2026-02-01")):
        assert period_text_format(sample)(date(2026, 2, 1)) == text


def test_calendar_refuses_periods_that_are_not_dates() -> None:
    with pytest.raises(MonitorError, match="not a date"):
        build_calendar(_cfg(), ["q1", "q2"])


def test_calendar_expected_latest_respects_expected_by() -> None:
    cfg = _cfg(expected_by=5)
    cal = build_calendar(cfg, ["2026-01", "2026-02"])
    assert not is_due(date(2026, 3, 1), cfg, date(2026, 4, 5))
    assert is_due(date(2026, 3, 1), cfg, date(2026, 4, 6))
    assert expected_latest(cal, cfg, date(2026, 4, 5)) == date(2026, 2, 1)
    assert expected_latest(cal, cfg, date(2026, 4, 6)) == date(2026, 3, 1)


def test_calendar_finds_gaps_and_counts_them() -> None:
    cfg = _cfg()
    cal = build_calendar(cfg, ["2026-01", "2026-04"])
    missing, last = missing_starts(cal, cfg, date(2026, 5, 15))
    assert missing == [date(2026, 2, 1), date(2026, 3, 1)] and last == date(2026, 4, 1)
    assert gap_between(cal, date(2026, 1, 1), date(2026, 4, 1)) == 2


def test_calendar_settling_by_periods_and_days() -> None:
    periods = ["2026-01", "2026-02", "2026-03"]
    cfg = _cfg(settle_periods=2)
    assert settling_periods(cfg, periods, date(2026, 4, 10)) == {"2026-02", "2026-03"}
    cfg = _cfg(settle_days=20)
    cal = build_calendar(cfg, periods)
    assert settling_periods(cfg, periods, date(2026, 4, 10), cal) == {"2026-03"}


def test_a_monthly_table_missing_its_latest_month_is_late_and_red() -> None:
    cfg = _cfg(expected_by=5, gate={"fail_on": ["red"]})
    found, results = _run(_rows(["2026-01", "2026-02"]), cfg, as_of="2026-04-10")
    late = results[("2026-03", "", "", "period_late")]
    assert late["status"] == "red" and late["value"] == 1.0 and late["period_date"] == "2026-03-01"
    assert found.outcome.status == "failed" and found.outcome.latest_period == "2026-03"


def test_before_expected_by_the_latest_month_is_not_late() -> None:
    cfg = _cfg(expected_by=15, gate={"fail_on": ["red"]})
    found, results = _run(_rows(["2026-01", "2026-02"]), cfg, as_of="2026-04-10")
    assert not any(k[3] in ("period_late", "period_missing") for k in results)
    assert found.outcome.status == "passed"


def test_a_gap_month_is_missing_and_baselines_record_the_gap() -> None:
    cfg = _cfg(baseline="previous")
    _, results = _run(_rows(["2026-01", "2026-03", "2026-04"]), cfg, as_of="2026-05-10")
    assert results[("2026-02", "", "", "period_missing")]["status"] == "red"
    assert results[("2026-03", "", "", "volume_change")]["baseline_gap"] == 1
    assert results[("2026-04", "", "", "volume_change")]["baseline_gap"] == 0
    assert ("2026-02", "", "", "period_late") not in results


def test_a_late_arrival_turns_the_stored_red_green() -> None:
    cfg = _cfg()
    store = MemoryStore()
    found, _ = _run(_rows(["2026-01", "2026-03"]), cfg, store, as_of="2026-04-10")
    store.tables = {k: merge_rows(k, store.tables.get(k, []), v) for k, v in found.tables.items()}
    _, results = _run(_rows(["2026-01", "2026-02", "2026-03"]), cfg, store, as_of="2026-04-10")
    assert results[("2026-02", "", "", "period_missing")]["status"] == "green"
    assert results[("2026-02", "", "", "period_missing")]["value"] == 0.0


def test_without_a_cadence_nothing_changes() -> None:
    cfg = parse_config({"period": "month", "columns": {"x": {"edges": [0, 1.5, 3]}}, "min_rows": 0})
    found, results = _run(_rows(["2026-01", "2026-03"]), cfg)
    assert not any(k[3] in ("period_late", "period_missing") for k in results)
    assert all(r["baseline_gap"] == 0 and r["settling"] is False for r in results.values())
    assert found.outcome.latest_period == "2026-03"


def test_settling_periods_are_judged_but_kept_out_of_baselines_and_the_gate() -> None:
    cfg = _cfg(settle_periods=1, baseline="previous", gate={"fail_on": ["red"]})
    rows = _rows(["2026-01", "2026-02"], 40) + _rows(["2026-03"], 4)
    found, results = _run(rows, cfg, as_of="2026-04-02")
    settling = results[("2026-03", "", "", "volume_change")]
    assert settling["settling"] is True and settling["status"] == "red"
    assert results[("2026-02", "", "", "volume_change")]["settling"] is False
    assert found.outcome.red == 0 and found.outcome.status != "failed"
    # a period ending within settle_days is never a baseline: April is judged against February, not March
    more = _cfg(settle_days=40, baseline="previous")
    _, later = _run(rows + _rows(["2026-04"], 40), more, as_of="2026-05-02")
    assert later[("2026-04", "", "", "volume_change")]["baseline_rows"] == 40.0
    assert later[("2026-03", "", "", "volume_change")]["settling"] is True
    assert later[("2026-02", "", "", "volume_change")]["settling"] is False
