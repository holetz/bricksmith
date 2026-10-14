"""Data quality: comparison planning and execution, checks, regression reports and gates."""

from bricksmith.quality.checks import (
    FAILED,
    PASSED,
    UNAVAILABLE,
    Check,
    CheckResult,
    condition_sql,
    evaluate_checks,
    load_checks,
    require_check,
    run_check,
)
from bricksmith.quality.compare import compare, plan_for_frames
from bricksmith.quality.errors import CheckFailedError, QualityError
from bricksmith.quality.plan import (
    ColumnPlan,
    ComparisonPlan,
    Diagnostic,
    Tolerance,
    plan_comparison,
    schema_of,
)
from bricksmith.quality.regression import GatePolicy, RegressionReport, build_regression_report
from bricksmith.quality.report import ColumnResult, ComparisonReport, SideStats
from bricksmith.quality.rows import compare_rows, values_equal

__all__ = [
    "FAILED",
    "PASSED",
    "UNAVAILABLE",
    "Check",
    "CheckFailedError",
    "CheckResult",
    "ColumnPlan",
    "ColumnResult",
    "ComparisonPlan",
    "ComparisonReport",
    "Diagnostic",
    "GatePolicy",
    "QualityError",
    "RegressionReport",
    "SideStats",
    "Tolerance",
    "build_regression_report",
    "compare",
    "compare_rows",
    "condition_sql",
    "evaluate_checks",
    "load_checks",
    "plan_comparison",
    "plan_for_frames",
    "require_check",
    "run_check",
    "schema_of",
    "values_equal",
]
