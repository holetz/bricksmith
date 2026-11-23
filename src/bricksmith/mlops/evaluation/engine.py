"""The evaluation suite: every sample measured against the reference sample through the shared monitor core.

``evaluate`` fits one monitor :class:`~bricksmith.monitor.reference.Reference` on the reference sample (score,
grade and declared features) and measures each sample with :func:`bricksmith.monitor.adapter.measure`, whole and
per closing. KS, AUC, Gini, PSI, CSI, Brier and ECE therefore have one definition shared with model monitoring,
for rows and Spark DataFrames alike. The per-grade calibration table and its inversions are computed here from
grade counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.evaluation.config import INCREASING, EvaluationConfig, fail, parse_config
from bricksmith.monitor.adapter import fit_reference, measure
from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.measure import OK, UNAVAILABLE, segment_name

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from bricksmith.mlops.stages.config import Development
    from bricksmith.monitor.measure import Measurement
    from bricksmith.monitor.reference import Reference

#: The closing value of a whole-sample row.
WHOLE = ""
INVERSIONS = "inversions"
GRADE_PSI = "grade_psi"
#: Monitor-core metrics kept in the results, plus ``grade_psi`` (the CSI of the grade) and ``inversions``.
KEPT = frozenset({"row_count", "psi", "csi", "auc", "gini", "ks", "brier", "ece", "null_rate"})
CALIBRATION = frozenset({"brier", "ece"})
#: Grade count slots: rows, events, probability sum, squared error sum, rows with an invalid probability.
ROWS, EVENTS, PROBABILITY, SQUARED, INVALID = range(5)
type Counts = dict[tuple[str, str], list[float]]


@dataclass(frozen=True)
class EvaluationResult:
    """The results, score and grade distributions and grade tables of every evaluated sample."""

    config: EvaluationConfig
    reference: Reference
    samples: tuple[str, ...]
    results: tuple[dict[str, Any], ...]
    distribution: tuple[dict[str, Any], ...]
    grades: tuple[dict[str, Any], ...]

    def value(self, sample: str, metric: str, subject: str = "", closing: str = WHOLE) -> float | None:
        """Return one result value (``None`` when unavailable or absent)."""
        for row in self.results:
            if (row["sample"], row["metric"], row["closing"]) == (sample, metric, closing) and (
                not subject or row["subject"] == subject
            ):
                return row["value"]
        return None

    def metrics(self) -> dict[str, float]:
        """Return the whole-sample values as flat MLflow metric names (``<sample>.<metric>[.<subject>]``)."""
        out: dict[str, float] = {}
        for row in self.results:
            if row["closing"] != WHOLE or row["value"] is None:
                continue
            suffix = f".{row['subject']}" if row["metric"] == "csi" else ""
            out[f"{row['sample']}.{row['metric']}{suffix}"] = float(row["value"])
        return out

    def to_dict(self) -> dict[str, Any]:
        """Return the plain, JSON-serializable form."""
        return {
            "config": self.config.to_dict(),
            "distribution": [dict(r) for r in self.distribution],
            "fingerprint": self.config.fingerprint(),
            "grades": [dict(r) for r in self.grades],
            "name": self.config.name,
            "reference": self.reference.to_dict(),
            "results": [dict(r) for r in self.results],
            "samples": list(self.samples),
        }


# -- engine-specific reads -----------------------------------------------------------------------------------------
def _rows_samples(rows: Sequence[Mapping[str, Any]], column: str) -> list[str]:
    return sorted({str(r[column]) for r in rows if r.get(column) is not None})


def _rows_subset(rows: Sequence[Mapping[str, Any]], column: str, name: str) -> list[Mapping[str, Any]]:
    return [r for r in rows if r.get(column) is not None and str(r[column]) == name]


def _label_value(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number in (0.0, 1.0) else None


def _add(counts: Counts, key: tuple[str, str], label: float, probability: object) -> None:
    slot = counts.setdefault(key, [0.0, 0.0, 0.0, 0.0, 0.0])
    slot[ROWS] += 1
    slot[EVENTS] += label
    try:
        p = None if probability is None else float(probability)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        p = None
    if p is None or not 0.0 <= p <= 1.0:
        slot[INVALID] += 1
        return
    slot[PROBABILITY] += p
    slot[SQUARED] += (p - label) ** 2


def rows_grade_counts(rows: Iterable[Mapping[str, Any]], config: EvaluationConfig) -> Counts:
    """Return ``(closing, grade) -> [rows, events, probability sum, squared error, invalid]`` over labelled rows.

    Rows whose label is not 0 or 1 (null included) are left out; the closing ``""`` holds the whole sample.
    """
    counts: Counts = {}
    for row in rows:
        label = _label_value(row.get(config.label))
        if label is None:
            continue
        grade = segment_name(row.get(config.grade))
        probability = row.get(config.probability_column)
        _add(counts, (WHOLE, grade), label, probability)
        if config.per_closing:
            _add(counts, (segment_name(row.get(config.closing)), grade), label, probability)
    return counts


def _samples(data: Any, config: EvaluationConfig) -> list[str]:  # noqa: ANN401 - rows or DataFrame
    return _rows_samples(data, config.sample)


def _subset(data: Any, config: EvaluationConfig, name: str) -> Any:  # noqa: ANN401 - rows or DataFrame
    return _rows_subset(data, config.sample, name)


def _grade_counts(data: Any, config: EvaluationConfig) -> Counts:  # noqa: ANN401 - rows or DataFrame
    return rows_grade_counts(data, config)


# -- shared derivation ---------------------------------------------------------------------------------------------
def _columns(config: EvaluationConfig) -> dict[str, dict[str, Any]]:
    columns: dict[str, dict[str, Any]] = {
        config.score: {"type": "numeric", "method": "quantile", "bins": config.score_bins}
    }
    if config.grade:
        columns[config.grade] = {"type": "categorical", "max_categories": max(len(config.grades), 50)}
    for feature in config.features:
        columns[feature] = config.bins_of(feature)
    return columns


def _fit(data: Any, config: EvaluationConfig, columns: Mapping[str, Mapping[str, Any]], score: str) -> Reference:  # noqa: ANN401 - rows or DataFrame
    try:
        return fit_reference(
            data,
            columns,
            name=config.name,
            version=1,
            score=score,
            label=config.label,
            segment=config.closing if config.per_closing else "",
            fit_source=f"{config.sample}={config.reference}",
            metric_config={"calibration_bins": config.calibration_bins, "epsilon": config.epsilon},
        )
    except MonitorError as error:
        fail(f"cannot fit the reference sample {config.reference!r}: {error}")


def _result(sample: str, m: Measurement, config: EvaluationConfig) -> dict[str, Any]:
    metric = GRADE_PSI if m.metric == "csi" and config.grade and m.subject == config.grade else m.metric
    return {
        "sample": sample,
        "closing": m.segment,
        "metric": metric,
        "subject": m.subject,
        "value": m.value,
        "count": m.count,
        "status": m.status,
        "reason": m.reason,
    }


def _distribution(sample: str, m: Measurement, metric: str) -> list[dict[str, Any]]:
    bins = m.detail.get("bins", {}) if isinstance(m.detail, dict) else {}
    return [
        {
            "sample": sample,
            "closing": m.segment,
            "metric": metric,
            "subject": m.subject,
            "bin": name,
            "reference_share": term["reference"],
            "current_share": term["current"],
            "contribution": term["contribution"],
        }
        for name, term in bins.items()
    ]


def _order(config: EvaluationConfig, present: Iterable[str]) -> list[tuple[str, int]]:
    declared = list(config.grades)
    extra = sorted(set(present) - set(declared))
    return [(g, i) for i, g in enumerate(declared)] + [(g, -1) for g in extra]


def grade_rows(counts: Counts, config: EvaluationConfig, sample: str) -> tuple[list[dict[str, Any]], list[dict]]:
    """Return the per-grade table of one sample and its ``inversions`` result rows.

    Observed rate = events / rows; expected rate = mean probability; Brier within the grade. Expected and Brier
    are ``None`` when a probability is null or outside [0, 1]. An inversion is a declared grade whose observed
    rate moves against ``direction`` relative to the previous declared grade with rows.
    """
    table: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for closing in sorted({c for c, _ in counts}):
        present = {g for c, g in counts if c == closing}
        previous: float | None = None
        inversions = 0
        total = 0
        for grade, order in _order(config, present):
            slot = counts.get((closing, grade))
            if slot is None:
                continue
            rows, events = int(slot[ROWS]), int(slot[EVENTS])
            total += rows
            observed = events / rows
            valid = slot[INVALID] == 0
            expected = slot[PROBABILITY] / rows if valid else None
            inversion = False
            if order >= 0:
                if previous is not None:
                    inversion = observed < previous if config.direction == INCREASING else observed > previous
                    inversions += int(inversion)
                previous = observed
            table.append(
                {
                    "sample": sample,
                    "closing": closing,
                    "grade": grade,
                    "grade_order": order,
                    "rows": rows,
                    "events": events,
                    "observed_rate": observed,
                    "expected_rate": expected,
                    "observed_expected": observed / expected if expected else None,
                    "brier": slot[SQUARED] / rows if valid else None,
                    "inversion": inversion,
                }
            )
        declared = sum(1 for g, o in _order(config, present) if o >= 0 and (closing, g) in counts)
        status, reason = (OK, "") if declared else (UNAVAILABLE, "no_declared_grades")
        results.append(
            {
                "sample": sample,
                "closing": closing,
                "metric": INVERSIONS,
                "subject": config.grade,
                "value": float(inversions) if declared else None,
                "count": total,
                "status": status,
                "reason": reason,
            }
        )
    return table, results


def _sort_key(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(str(row.get(k, "")) for k in ("sample", "closing", "metric", "subject", "bin", "grade"))


def evaluate(
    data: Any,  # noqa: ANN401 - rows or DataFrame
    config: Mapping[str, Any] | EvaluationConfig,
    development: Development | None = None,
) -> EvaluationResult:
    """Evaluate every sample of ``data`` (rows or a Spark DataFrame with a sample column) against the reference.

    Results hold, per sample and per closing (``closing`` ``""`` is the whole sample): ``row_count``, ``psi`` of
    the score, ``csi`` of each feature, ``grade_psi``, ``auc``, ``gini``, ``ks``, ``brier``, ``ece``,
    ``null_rate`` and ``inversions``. A metric that cannot be computed has status ``unavailable`` and a reason.
    """
    checked = parse_config(dict(config) if not isinstance(config, EvaluationConfig) else config, development)
    names = list(checked.samples) or _samples(data, checked)
    if checked.reference not in names:
        fail(f"the reference sample {checked.reference!r} has no rows (samples: {', '.join(names) or 'none'})")
    names = [checked.reference, *(n for n in names if n != checked.reference)]
    reference_data = _subset(data, checked, checked.reference)
    reference = _fit(reference_data, checked, _columns(checked), checked.score)
    calibrated = None
    if checked.probability:
        probability_bins = {checked.probability: {"type": "numeric", "method": "quantile", "bins": 10}}
        calibrated = _fit(reference_data, checked, probability_bins, checked.probability)
    results: list[dict[str, Any]] = []
    distribution: list[dict[str, Any]] = []
    grades: list[dict[str, Any]] = []
    for name in names:
        part = _subset(data, checked, name)
        for m in measure(reference, part):
            row = _result(name, m, checked)
            if m.metric not in KEPT or (calibrated is not None and m.metric in CALIBRATION):
                continue
            results.append(row)
            if row["metric"] in {"psi", GRADE_PSI} and m.status == OK:
                distribution += _distribution(name, m, row["metric"])
        if calibrated is not None:
            results += [_result(name, m, checked) for m in measure(calibrated, part) if m.metric in CALIBRATION]
        if checked.grade:
            table, inversions = grade_rows(_grade_counts(part, checked), checked, name)
            grades += table
            results += inversions
    return EvaluationResult(
        config=checked,
        reference=reference,
        samples=tuple(names),
        results=tuple(sorted(results, key=_sort_key)),
        distribution=tuple(sorted(distribution, key=_sort_key)),
        grades=tuple(
            sorted(
                grades, key=lambda r: (r["sample"], r["closing"], r["grade_order"] < 0, r["grade_order"], r["grade"])
            )
        ),
    )


__all__ = ["GRADE_PSI", "INVERSIONS", "WHOLE", "EvaluationResult", "evaluate", "grade_rows", "rows_grade_counts"]
