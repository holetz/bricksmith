"""The selection funnel: six optional steps over the split samples, recorded in a per-variable ledger.

Each step evaluates only the variables still in play, writes one ledger entry per variable and metric (value,
threshold, rule, decision, reason) and removes the variables it drops. ``judgment`` runs last and may drop or
reinstate a variable with a declared reason. The ledger is the audit trail: rerunning the same configuration
over the same data reproduces it.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.selection.config import (
    BINARY,
    CRITICAL,
    JUDGMENT,
    MONOTONE,
    MULTIVARIATE,
    QUALITY,
    REDUNDANCY,
    STABILITY,
    STEPS,
    UNIVARIATE,
    SelectionConfig,
    fail,
    parse_config,
)
from bricksmith.mlops.selection.metrics import (
    WoeTable,
    correlation_matrix,
    discrimination,
    eta,
    psi_critical,
    quality_metrics,
    rate_groups,
    vif,
    woe_table,
)
from bricksmith.mlops.selection.summary import CATEGORICAL, NUMERIC, Engine, RowsEngine
from bricksmith.monitor.bins import category_text, count_bins, fit_bins
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stats import psi

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

PASS = "pass"  # noqa: S105 - a ledger decision, not a secret
DROP = "drop"
INFO = "info"
KEEP = "keep"
MAX = "max"
MIN = "min"
SIGN = "sign"
NONZERO = "nonzero"
FLOOR = "floor"
MANUAL = "manual"


@dataclass(frozen=True)
class LedgerEntry:
    """One variable, step and metric: the value, the threshold and rule it was judged by, and the decision."""

    variable: str
    step: str
    metric: str
    value: float | None
    threshold: float | None
    rule: str
    decision: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {
            "decision": self.decision,
            "metric": self.metric,
            "reason": self.reason,
            "rule": self.rule,
            "step": self.step,
            "step_order": STEPS.index(self.step),
            "threshold": self.threshold,
            "value": self.value,
            "variable": self.variable,
        }


@dataclass(frozen=True)
class VariableOutcome:
    """Where a variable ended: selected, or the step it left at and why."""

    variable: str
    kind: str
    selected: bool
    left_at: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {"kind": self.kind, "left_at": self.left_at, "reason": self.reason, "selected": self.selected,
                "variable": self.variable}  # fmt: skip


@dataclass(frozen=True)
class SelectionResult:
    """The ledger, the per-variable outcomes, the final feature set and the development-fitted WoE tables."""

    config: SelectionConfig
    ledger: tuple[LedgerEntry, ...]
    variables: tuple[VariableOutcome, ...]
    selected: tuple[str, ...]
    woe: Mapping[str, WoeTable] = field(default_factory=dict)
    development_rows: int = 0
    out_of_time_rows: int = 0

    def ledger_rows(self) -> list[dict[str, Any]]:
        """Return the ledger as plain rows."""
        return [e.to_dict() for e in self.ledger]

    def variable_rows(self) -> list[dict[str, Any]]:
        """Return the per-variable outcomes as plain rows."""
        return [v.to_dict() for v in self.variables]

    def dropped_by_step(self) -> dict[str, int]:
        """Return how many variables left at each step."""
        counts: dict[str, int] = dict.fromkeys(STEPS, 0)
        for outcome in self.variables:
            if not outcome.selected:
                counts[outcome.left_at] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        """Return the plain, JSON-serializable form."""
        return {
            "config": self.config.to_dict(),
            "development_rows": self.development_rows,
            "ledger": self.ledger_rows(),
            "name": self.config.name,
            "out_of_time_rows": self.out_of_time_rows,
            "selected": list(self.selected),
            "variables": self.variable_rows(),
            "woe": {k: t.to_dict() for k, t in sorted(self.woe.items())},
        }


def apply_woe(
    rows: Iterable[Mapping[str, Any]], tables: Mapping[str, WoeTable], *, suffix: str = "_woe"
) -> list[dict[str, Any]]:
    """Return new rows with ``<variable><suffix>`` WoE columns added; the input rows are not changed.

    The tables are fitted on development: use them to train and score models, never to write feature tables.
    """
    return [{**row, **{f"{v}{suffix}": t.woe_of(row.get(v)) for v, t in tables.items()}} for row in rows]


def _judge(value: float | None, threshold: float | None, rule: str) -> bool:
    if value is None or threshold is None:
        return False
    if rule == MAX:
        return value > threshold
    return value <= threshold if rule in (FLOOR, NONZERO) else value < threshold


def _reason(metric: str, value: float, threshold: float, rule: str) -> str:
    side = {MAX: "above the maximum", FLOOR: "at or below", NONZERO: "at or below"}.get(rule, "below the minimum")
    return f"{metric} {value:.6g} {side} {threshold:.6g}"


class _Run:
    def __init__(self, config: SelectionConfig, engine: Engine) -> None:
        self.config = config
        self.engine = engine
        self.candidates = config.candidates(engine.columns)
        named = {*config.judgment.keep, *config.judgment.drop, *config.expected_signs}
        unknown = sorted(named - set(self.candidates))
        if unknown:
            fail(f"judgment or expected_signs name variables that are not candidates: {', '.join(unknown)}")
        self.kinds = engine.kinds(self.candidates)
        self.ledger: list[LedgerEntry] = []
        self.left: dict[str, tuple[str, str]] = {}
        self.strength: dict[str, float] = {}
        self.woe: dict[str, WoeTable] = {}
        self._counts: dict[str, dict[str, dict[Any, int]]] = {}

    @property
    def alive(self) -> list[str]:
        return [v for v in self.candidates if v not in self.left]

    def counts(self, variable: str) -> dict[str, dict[Any, int]]:
        if variable not in self._counts:
            self._counts[variable] = self.engine.closing_counts(variable)
        return self._counts[variable]

    def entry(  # noqa: PLR0913, PLR0917 - one argument per ledger column
        self,
        variable: str,
        step: str,
        metric: str,
        value: float | None,
        threshold: float | None = None,
        rule: str = "",
        *,
        decision: str = "",
        reason: str = "",
    ) -> None:
        if not decision:
            if value is None or threshold is None:
                decision = INFO
            elif _judge(value, threshold, rule):
                decision, reason = DROP, reason or _reason(metric, value, threshold, rule)
            else:
                decision = PASS
        self.ledger.append(LedgerEntry(variable, step, metric, value, threshold, rule, decision, reason))
        if decision == DROP and variable not in self.left:
            self.left[variable] = (step, reason)

    def merged(self, variable: str, *, out_of_time: bool) -> dict[Any, int]:
        window = self.config.out_of_time if out_of_time else self.config.development
        total: dict[Any, int] = {}
        for closing, values in self.counts(variable).items():
            if window is not None and window.contains(closing):
                for value, count in values.items():
                    total[value] = total.get(value, 0) + count
        return total

    def quality(self) -> None:
        options = self.config.quality
        for variable in self.alive:
            metrics = quality_metrics(self.merged(variable, out_of_time=False))
            self.entry(variable, QUALITY, "missing_share", metrics["missing_share"], options.max_missing, MAX)
            self.entry(variable, QUALITY, "constant_share", metrics["constant_share"], options.max_constant, MAX)
            if self.kinds[variable] == CATEGORICAL:
                limit = None if options.max_categories is None else float(options.max_categories)
                self.entry(variable, QUALITY, "cardinality", metrics["cardinality"], limit, MAX)

    def bin_config(self, variable: str, bins: int, max_categories: int) -> dict[str, Any]:
        if self.kinds[variable] == NUMERIC:
            return {"type": "numeric", "method": "quantile", "bins": bins}
        return {"type": "categorical", "max_categories": max_categories}

    def psi_threshold(self, reference: Mapping[str, int], current: Mapping[str, int]) -> float | None:
        options = self.config.stability
        if options.psi_max != CRITICAL:
            return options.psi_max  # type: ignore[return-value]
        bins = sum(1 for k in set(reference) | set(current) if reference.get(k, 0) or current.get(k, 0))
        return psi_critical(bins, sum(reference.values()), sum(current.values()), options.alpha)

    def stability(self) -> None:
        options = self.config.stability
        for variable in self.alive:
            try:
                spec = fit_bins(
                    variable,
                    self.merged(variable, out_of_time=False),
                    self.bin_config(variable, options.bins, options.max_categories),
                )
            except MonitorError as exc:
                self.entry(variable, STABILITY, "psi_oot", None, decision=DROP, reason=f"cannot bin: {exc}")
                continue
            if self.config.out_of_time:
                current = count_bins(spec, self.merged(variable, out_of_time=True))
                if sum(current.values()):
                    value = psi(spec.counts, current)
                    self.entry(variable, STABILITY, "psi_oot", value, self.psi_threshold(spec.counts, current), MAX)
            if options.month_over_month:
                self.month_over_month(variable, spec)

    def month_over_month(self, variable: str, spec: Any) -> None:  # noqa: ANN401 - a fitted BinSpec
        by_closing = {c: count_bins(spec, values) for c, values in sorted(self.counts(variable).items())}
        closings = [c for c in sorted(by_closing) if sum(by_closing[c].values())]
        worst: tuple[float, float | None] | None = None
        for previous, current in itertools.pairwise(closings):
            value = psi(by_closing[previous], by_closing[current])
            limit = self.psi_threshold(by_closing[previous], by_closing[current])
            excess = value - (limit if limit is not None else 0.0)
            if worst is None or excess > worst[0] - (worst[1] if worst[1] is not None else 0.0):
                worst = (value, limit)
        if worst is not None:
            self.entry(variable, STABILITY, "psi_mom_max", worst[0], worst[1], MAX)

    def univariate(self) -> None:
        for variable in self.alive:
            if self.config.target_kind == BINARY:
                self.binary_power(variable)
            else:
                self.continuous_power(variable)

    def binary_power(self, variable: str) -> None:
        options = self.config.univariate
        by_value = self.engine.class_counts(variable)
        numeric = self.kinds[variable] == NUMERIC
        if numeric:
            groups = {float(v): c for v, c in by_value.items() if v is not None}
        else:
            groups = rate_groups(self._by_category(by_value))
        power = discrimination(groups)
        direction = power["direction"] if power else 0.0
        try:
            spec = fit_bins(
                variable,
                {v: e + n for v, (e, n) in by_value.items()},
                self.bin_config(variable, options.bins, options.max_categories),
            )
        except MonitorError as exc:
            self.entry(variable, UNIVARIATE, "iv", None, decision=DROP, reason=f"cannot bin: {exc}")
            return
        per_bin: dict[str, tuple[int, int]] = {}
        for value, (events, non_events) in by_value.items():
            key = spec.assign(value)
            e, n = per_bin.get(key, (0, 0))
            per_bin[key] = (e + events, n + non_events)
        table = woe_table(
            spec,
            per_bin,
            smoothing=options.smoothing,
            monotone=options.binning == MONOTONE,
            increasing=direction >= 0,
        )
        self.woe[variable] = table
        self.strength[variable] = table.iv
        self.entry(variable, UNIVARIATE, "iv", table.iv, options.min_iv, MIN)
        self.entry(variable, UNIVARIATE, "ks", power["ks"] if power else None, options.min_ks, MIN)
        self.entry(variable, UNIVARIATE, "gini", power["gini"] if power else None, options.min_gini, MIN)
        if numeric:
            self.sign(variable, direction)

    @staticmethod
    def _by_category(by_value: Mapping[Any, tuple[int, int]]) -> dict[str, tuple[int, int]]:
        merged: dict[str, tuple[int, int]] = {}
        for value, (events, non_events) in by_value.items():
            if value is None:
                continue
            key = category_text(value)
            e, n = merged.get(key, (0, 0))
            merged[key] = (e + events, n + non_events)
        return merged

    def continuous_power(self, variable: str) -> None:
        options = self.config.univariate
        if self.kinds[variable] == NUMERIC:
            rho = self.engine.rank_moments(variable).correlation()
            strength = None if rho is None else abs(rho)
            self.strength[variable] = strength or 0.0
            self.entry(variable, UNIVARIATE, "spearman", strength, options.min_spearman, MIN)
            self.sign(variable, 0.0 if rho is None else (1.0 if rho > 0 else -1.0 if rho < 0 else 0.0))
            return
        moments = {k: v for k, v in self.engine.target_moments(variable).items() if k is not None}
        value = eta(moments)
        self.strength[variable] = value or 0.0
        self.entry(variable, UNIVARIATE, "eta", value, options.min_eta, MIN)

    def sign(self, variable: str, direction: float) -> None:
        expected = self.config.expected_signs.get(variable)
        if expected is None:
            return
        wanted = 1.0 if expected == "+" else -1.0
        if direction == 0:
            self.entry(variable, UNIVARIATE, "direction", direction, wanted, SIGN, decision=INFO,
                       reason="no direction to compare with the expected sign")  # fmt: skip
        elif direction != wanted:
            found = "+" if direction > 0 else "-"
            reason = f"direction {found} disagrees with the expected {expected}"
            self.entry(variable, UNIVARIATE, "direction", direction, wanted, SIGN, decision=DROP, reason=reason)
        else:
            self.entry(variable, UNIVARIATE, "direction", direction, wanted, SIGN, decision=PASS)

    def ranked_numerics(self) -> list[str]:
        numerics = [v for v in self.alive if self.kinds[v] == NUMERIC]
        order = {v: i for i, v in enumerate(self.candidates)}
        return sorted(numerics, key=lambda v: (-self.strength.get(v, 0.0), order[v]))

    def redundancy(self) -> None:
        options = self.config.redundancy
        for variable in self.alive:
            if self.kinds[variable] == CATEGORICAL:
                self.entry(variable, REDUNDANCY, "max_correlation", None, decision=INFO,
                           reason="categorical: not evaluated")  # fmt: skip
        ranked = self.ranked_numerics()
        pairs = [(a, b) for i, a in enumerate(ranked) for b in ranked[i + 1 :]]
        moments = self.engine.pair_moments(pairs)
        kept: list[str] = []
        for variable in ranked:
            worst, partner = None, ""
            for other in kept:
                value = (moments.get((other, variable)) or moments[variable, other]).correlation()
                if value is not None and (worst is None or abs(value) > worst):
                    worst, partner = abs(value), other
            limit = options.max_correlation
            if _judge(worst, limit, MAX):
                reason = f"correlated with {partner} (|r| = {worst:.6g} above {limit:.6g})"
                self.entry(variable, REDUNDANCY, "max_correlation", worst, limit, MAX, decision=DROP, reason=reason)
            else:
                self.entry(variable, REDUNDANCY, "max_correlation", worst, limit, MAX)
                kept.append(variable)
        self.vif(kept)

    def vif(self, variables: list[str]) -> None:
        if not variables:
            return
        options = self.config.redundancy
        rows = [r for r in self.engine.sample(variables) if all(r[v] is not None for v in variables)]
        if len(rows) < 2:  # noqa: PLR2004 - a correlation needs two rows
            for variable in variables:
                self.entry(variable, REDUNDANCY, "vif", None, decision=INFO, reason="too few complete rows")
            return
        columns = {v: [float(r[v]) for r in rows] for v in variables}
        remaining = list(variables)
        while True:
            values = vif(correlation_matrix([columns[v] for v in remaining]))
            worst = max(range(len(remaining)), key=lambda i: values[i])
            if not _judge(values[worst], options.max_vif, MAX) or len(remaining) < 2:  # noqa: PLR2004
                break
            self.entry(remaining[worst], REDUNDANCY, "vif", values[worst], options.max_vif, MAX)
            del remaining[worst]
        for variable, value in zip(remaining, values, strict=True):
            self.entry(variable, REDUNDANCY, "vif", value, options.max_vif, MAX)

    def multivariate(self) -> None:
        from bricksmith.mlops.selection.multivariate import evaluate  # noqa: PLC0415 - scikit-learn loads where used

        options = self.config.multivariate
        for variable in self.alive:
            if self.kinds[variable] == CATEGORICAL:
                self.entry(variable, MULTIVARIATE, "coefficient", None, decision=INFO,
                           reason="categorical: not evaluated")  # fmt: skip
        numerics = [v for v in self.alive if self.kinds[v] == NUMERIC]
        if not numerics:
            return
        holdout = self.engine.sample(numerics, out_of_time=True) if self.config.out_of_time else []
        scores = evaluate(self.engine.sample(numerics), holdout, numerics, self.config)
        for variable in numerics:
            if scores is None:
                self.entry(variable, MULTIVARIATE, "coefficient", None, decision=INFO,
                           reason="too few complete rows, or a single target value")  # fmt: skip
                continue
            coefficient, importance = scores[variable]
            self.entry(variable, MULTIVARIATE, "coefficient", abs(coefficient), 0.0, NONZERO,
                       reason="" if abs(coefficient) > 0 else "zero coefficient under the penalty")  # fmt: skip
            self.entry(variable, MULTIVARIATE, "permutation_importance", importance, options.min_importance, FLOOR)

    def judgment(self) -> None:
        options = self.config.judgment
        for variable in self.candidates:
            if variable in options.keep:
                reason = options.keep[variable]
                self.left.pop(variable, None)
                self.entry(variable, JUDGMENT, "manual", None, rule=MANUAL, decision=KEEP, reason=reason)
            elif variable in options.drop:
                self.entry(
                    variable, JUDGMENT, "manual", None, rule=MANUAL, decision=DROP, reason=options.drop[variable]
                )

    def run(self) -> SelectionResult:
        steps = {QUALITY: self.quality, STABILITY: self.stability, UNIVARIATE: self.univariate,
                 REDUNDANCY: self.redundancy, MULTIVARIATE: self.multivariate, JUDGMENT: self.judgment}  # fmt: skip
        for step in STEPS:
            if self.config.enabled(step):
                steps[step]()
        kept = set(self.config.judgment.keep)
        outcomes = tuple(
            VariableOutcome(v, self.kinds[v], v not in self.left, *self.left.get(v, ("", "")))
            if v not in kept
            else VariableOutcome(
                v, self.kinds[v], selected=True, reason=f"kept by judgment: {self.config.judgment.keep[v]}"
            )
            for v in self.candidates
        )
        dev, oot = self.engine.sizes()
        return SelectionResult(
            self.config,
            tuple(self.ledger),
            outcomes,
            tuple(o.variable for o in outcomes if o.selected),
            dict(self.woe),
            dev,
            oot,
        )


def engine_for(data: object, config: SelectionConfig) -> Engine:
    """Return the engine for ``data``: rows (a list or tuple of mappings) or a Spark DataFrame."""
    if isinstance(data, list | tuple):
        return RowsEngine(data, config)
    if hasattr(data, "sparkSession") and hasattr(data, "groupBy"):
        from bricksmith.mlops.selection.spark import SparkEngine  # noqa: PLC0415 - Spark loads where used

        return SparkEngine(data, config)  # type: ignore[arg-type]
    return fail(f"input must be a list of mappings or a Spark DataFrame, got {type(data).__name__}")


def select_features(data: object, config: Mapping[str, Any] | SelectionConfig) -> SelectionResult:
    """Run the funnel over ``data`` (rows or a Spark DataFrame) and return the ledger and the final feature set."""
    parsed = parse_config(config)
    return _Run(parsed, engine_for(data, parsed)).run()


__all__ = [
    "DROP",
    "INFO",
    "KEEP",
    "PASS",
    "LedgerEntry",
    "SelectionResult",
    "VariableOutcome",
    "apply_woe",
    "engine_for",
    "select_features",
]
