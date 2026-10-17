"""One entry point that compares Spark DataFrames or row lists under a plan."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.quality.plan import ComparisonPlan, Tolerance, plan_comparison, schema_of
from bricksmith.quality.rows import compare_rows

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.quality.report import ComparisonReport


def is_frame(data: object) -> bool:
    """Tell whether ``data`` looks like a Spark DataFrame."""
    return hasattr(data, "groupBy") and hasattr(data, "schema")


def compare(
    left: Any,  # noqa: ANN401
    right: Any,  # noqa: ANN401
    plan: ComparisonPlan,
    *,
    sample_limit: int | None = None,
    numeric_severity: bool = False,
) -> ComparisonReport:
    """Compare two Spark DataFrames (remotely) or two row lists under ``plan``.

    ``numeric_severity`` (off by default) adds the largest finite absolute deviation per numeric column and orders
    mismatch samples (which still need ``sample_limit``) by it.
    """
    if is_frame(left) and is_frame(right):
        from bricksmith.quality.spark import compare_frames  # noqa: PLC0415

        return compare_frames(left, right, plan, sample_limit=sample_limit, numeric_severity=numeric_severity)
    if is_frame(left) or is_frame(right):
        msg = "compare two Spark DataFrames or two row lists, not a mix"
        raise TypeError(msg)
    return compare_rows(left, right, plan, sample_limit=sample_limit, numeric_severity=numeric_severity)


def plan_for_frames(  # noqa: PLR0913 - mirrors plan_comparison
    left: Any,  # noqa: ANN401
    right: Any,  # noqa: ANN401
    keys: Sequence[str],
    *,
    columns: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
    tolerances: Mapping[str, Tolerance] | None = None,
    default_tolerance: Tolerance | None = None,
    left_name: str = "left",
    right_name: str = "right",
) -> ComparisonPlan:
    """Plan a comparison from the schemas of two Spark DataFrames."""
    return plan_comparison(
        schema_of(left),
        schema_of(right),
        keys,
        columns=columns,
        exclude=exclude,
        tolerances=tolerances,
        default_tolerance=default_tolerance,
        left=left_name,
        right=right_name,
    )
