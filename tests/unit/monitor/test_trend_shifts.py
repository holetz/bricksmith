"""Business-day calendar and sustained-shift detectors (CUSUM, EWMA, change points) on fixed synthetic scores."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

from datetime import date

import pytest

from bricksmith.monitor import MonitorError
from bricksmith.monitor.business import BusinessCalendar, business_days
from bricksmith.monitor.limits import BOTH, GREEN, HIGHER, LOWER, RED
from bricksmith.monitor.shifts import change_point_suggested, change_points, cusum, drift_status, ewma

QUIET = [0.3, -0.4, 0.1, -0.2, 0.5, -0.3, 0.2, -0.1, 0.0, 0.4, -0.5, 0.2]


def test_a_month_counts_its_business_days() -> None:
    plain = BusinessCalendar()
    assert business_days("2025-03", plain) == 21
    assert business_days("202503", plain) == 21
    assert business_days("2025-02", plain) == 20
    holidays = BusinessCalendar(holidays=frozenset({date(2025, 3, 3), date(2025, 3, 4)}))
    assert business_days("2025-03", holidays) == 19


def test_a_day_is_one_business_day_or_none() -> None:
    cal = BusinessCalendar(holidays=frozenset({date(2025, 3, 5)}))
    assert business_days("2025-03-04", cal) == 1
    assert business_days("2025-03-05", cal) == 0
    assert business_days("20250308", cal) == 0
    assert business_days("not a period", cal) is None
    assert business_days("2025-13", cal) is None


def test_a_custom_weekend_and_a_bad_calendar() -> None:
    friday_saturday = BusinessCalendar(weekend=(4, 5))
    assert business_days("2025-03-07", friday_saturday) == 0
    assert business_days("2025-03-09", friday_saturday) == 1
    cal = BusinessCalendar.from_dict({"weekend": [5, 6], "holidays": ["2025-12-25"]})
    assert cal.to_dict() == {"holidays": ["2025-12-25"], "weekend": [5, 6]}
    for bad in ({"weekends": [5]}, {"weekend": [7]}, {"weekend": [0, 1, 2, 3, 4, 5, 6]}, {"holidays": ["x"]}, 3):
        with pytest.raises(MonitorError):
            BusinessCalendar.from_dict(bad)


def test_cusum_sees_a_one_sigma_shift_the_point_rule_never_does() -> None:
    shifted = [*QUIET, *([1.0] * 12)]
    assert max(abs(z) for z in shifted) < 3.0
    assert cusum(shifted, HIGHER) and drift_status(shifted, "cusum", HIGHER) == RED
    assert not cusum(shifted, LOWER)
    assert cusum([-z for z in shifted], LOWER) and cusum(shifted, BOTH)
    assert drift_status(QUIET, "cusum", BOTH) == GREEN


def test_ewma_sees_a_sustained_shift_and_ignores_noise() -> None:
    shifted = [*QUIET, *([1.5] * 8)]
    assert ewma(shifted, HIGHER) and drift_status(shifted, "ewma", HIGHER) == RED
    assert not ewma(shifted, LOWER)
    assert drift_status(QUIET, "ewma", BOTH) == GREEN


def test_a_detector_says_nothing_on_too_few_scores_and_rejects_bad_input() -> None:
    assert drift_status([5.0, 5.0], "cusum") == "" and drift_status(QUIET, "none") == ""
    with pytest.raises(MonitorError):
        drift_status(QUIET, "median")
    with pytest.raises(MonitorError):
        cusum(QUIET, "sideways")


def test_change_points_find_the_level_shift_and_nothing_in_noise() -> None:
    assert change_points(QUIET) == []
    shifted = [*QUIET, *([2.5, 2.1, 2.8, 2.4, 2.6, 2.2])]
    assert change_points(shifted) == [12]


def test_a_change_point_is_suggested_only_in_the_worse_direction() -> None:
    up = [*QUIET, *([2.5, 2.1, 2.8, 2.4, 2.6, 2.2])]
    assert change_point_suggested(up, HIGHER) and not change_point_suggested(up, LOWER)
    assert change_point_suggested([-z for z in up], LOWER) and change_point_suggested(up, BOTH)
    assert not change_point_suggested(QUIET, BOTH) and not change_point_suggested([1.0, 2.0], HIGHER)
