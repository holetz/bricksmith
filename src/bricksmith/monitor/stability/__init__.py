"""Data stability: watch a table period by period against a baseline, declared on the task that writes it.

A task declares ``metadata={"stability": {"period": "...", ...}}``; the plan lists the stability tables as
declared targets, and the runner profiles the written output, judges it with traffic lights, a trend check
and persistence alerts, writes the results and applies the declared gate (warn by default, or fail the task).
The engine also runs on any rows or DataFrame through :func:`check`. Spark loads only where it is used.
"""

from bricksmith.monitor.stability.backtest import BacktestReport, backtest
from bricksmith.monitor.stability.config import Baseline, Gate, StabilityConfig, parse_config, parse_declarations
from bricksmith.monitor.stability.engine import (
    CheckResult,
    MemoryStore,
    Outcome,
    StabilityGateError,
    Store,
    check,
)
from bricksmith.monitor.stability.tables import KEYS, SCHEMAS, view_statements

__all__ = [
    "KEYS",
    "SCHEMAS",
    "BacktestReport",
    "Baseline",
    "CheckResult",
    "Gate",
    "MemoryStore",
    "Outcome",
    "StabilityConfig",
    "StabilityGateError",
    "Store",
    "backtest",
    "check",
    "parse_config",
    "parse_declarations",
    "view_statements",
]
