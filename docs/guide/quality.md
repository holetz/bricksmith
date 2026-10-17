# Comparing data and checking quality

Everything is planned offline and only counts and bounded samples leave the cluster.

## Compare two datasets

```python
from bricksmith.quality import Tolerance, compare, plan_for_frames

plan = plan_for_frames(before, after, ["id"], exclude=["loaded_at"], tolerances={"amount": Tolerance(absolute=0.01)})
print(plan.to_json())  # columns, tolerances and diagnostics for anything not compared
report = compare(before, after, plan, sample_limit=5)  # samples only when a limit is given
```

`plan_comparison(left_schema, right_schema, keys)` does the same from `{column: type}` mappings, and
`compare_rows` runs a plan over lists of dict rows (small local data and tests). Two values match when
`|l - r| <= max(absolute, relative * max(|l|, |r|))`; the boundary is inclusive. Nulls are equal to nulls.
Maps, binaries and similar types are reported as `unsupported_type` diagnostics, never skipped silently.

### Numeric severity (opt-in)

```python
report = compare(before, after, plan, sample_limit=5, numeric_severity=True)
```

By default a comparison reports mismatch counts and key-ordered samples, and nothing else changes. With
`numeric_severity=True` each numeric column gets `max_abs_deviation`, the largest finite `|l - r|` among its
mismatches (null when no mismatch is eligible, never zero for "nothing"), and, only with an explicit
`sample_limit`, its mismatch samples are ordered by largest deviation, ties by key ascending, each carrying
`abs_deviation`. Pairs with a null, non-numeric, bool, NaN or infinite operand, or a non-finite difference, keep
their mismatch count but are excluded from the maximum and ranked last. Floating columns use the double
difference; integer and `DECIMAL` columns are exact, and a pair needing more than 38 digits raises `QualityError`
instead of rounding. Values are plain decimal strings with trailing zeros removed. Matching pairs, including
those inside a tolerance, are never sampled. Reports without the option serialize exactly as before and old
reports still load.

Duplicate or null keys make the report `invalid` (no value comparison, never a one-to-one result).

## Checks

```python
from bricksmith.quality import Check, require_check
from bricksmith.tasks import task


@task(kind="check")
def check_orders(ctx):
    orders = ctx.read("orders")
    require_check(Check.grain("id"), orders)
    require_check(Check.accepted_values("status", ["open", "closed"]), orders)
```

Kinds (constructors): `Check.grain`, `Check.cardinality`, `Check.not_null` (kind `nullability`), `Check.accepted_values`, `Check.completeness`, `Check.freshness` (its `max_age_seconds` must be a finite, non-negative number; NaN, infinity and negatives raise `QualityError`). `run_check`
returns `passed`, `failed` or `unavailable` (with a reason) instead of raising; `require_check` raises
unless passed. As write conditions use `condition_sql(Check.not_null("id"))` in the `input_checks` or
`target_checks` metadata (`relation=` names the target for dataset-level checks).

With `Check.cardinality(..., require_match=True)`, a data row with a null in any key column counts as unmatched and fails the check, since a null key can never match.

## Regression gate

```bash
bricksmith quality compare --left c.s.before --right c.s.after --key id --tolerance amount=abs:0.01 --json
bricksmith quality check --table c.s.orders --checks checks.toml --report-out report.json
```

Exit code 0 means pass, 4 means the gate failed. `--policy policy.json` sets thresholds
(`max_missing_keys`, `max_extra_keys`, `max_differing_rows`, `max_differing_ratio`, `allow_unavailable`).
The JSON report is stable and carries a fingerprint, so the same inputs give identical reports.
Checks files are JSON lists or TOML `[[check]]` tables with `kind`, `columns` and the kind's options.
