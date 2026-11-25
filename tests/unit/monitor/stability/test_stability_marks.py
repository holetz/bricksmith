"""The stability marks core: validation, latest-per-selector ordering, expiry and the marks in force."""
# ruff: noqa: PT018 - dense assertions on rows

from __future__ import annotations

from typing import Any

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.marks import (
    ACCEPTED,
    EXCLUDED,
    INVALID,
    REGIME_CHANGE,
    UNMARKED,
    MarkSet,
    effective_marks,
    latest_marks,
    mark_row,
    marks_digest,
)


def _mark(mark: str, period: str = "2026-03", at: str = "2026-04-01T00:00:00+00:00", **extra: str) -> dict[str, Any]:
    return mark_row(
        dataset="orders", period=period, mark=mark, reason="known load", author="ana", created_at=at, **extra
    )


def test_a_mark_row_is_validated_and_deterministic() -> None:
    row = _mark(ACCEPTED, metric="psi", valid_until="2026-06-30")
    assert row == _mark(ACCEPTED, metric="psi", valid_until="2026-06-30")
    assert row["valid_until"] == "2026-06-30" and row["created_at"] == "2026-04-01T00:00:00+00:00"
    assert _mark(ACCEPTED)["mark_id"] != _mark(ACCEPTED, at="2026-04-02T00:00:00+00:00")["mark_id"]


@pytest.mark.parametrize(
    "bad",
    [
        {"mark": "nope"},
        {"reason": " "},
        {"author": ""},
        {"period": ""},
        {"created_at": "yesterday"},
        {"valid_until": "soon"},
        {"mark": EXCLUDED, "metric": "psi"},
        {"mark": INVALID, "segment": "region=north"},
    ],
)
def test_invalid_marks_are_refused(bad: dict[str, str]) -> None:
    fields = {"dataset": "orders", "period": "2026-03", "mark": ACCEPTED, "reason": "r", "author": "a",
              "created_at": "2026-04-01T00:00:00+00:00"}  # fmt: skip
    with pytest.raises(MonitorError):
        mark_row(**{**fields, **bad})  # type: ignore[arg-type]


def test_the_latest_row_of_a_selector_wins_and_unmark_cancels() -> None:
    old = _mark(EXCLUDED, at="2026-04-01T00:00:00+00:00")
    new = _mark(REGIME_CHANGE, at="2026-04-02T00:00:00+00:00")
    assert latest_marks([new, old])[("2026-03", "", "", "")]["mark"] == REGIME_CHANGE
    cancel = _mark(UNMARKED, at="2026-04-03T00:00:00+00:00")
    assert effective_marks([old, new, cancel], "2026-05-01") == []


def test_a_mark_stops_holding_after_valid_until_inclusive() -> None:
    row = _mark(ACCEPTED, valid_until="2026-05-01")
    assert effective_marks([row], "2026-05-01") == [row]
    assert effective_marks([row], "2026-05-02") == []


def test_effective_marks_read_only_the_dataset_and_periods_since() -> None:
    other = mark_row(dataset="x", period="2026-03", mark=EXCLUDED, reason="r", author="a",
                     created_at="2026-04-01T00:00:00+00:00")  # fmt: skip
    rows = [_mark(EXCLUDED), _mark(EXCLUDED, period="2026-01"), other]
    assert [m["period"] for m in effective_marks(rows, "2026-05-01", "orders", since="2026-02")] == ["2026-03"]


def test_the_mark_set_indexes_period_wide_marks_and_picks_the_most_specific() -> None:
    wide = _mark(EXCLUDED, period="2026-02")
    regime = _mark(REGIME_CHANGE, period="2026-04")
    bad = _mark(INVALID, period="2026-05")
    broad = _mark(ACCEPTED)
    narrow = _mark(ACCEPTED, metric="psi", at="2026-04-02T00:00:00+00:00")
    marks = MarkSet(tuple(effective_marks([wide, regime, bad, broad, narrow], "2026-06-01")))
    assert marks.excluded == {"2026-02", "2026-05"} and marks.regimes == {"2026-04"}
    assert marks.invalid == {"2026-05": "known load"}
    row = {"period": "2026-03", "segment": "", "column": "amount", "metric": "psi"}
    assert marks.of(row) == narrow and marks.accepted(row)
    assert marks.of({**row, "metric": "mean"}) == broad
    assert marks.of({**row, "period": "2026-09"}) is None and not MarkSet()


def test_the_digest_is_empty_without_marks_and_changes_with_each_mark() -> None:
    assert marks_digest([]) == []
    one, two = _mark(EXCLUDED), _mark(EXCLUDED, at="2026-04-09T00:00:00+00:00")
    assert marks_digest([one]) != marks_digest([two])
