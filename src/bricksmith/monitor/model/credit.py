"""The credit-model families measured on one frame.

Per-grade calibration, grade order, early-warning horizons, the exclusion mix and the bad rate. Every family reduces
the frame to small sums (rows, observed events, expected events) through ``Frame.sums``, on rows or Spark alike, and
runs its statistics on those sums here. Early-warning KS and Gini reuse the shared measurement core with the
horizon's label column.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.adapter import measure
from bricksmith.monitor.bins import category_text, is_null
from bricksmith.monitor.credit import adjusted_binomial_p, binomial_p, inversions, jeffreys_p
from bricksmith.monitor.measure import OK
from bricksmith.monitor.model.engine import Item
from bricksmith.monitor.stats import psi_terms

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bricksmith.monitor.model.config import Horizon, ModelMonitorConfig
    from bricksmith.monitor.model.engine import Frame
    from bricksmith.monitor.reference import Reference

#: The category of a row the exclusion cascade kept (its first exclusion reason is null).
NOT_EXCLUDED = "(none)"
Sums = dict[str, tuple[int, int, float]]


def segments_of(frame: Frame, segment: str) -> list[tuple[str, Frame]]:
    """Return the whole frame (segment ``""``) and, with a segment column, each of its segments."""
    return [("", frame)] + ([(s, frame.segment(segment, s)) for s in frame.segments(segment)] if segment else [])


def grade_order(config: ModelMonitorConfig, present: Sequence[str]) -> list[str]:
    """Return the grades from best to worst: the declared order, then undeclared grades in text order."""
    declared = [g for g in config.credit_options.grades if g in present]
    return declared + sorted(set(present) - set(declared))


def _column(config: ModelMonitorConfig, grade: str) -> str:
    return f"{config.grade}={grade}"


def grade_calibration(config: ModelMonitorConfig, frame: Frame) -> list[Item]:
    """Return observed/expected and the one-sided tests of each grade of each segment.

    ``expected`` on every item is the grade's expected events, which the volume gate compares with
    ``min_expected_events``; the adjusted binomial test is measured only when a correlation is declared.
    """
    credit = config.credit_options
    items: list[Item] = []
    for name, part in segments_of(frame, config.segment):
        sums = part.sums(config.score, config.label, credit.calibration_exponent, config.grade)
        for grade in grade_order(config, list(sums)):
            rows, events, expected = sums[grade]
            column = _column(config, grade)
            if expected <= 0:
                items.append(Item(name, column, "grade_oe", None, rows, "no_expected_events", expected=expected))
                continue
            pd = min(expected / rows, 1.0)
            values = {
                "grade_oe": events / expected,
                "grade_jeffreys_p": jeffreys_p(rows, events, pd),
                "grade_binomial_p": binomial_p(rows, events, pd),
            }
            if credit.correlation is not None:
                values["grade_binomial_adjusted_p"] = adjusted_binomial_p(rows, events, pd, credit.correlation)
            items += [Item(name, column, m, v, rows, expected=expected) for m, v in values.items()]
    return items


def grade_inversions(config: ModelMonitorConfig, frame: Frame) -> list[Item]:
    """Return the inversions between consecutive grades that pass the expected-events gate, per segment.

    The details carry the calibration table of every grade (rows, mean expected probability, observed rate).
    """
    credit = config.credit_options
    items: list[Item] = []
    for name, part in segments_of(frame, config.segment):
        sums = part.sums(config.score, config.label, credit.calibration_exponent, config.grade)
        order = grade_order(config, list(sums))
        details = tuple(
            {"bin": g, "bin_order": i, "count": sums[g][0], "mean_score": sums[g][2] / sums[g][0],
             "observed_rate": sums[g][1] / sums[g][0]}
            for i, g in enumerate(order)
        )  # fmt: skip
        rates = [sums[g][1] / sums[g][0] for g in order if sums[g][2] >= credit.min_expected_events]
        total = sum(sums[g][0] for g in order)
        if len(rates) < 2:  # noqa: PLR2004 - an inversion needs a pair of grades
            items.append(Item(name, config.grade, "grade_inversions", None, total, "fewer_than_two_grades", details))
        else:
            items.append(Item(name, config.grade, "grade_inversions", float(inversions(rates)), total, "", details))
    return items


def early_warning(config: ModelMonitorConfig, scored: Reference, horizon: Horizon, frame: Frame) -> list[Item]:
    """Return KS, Gini and observed/expected (rescaled to the horizon) of one early-warning label, per segment."""
    reference = dataclasses.replace(scored, label=horizon.label)
    names = {"ks": "early_ks", "gini": "early_gini"}
    items = [
        Item(m.segment, horizon.label, names[m.metric], m.value if m.status == OK else None, m.count,
             "" if m.status == OK else m.reason)
        for m in measure(reference, frame.data, segments=bool(config.segment))
        if m.metric in names
    ]  # fmt: skip
    exponent = config.credit_options.exponent(horizon.periods)
    for name, part in segments_of(frame, config.segment):
        rows, events, expected = part.sums(config.score, horizon.label, exponent).get("", (0, 0, 0.0))
        value = events / expected if expected > 0 else None
        reason = "" if value is not None else "no_expected_events"
        items.append(Item(name, horizon.label, "early_oe", value, rows, reason, expected=expected))
    return items


def exclusion_counts(frame: Frame, column: str) -> dict[str, int]:
    """Return the rows per first exclusion reason (``(none)`` for rows the cascade kept)."""
    counts: dict[str, int] = {}
    for value, count in frame.value_counts(column).items():
        key = NOT_EXCLUDED if is_null(value) else category_text(value)
        counts[key] = counts.get(key, 0) + count
    return counts


def exclusion_baseline(config: ModelMonitorConfig, development: Frame) -> dict[str, dict[str, int]]:
    """Return the development exclusion mix of the whole population and of each segment."""
    column = config.credit_options.exclusion
    return {name: exclusion_counts(part, column) for name, part in segments_of(development, config.segment)}


def exclusion_mix(config: ModelMonitorConfig, baseline: Mapping[str, Mapping[str, int]], frame: Frame) -> list[Item]:
    """Return the PSI of the first-exclusion-reason mix against development, with per-reason details."""
    column = config.credit_options.exclusion
    items: list[Item] = []
    for name, part in segments_of(frame, config.segment):
        current = exclusion_counts(part, column)
        rows = sum(current.values())
        reference = baseline.get(name, {})
        if not rows or not sum(reference.values()):
            reason = "empty_window" if not rows else "empty_reference"
            items.append(Item(name, column, "exclusion_psi", None, rows, reason))
            continue
        terms = psi_terms(reference, current, config.epsilon)
        details: tuple[dict[str, Any], ...] = tuple(
            {"bin": key, "bin_order": i, "reference_share": t["reference"], "current_share": t["current"],
             "contribution": t["contribution"]}
            for i, (key, t) in enumerate(terms.items())
        )  # fmt: skip
        items.append(Item(name, column, "exclusion_psi", sum(t["contribution"] for t in terms.values()), rows, "",
                          details))  # fmt: skip
    return items


def bad_rate(config: ModelMonitorConfig, frame: Frame) -> list[Item]:
    """Return the observed event rate of the whole population and of each segment."""
    items: list[Item] = []
    for name, part in segments_of(frame, config.segment):
        rows, events, _ = part.sums(config.score, config.label, 1.0).get("", (0, 0, 0.0))
        items.append(Item(name, "", "bad_rate", events / rows if rows else None, rows, "" if rows else "empty_window"))
    return items


__all__ = [
    "NOT_EXCLUDED",
    "bad_rate",
    "early_warning",
    "exclusion_baseline",
    "exclusion_counts",
    "exclusion_mix",
    "grade_calibration",
    "grade_inversions",
    "grade_order",
    "segments_of",
]
