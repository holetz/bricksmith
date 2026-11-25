"""The canonical text spelling of categories and segment names (booleans lowercase, NaN null, doubles as ``str``)."""

from __future__ import annotations

from bricksmith.monitor import fit_reference
from bricksmith.monitor.bins import category_text
from bricksmith.monitor.measure import NULL_SEGMENT, measure_rows, segment_name


def test_segment_names_use_the_category_spelling() -> None:
    assert [segment_name(v) for v in (True, False, None, float("nan"), 1e7, 2, "a")] == [
        "true",
        "false",
        NULL_SEGMENT,
        NULL_SEGMENT,
        "10000000.0",
        "2",
        "a",
    ]
    assert all(segment_name(v) == category_text(v) for v in (True, False, 1e7, 1e-5, 0.1, 3, "x"))


def test_boolean_segments_are_measured_under_lowercase_names() -> None:
    rows = [{"x": float(i), "g": i % 2 == 0} for i in range(6)] + [{"x": 1.0, "g": float("nan")}]
    reference = fit_reference(rows, {"x": {"bins": 2}}, name="r", version=1, segment="g")
    segments = {m.segment for m in measure_rows(reference, rows)}
    assert segments == {"", "true", "false", NULL_SEGMENT}


def test_both_zero_signs_are_one_category() -> None:
    from bricksmith.monitor.bins import BinSpec, fit_bins  # noqa: PLC0415

    assert category_text(-0.0) == category_text(0.0) == "0.0"
    fitted = fit_bins("x", {-0.0: 2, 1.5: 1}, {"type": "categorical"})
    assert fitted.categories == ("0.0", "1.5")
    assert {fitted.assign(0.0), fitted.assign(-0.0)} == {"cat:0.0"}
    stored = BinSpec("x", "categorical", categories=("-0.0", "1.5"))
    assert {stored.assign(0.0), stored.assign(-0.0)} == {"cat:-0.0"}
    assert stored.assign(2.5) == "unseen"
