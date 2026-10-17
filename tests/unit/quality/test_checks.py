"""Checks on row data, their SQL conditions and use as tasks or write conditions."""

# ruff: noqa: PT018 - dense assertions in synthetic tests

from __future__ import annotations

import datetime as dt

import pytest

from bricksmith.quality import (
    Check,
    CheckFailedError,
    QualityError,
    condition_sql,
    evaluate_checks,
    load_checks,
    require_check,
    run_check,
)
from bricksmith.storage import parse_write_spec

ROWS = [
    {"id": 1, "kind": "a", "seen": dt.datetime(2026, 1, 1, tzinfo=dt.UTC), "opt": 1},
    {"id": 2, "kind": "b", "seen": dt.datetime(2025, 12, 31, tzinfo=dt.UTC), "opt": None},
    {"id": 3, "kind": "z", "seen": None, "opt": None},
]
NOW = dt.datetime(2026, 1, 2, tzinfo=dt.UTC)


def test_grain_passes_and_fails_with_hand_counted_findings() -> None:
    assert run_check(Check.grain("id"), ROWS).status == "passed"
    assert run_check(Check.grain("id"), []).status == "passed"
    dup = [*ROWS, {"id": 1}, {"id": 1}, {"id": None}]
    result = run_check(Check.grain("id"), dup, sample_limit=5)
    assert result.status == "failed"
    assert result.observed == {"duplicate_groups": 1, "duplicate_rows": 3, "null_key_rows": 1, "rows": 6}
    assert result.samples == ({"id": 1},)
    assert run_check(Check.grain("id"), dup).samples == ()


def test_cardinality_expectations() -> None:
    right = [{"id": 1}, {"id": 2}, {"id": 2}]
    one_to_many = Check.cardinality("id", expect="one_to_many")
    assert run_check(one_to_many, ROWS, right).status == "passed"
    assert run_check(Check.cardinality("id", expect="many_to_one"), ROWS, right).status == "failed"
    both = run_check(Check.cardinality("id"), ROWS, right)
    assert both.status == "failed" and both.observed["other_invalid_groups"] == 1
    assert run_check(Check.cardinality("id", expect="many_to_many"), ROWS, right).status == "passed"
    matched = Check.cardinality("id", expect="one_to_many", require_match=True)
    result = run_check(matched, ROWS, right, sample_limit=3)
    assert result.status == "failed" and result.observed["unmatched_rows"] == 1 and result.samples == ({"id": 3},)
    assert run_check(Check.cardinality("id"), ROWS).status == "unavailable"


def test_nullability_accepted_values_and_completeness() -> None:
    assert run_check(Check.not_null("id", "kind"), ROWS).status == "passed"
    failed = run_check(Check.not_null("opt", "id"), ROWS)
    assert failed.status == "failed" and failed.observed["nulls"] == {"opt": 2, "id": 0}
    accepted = Check.accepted_values("kind", ["a", "b"])
    result = run_check(accepted, ROWS, sample_limit=5)
    assert result.status == "failed" and result.observed["invalid_rows"] == 1 and result.samples == ("z",)
    assert run_check(Check.accepted_values("opt", [1]), ROWS).observed["invalid_rows"] == 2  # nulls are not accepted
    assert run_check(Check.accepted_values("opt", [1], allow_null=True), ROWS).status == "passed"
    assert run_check(Check.completeness("opt"), ROWS).status == "failed"  # 1 of 3 non-null
    assert run_check(Check.completeness("opt", min_ratio=1 / 3), ROWS).status == "passed"  # exactly at the minimum
    empty = run_check(Check.completeness("id"), [])
    assert empty.status == "unavailable" and "empty" in empty.reason


def test_freshness_boundary_and_unavailable() -> None:
    day = 86400.0  # newest value is one day before NOW
    assert run_check(Check.freshness("seen", max_age_seconds=day), ROWS, now=NOW).status == "passed"
    assert run_check(Check.freshness("seen", max_age_seconds=day - 1), ROWS, now=NOW).status == "failed"
    assert (
        run_check(Check.freshness("seen", max_age_seconds=1), ROWS, now=dt.datetime(2025, 1, 1, tzinfo=dt.UTC)).status
        == "passed"
    )
    dates = [{"d": dt.date(2026, 1, 1)}]
    assert run_check(Check.freshness("d", max_age_seconds=day), dates, now=NOW).status == "passed"
    assert run_check(Check.freshness("seen", max_age_seconds=1), [], now=NOW).status == "unavailable"
    assert run_check(Check.freshness("kind", max_age_seconds=1), ROWS, now=NOW).status == "unavailable"
    with pytest.raises(QualityError):
        run_check(Check.freshness("seen", max_age_seconds=1), ROWS, now=dt.datetime(2026, 1, 2))  # noqa: DTZ001


def test_missing_column_is_unavailable_with_a_reason() -> None:
    result = run_check(Check.grain("nope"), ROWS)
    assert result.status == "unavailable" and "nope" in result.reason


def test_declaration_validation_and_round_trip() -> None:
    for bad in (
        lambda: Check("bogus", "x", ("a",)),
        Check.grain,
        lambda: Check.accepted_values("a", []),
        lambda: Check.cardinality("a", expect="x"),
        lambda: Check.completeness("a", min_ratio=2),
        lambda: Check.from_dict({"kind": "grain", "columns": ["a"], "extra": 1}),
    ):
        with pytest.raises(QualityError):
            bad()
    loaded = load_checks(
        '[{"kind": "grain", "columns": "id"}, {"kind": "accepted_values", "columns": ["kind"], "values": ["a"]}]'
    )
    assert [c.name for c in loaded] == ["grain(id)", "accepted_values(kind)"]
    from_toml = load_checks('[[check]]\nkind = "nullability"\ncolumns = ["a"]\n', fmt="toml")
    assert from_toml[0].kind == "nullability"
    results = evaluate_checks(loaded, ROWS)
    assert [r.status for r in results] == ["passed", "failed"]


def test_checks_as_ordinary_tasks_raise_unless_they_pass() -> None:
    assert require_check(Check.grain("id"), ROWS).passed
    with pytest.raises(CheckFailedError, match="failed"):
        require_check(Check.not_null("opt"), ROWS)
    with pytest.raises(CheckFailedError, match="unavailable"):
        require_check(Check.grain("nope"), ROWS)
    assert require_check(Check.grain("nope"), ROWS, allow_unavailable=True).status == "unavailable"


def test_conditions_render_sql_for_the_storage_hooks() -> None:
    assert condition_sql(Check.not_null("id", "kind")) == "`id` IS NOT NULL AND `kind` IS NOT NULL"
    assert condition_sql(Check.accepted_values("kind", ["a", "b"])) == "coalesce(`kind` IN ('a', 'b'), false)"
    assert condition_sql(Check.accepted_values("kind", ["a"], allow_null=True)) == "(`kind` IN ('a') OR `kind` IS NULL)"
    assert condition_sql(Check.grain("id", "kind")) == (
        "(SELECT count(*) FROM (SELECT 1 FROM {input} GROUP BY `id`, `kind` HAVING count(*) > 1 "
        "OR `id` IS NULL OR `kind` IS NULL)) = 0"
    )
    assert "count(`id`) / count(*)" in condition_sql(Check.completeness("id", min_ratio=0.5), relation="`c`.`s`.`t`")
    assert "unix_timestamp(current_timestamp()) - 3600" in condition_sql(Check.freshness("seen", max_age_seconds=3600))
    card = condition_sql(Check.cardinality("id", require_match=True), other="`o`")
    assert "LEFT ANTI JOIN `o` AS r ON l.`id` = r.`id`" in card and card.count(") = 0") == 3
    with pytest.raises(QualityError):
        condition_sql(Check.cardinality("id"))
    spec = parse_write_spec(
        {
            "write": "overwrite",
            "input_checks": [condition_sql(Check.not_null("id")), condition_sql(Check.grain("id"))],
            "target_checks": [condition_sql(Check.grain("id"), relation="`c`.`s`.`t`")],
        }
    )
    assert len(spec.input_checks) == 2 and len(spec.target_checks) == 1


def test_required_match_fails_null_data_keys() -> None:
    required = Check.cardinality("id", expect="many_to_one", require_match=True)
    single = run_check(required, [{"id": None}], [{"id": 1}], sample_limit=3)
    assert single.status == "failed" and single.observed["unmatched_rows"] == 1
    assert single.samples == ({"id": None},)
    both = run_check(required, [{"id": None}], [{"id": None}])
    assert both.status == "failed" and both.observed["unmatched_rows"] == 1
    mixed = run_check(required, [{"id": None}, {"id": 1}, {"id": 2}], [{"id": 1}], sample_limit=3)
    assert mixed.observed["unmatched_rows"] == 2 and mixed.samples == ({"id": None}, {"id": 2})
    assert run_check(required, [{"id": None}, {"id": 2}], [{"id": 1}], sample_limit=1).samples == ({"id": None},)
    assert run_check(required, [{"id": None}], [{"id": 1}]).samples == ()
    empty = run_check(required, [{"id": None}], [])
    assert empty.status == "failed" and empty.observed["unmatched_rows"] == 1
    composite = Check.cardinality("a", "b", expect="many_to_one", require_match=True)
    partial = run_check(composite, [{"a": 1, "b": None}], [{"a": 1, "b": 2}])
    assert partial.status == "failed" and partial.observed["unmatched_rows"] == 1
    assert run_check(required, [{"id": 1}], [{"id": 1}]).status == "passed"


def test_null_data_keys_without_require_match_keep_cardinality_behavior() -> None:
    result = run_check(Check.cardinality("id", expect="many_to_one"), [{"id": None}], [{"id": 1}])
    assert result.observed["unmatched_rows"] == 1
    assert result.observed["data_invalid_groups"] == 1 and result.status == "passed"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -1, True, "x"])
def test_freshness_rejects_invalid_threshold(bad: object) -> None:
    with pytest.raises(QualityError):
        Check.freshness("seen", max_age_seconds=bad)  # type: ignore[arg-type]
    with pytest.raises(QualityError):
        Check.from_dict({"kind": "freshness", "columns": ["seen"], "max_age_seconds": bad})


@pytest.mark.parametrize("bad", ["nan", "inf", None])
def test_freshness_from_dict_rejects_nonfinite_text(bad: object) -> None:
    with pytest.raises(QualityError):
        Check.from_dict({"kind": "freshness", "columns": ["seen"], "max_age_seconds": bad})


@pytest.mark.parametrize("ok", [0, 0.0, 1, 86400.5, 1e300])
def test_freshness_accepts_finite_non_negative_threshold(ok: float) -> None:
    assert Check.freshness("seen", max_age_seconds=ok).max_age_seconds == ok
    assert Check.from_dict({"kind": "freshness", "columns": ["seen"], "max_age_seconds": ok}).max_age_seconds == ok


def test_freshness_rejects_threshold_too_large_for_float() -> None:
    huge = 10**400
    with pytest.raises(QualityError):
        Check.freshness("seen", max_age_seconds=huge)
    with pytest.raises(QualityError):
        Check.from_dict({"kind": "freshness", "columns": ["seen"], "max_age_seconds": huge})
    with pytest.raises(QualityError):
        load_checks('[{"kind": "freshness", "columns": ["seen"], "max_age_seconds": ' + str(huge) + "}]")


def test_truncated_samples_order_by_value_like_spark() -> None:
    required = Check.cardinality("a", expect="many_to_many", require_match=True)
    nums = run_check(required, [{"a": 10}, {"a": 2}], [{"a": 1}], sample_limit=1)
    assert nums.samples == ({"a": 2},)
    strs = run_check(required, [{"a": "zed's"}, {"a": "apple"}], [{"a": "x"}], sample_limit=1)
    assert strs.samples == ({"a": "apple"},)
    grain = run_check(Check.grain("a"), [{"a": 10}, {"a": 10}, {"a": 2}, {"a": 2}], sample_limit=1)
    assert grain.samples == ({"a": 2},)
    accepted = run_check(Check.accepted_values("a", ["ok"]), [{"a": 10}, {"a": 2}, {"a": None}], sample_limit=2)
    assert accepted.samples == (None, 2)
