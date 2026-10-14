"""Comparison planning and the pure reference comparison, with hand-calculated expectations."""
# ruff: noqa: PT018 - dense assertions in synthetic tests

from __future__ import annotations

import pytest

from bricksmith.quality import (
    ComparisonPlan,
    ComparisonReport,
    QualityError,
    Tolerance,
    compare_rows,
    plan_comparison,
    values_equal,
)

SCHEMA = {"id": "int", "name": "string", "score": "double"}


def plan(**options: object) -> ComparisonPlan:
    return plan_comparison(SCHEMA, SCHEMA, ["id"], **options)  # type: ignore[arg-type]


LEFT = [
    {"id": 1, "name": "a", "score": 1.0},
    {"id": 2, "name": "b", "score": 2.0},
    {"id": 3, "name": "c", "score": None},
    {"id": 4, "name": None, "score": 4.0},
]
RIGHT = [
    {"id": 1, "name": "a", "score": 1.0},  # identical
    {"id": 2, "name": "B", "score": 2.5},  # name and score changed
    {"id": 3, "name": "c", "score": None},  # null equals null
    {"id": 5, "name": "e", "score": 5.0},  # extra; id 4 is missing
]


def test_changed_missing_and_extra_rows_have_hand_derived_counts() -> None:
    report = compare_rows(LEFT, RIGHT, plan())
    assert report.status == "different"
    assert (report.left.rows, report.right.rows) == (4, 4)  # type: ignore[union-attr]
    assert (report.missing_in_right, report.extra_in_right, report.matched_keys) == (1, 1, 3)
    assert report.rows_with_differences == 1
    assert {c.name: c.mismatches for c in report.columns} == {"name": 1, "score": 1}
    assert report.samples == {}  # nothing unless asked


def test_identical_and_empty_datasets() -> None:
    same = compare_rows(LEFT, list(reversed(LEFT)), plan())
    assert same.status == "identical" and same.matched_keys == 4
    both_empty = compare_rows([], [], plan())
    assert both_empty.status == "identical" and both_empty.matched_keys == 0
    one_empty = compare_rows([], RIGHT, plan())
    assert one_empty.status == "different" and one_empty.extra_in_right == 4
    assert compare_rows(LEFT, [], plan()).missing_in_right == 4


def test_null_safe_values() -> None:
    assert values_equal(None, None)
    assert not values_equal(None, 0)
    assert not values_equal("", None)
    assert values_equal(float("nan"), float("nan"))
    assert not values_equal(float("nan"), 1.0)
    assert not values_equal(float("inf"), 1.0, Tolerance(absolute=1e9))
    assert values_equal(float("inf"), float("inf"))


def test_duplicate_keys_never_give_a_one_to_one_result() -> None:
    dup = [*LEFT, {"id": 1, "name": "x", "score": 9.0}]
    report = compare_rows(dup, RIGHT, plan())
    assert report.status == "invalid"
    assert report.left.duplicate_key_groups == 1 and report.left.duplicate_key_rows == 2  # type: ignore[union-attr]
    assert report.matched_keys == 0 and report.columns == () and report.rows_with_differences == 0
    assert any("duplicate" in r for r in report.reasons)
    other_side = compare_rows(LEFT, [*RIGHT, RIGHT[0]], plan())
    assert other_side.status == "invalid" and other_side.right.duplicate_key_rows == 2  # type: ignore[union-attr]


def test_null_keys_invalidate() -> None:
    report = compare_rows([*LEFT, {"id": None, "name": "n", "score": 0.0}], RIGHT, plan())
    assert report.status == "invalid" and report.left.null_key_rows == 1  # type: ignore[union-attr]
    assert any("null key" in r for r in report.reasons)
    two = plan_comparison({"a": "int", "b": "int", "v": "int"}, {"a": "int", "b": "int", "v": "int"}, ["a", "b"])
    rows = [{"a": 1, "b": None, "v": 1}]
    assert compare_rows(rows, rows, two).status == "invalid"  # a partly null composite key is a null key


def test_composite_keys() -> None:
    two = plan_comparison({"a": "int", "b": "int", "v": "int"}, {"a": "int", "b": "int", "v": "int"}, ["a", "b"])
    left = [{"a": 1, "b": 1, "v": 1}, {"a": 1, "b": 2, "v": 2}]
    right = [{"a": 1, "b": 1, "v": 1}, {"a": 1, "b": 2, "v": 3}]
    report = compare_rows(left, right, two)
    assert report.status == "different" and report.matched_keys == 2 and report.rows_with_differences == 1


@pytest.mark.parametrize(
    ("left", "right", "tolerance", "expected"),
    [
        (1.0, 1.5, Tolerance(absolute=0.5), True),  # exactly at the absolute boundary
        (1.0, 1.5000001, Tolerance(absolute=0.5), False),  # just beyond
        (3, 4, Tolerance(relative=0.25), True),  # allowed = 0.25 * max(3, 4) = 1, difference 1
        (3, 4.0001, Tolerance(relative=0.25), False),  # allowed 1.000025, difference 1.0001
        (1.0, 1.4, Tolerance(absolute=0.1, relative=0.5), True),  # allowed = max(0.1, 0.7)
        (1.0, 1.0, Tolerance(), True),
        (1.0, 1.0000001, Tolerance(), False),  # zero tolerance is exact
        (0.1, 0.3, Tolerance(absolute=0.2), True),  # decimal arithmetic: exactly 0.2, not 0.19999999999999998
        ("a", "b", Tolerance(absolute=5), False),
        (True, 2, Tolerance(absolute=5), False),
    ],
)
def test_tolerance_boundaries_are_inclusive(left: object, right: object, tolerance: Tolerance, expected: bool) -> None:  # noqa: FBT001
    assert values_equal(left, right, tolerance) is expected
    assert values_equal(right, left, tolerance) is expected  # symmetric


def test_tolerance_is_validated() -> None:
    for bad in (-1.0, float("nan"), float("inf")):
        with pytest.raises(QualityError):
            Tolerance(absolute=bad)


def test_tolerance_applies_per_column_in_a_comparison() -> None:
    chosen = plan(tolerances={"score": Tolerance(absolute=0.5)})
    report = compare_rows(LEFT, RIGHT, chosen)
    assert {c.name: c.mismatches for c in report.columns} == {"name": 1, "score": 0}  # 2.0 vs 2.5 sits on the boundary
    tight = plan(tolerances={"score": Tolerance(absolute=0.25)})
    assert {c.name: c.mismatches for c in compare_rows(LEFT, RIGHT, tight).columns}["score"] == 1


def test_bounded_samples_only_when_requested() -> None:
    left = [{"id": i, "name": "x", "score": 0.0} for i in range(1, 6)]
    right = [{"id": i, "name": "y", "score": 0.0} for i in range(1, 6)] + [{"id": 9, "name": "z", "score": 0.0}]
    report = compare_rows(left, right, plan(), sample_limit=2)
    assert report.columns[0].mismatches == 5  # counts are complete
    assert len(report.samples["mismatch:name"]) == 2  # samples are bounded
    assert report.samples["mismatch:name"][0] == {"key": {"id": 1}, "left": "x", "right": "y"}
    assert report.samples["extra_in_right"] == [{"id": 9}] and report.samples["missing_in_right"] == []
    assert compare_rows(left, right, plan()).samples == {}
    for bad in (0, -1, 1001, True, 1.5):
        with pytest.raises(QualityError):
            compare_rows(left, right, plan(), sample_limit=bad)  # type: ignore[arg-type]


def test_plan_selection_exclusion_and_unsupported_types_are_diagnosed() -> None:
    left = {
        "id": "int",
        "a": "int",
        "m": "map<string,int>",
        "b": "binary",
        "arr": "array<int>",
        "s": "string",
        "x": "int",
    }
    right = {
        "id": "bigint",
        "a": "double",
        "m": "map<string,int>",
        "b": "binary",
        "arr": "array<int>",
        "s": "int",
        "y": "int",
    }
    auto = plan_comparison(left, right, ["id"], exclude=["arr"])
    assert [c.name for c in auto.columns] == ["a"]  # int vs double are both numeric; id int vs bigint too
    codes = {(d.code, d.column): d.severity for d in auto.diagnostics}
    assert codes == {
        ("unsupported_type", "m"): "warning",
        ("unsupported_type", "b"): "warning",
        ("type_mismatch", "s"): "warning",
        ("column_only_left", "x"): "warning",
        ("column_only_right", "y"): "warning",
    }
    assert auto.executable  # warnings do not block
    explicit = plan_comparison(left, right, ["id"], columns=["a", "m", "ghost"])
    assert not explicit.executable
    assert {(d.code, d.column) for d in explicit.diagnostics if d.severity == "error"} == {
        ("unsupported_type", "m"),
        ("column_missing", "ghost"),
    }
    blocked = compare_rows([], [], explicit)
    assert blocked.status == "invalid" and blocked.left is None and blocked.reasons


def test_plan_key_diagnostics() -> None:
    assert {d.code for d in plan_comparison(SCHEMA, SCHEMA, []).diagnostics} == {"no_keys"}
    assert {d.code for d in plan_comparison(SCHEMA, SCHEMA, ["nope"]).diagnostics} == {"key_missing"}
    assert {d.code for d in plan_comparison({"k": "map<string,int>"}, {"k": "map<string,int>"}, ["k"]).diagnostics} == {
        "key_unsupported_type"
    }
    assert {d.code for d in plan_comparison({"k": "int"}, {"k": "string"}, ["k"]).diagnostics} == {"key_type_mismatch"}
    assert "key_selected_as_value" in {d.code for d in plan(columns=["id"]).diagnostics}
    assert "tolerance_ignored" in {d.code for d in plan(tolerances={"name": Tolerance(absolute=1)}).diagnostics}
    assert "tolerance_unused" in {
        d.code for d in plan(columns=["name"], tolerances={"score": Tolerance(absolute=1)}).diagnostics
    }


def test_plan_and_report_round_trip_with_stable_json() -> None:
    chosen = plan(tolerances={"score": Tolerance(relative=0.25)}, left="before", right="after")
    assert ComparisonPlan.from_dict(chosen.to_dict()) == chosen
    assert chosen.to_json() == ComparisonPlan.from_dict(chosen.to_dict()).to_json()
    report = compare_rows(LEFT, RIGHT, chosen, sample_limit=3)
    rebuilt = ComparisonReport.from_dict(report.to_dict())
    assert rebuilt.to_json() == report.to_json()
    assert "different" in report.to_text() and "name" in report.to_text()
    with pytest.raises(QualityError):
        ComparisonPlan.from_dict({**chosen.to_dict(), "version": 99})
