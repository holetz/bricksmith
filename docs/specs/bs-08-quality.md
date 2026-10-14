# BS-08. Data quality, comparison and regression evidence

Slice of [Epic 0001](../epics/0001-bricksmith-library.md); [user guide](../guide/quality.md).

## Decisions closed

**Plan first.** `plan_comparison(left_schema, right_schema, keys, ...)` is pure: schemas are `{column: type}`
strings. It returns a serializable `ComparisonPlan` (keys, compared columns with tolerances, diagnostics).
Columns are selected explicitly (`columns`) or implicitly (all shared non-key columns minus `exclude`).
Nothing is skipped silently: unsupported types (map, binary, interval, variant, void), incompatible types
and one-sided columns become diagnostics, `error` when selected explicitly (the plan is not executable)
and `warning` when selected implicitly (the column is excluded and reported). Numeric types of different
width compare; any other type difference is `type_mismatch`.

**Tolerance.** Values match when `|l - r| <= max(absolute, relative * max(|l|, |r|))`: inclusive boundary,
symmetric, zero means exact. Null equals null, null never equals a value, NaN equals NaN. The pure
implementation uses exact decimal arithmetic; Spark uses doubles, so boundary cases are only reliable with
exactly representable operands (documented, tested with such values).

**Validity.** Duplicate keys and null keys (any key column null) on either side, or a blocked plan, make the
report `invalid`: value comparison is skipped and counts of matches are zero. A one-to-one result is never
reported for such data, and the gate always fails an invalid comparison.

**Bounded results.** Counts are always complete. Samples (missing/extra keys, per-column mismatches with
key, left and right value) appear only for an explicit `sample_limit` of 1..1000, per category.

**Checks.** `Check` kinds: `grain`, `cardinality` (`expect`, `require_match`), `nullability`,
`accepted_values` (`allow_null`), `completeness` (`min_ratio`; an empty dataset is unavailable) and
`freshness` (`max_age_seconds`, inclusive; future values are fresh; dates are midnight UTC in the row
implementation and in the session time zone on Spark). `run_check` returns `passed`/`failed`/`unavailable`
with a reason (missing column, missing second relation, empty or non-temporal data). As a task of kind
`check`, `require_check` raises `CheckFailedError` (`quality_check_failed`) unless passed. As a write
condition, `condition_sql` renders the SQL boolean for `input_checks`/`target_checks`: row predicates for
nullability and accepted values, scalar subqueries over `{input}` (or a quoted target) for dataset checks.
A condition cannot be unavailable; missing data makes it false.

**Gate.** `build_regression_report(name, comparisons=, checks=, policy=)` applies a `GatePolicy`
(thresholds for missing/extra keys and differing rows or ratio, `allow_unavailable`). The JSON has sorted
keys, no timestamps or environment details and a SHA-256 `fingerprint` of its body, which `from_dict`
verifies. Verdict `fail` exits with the validation code (4), `pass` with 0; an empty report fails.

## Public contract

`bricksmith.quality`: `plan_comparison`, `plan_for_frames`, `schema_of`, `ComparisonPlan`, `Tolerance`,
`Diagnostic`, `compare`, `compare_rows`, `values_equal`, `ComparisonReport`, `Check`, `CheckResult`,
`run_check`, `evaluate_checks`, `require_check`, `condition_sql`, `load_checks`, `GatePolicy`,
`RegressionReport`, `build_regression_report`, errors `QualityError`, `CheckFailedError`.
CLI: `bricksmith quality compare|check` (`--json`, `--sample-limit`, `--report-out`, `--policy`).

## Deviations

`pyproject.toml` ignores `S608` under `src/bricksmith/quality` like storage (SQL from validated identifiers
and quoted literals). The CLI group is appended to `GROUPS`.

## Acceptance tests

`tests/unit/quality/`: identical/changed/missing/extra rows, invalid keys, empty datasets, tolerance at and
beyond the boundary, null-safe equality, unsupported types, each check kind, bounded samples, gate and CLI.
`tests/remote/test_quality.py`: see [evidence](../evidence/bs-08-free.md).
