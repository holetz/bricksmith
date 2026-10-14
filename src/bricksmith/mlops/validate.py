"""Dataset validation (before fitting) and the fingerprint of the resolved configuration and inputs.

All checks run on row dicts (the training dataset's key, time, split and label columns); the Spark
helpers collect only those projected columns. A finding is an error unless stated otherwise.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import DatasetValidationError
from bricksmith.mlops.spec import normalize_type, to_date

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import date

    from bricksmith.mlops.spec import ModelSpec

EXAMPLE_LIMIT = 3


@dataclass(frozen=True)
class Finding:
    """One diagnosed problem with its count and a few example keys."""

    code: str
    message: str
    count: int = 1
    examples: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {"code": self.code, "count": self.count, "examples": list(self.examples), "message": self.message}


@dataclass(frozen=True)
class ValidationReport:
    """The findings of a dataset validation; empty means the dataset may be fitted."""

    model: str
    findings: tuple[Finding, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether there is no finding."""
        return not self.findings

    def codes(self) -> list[str]:
        """Return the sorted finding codes."""
        return sorted({f.code for f in self.findings})

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {"findings": [f.to_dict() for f in self.findings], "model": self.model, "ok": self.ok}

    def require(self) -> None:
        """Raise :class:`DatasetValidationError` listing the findings when any exists."""
        if self.findings:
            lines = "; ".join(f"{f.code} ({f.count}): {f.message}" for f in self.findings)
            msg = f"dataset for model {self.model!r} is not valid: {lines}"
            raise DatasetValidationError(msg)


def _finding(code: str, message: str, bad: Sequence[Mapping[str, Any]], spec: ModelSpec) -> Finding:
    examples = tuple("|".join(str(row.get(c)) for c in (*spec.key, spec.snapshot)) for row in bad[:EXAMPLE_LIMIT])
    return Finding(code, message, len(bad), examples)


def check_feature_contract(spec: ModelSpec, actual: Sequence[tuple[str, str]]) -> list[Finding]:
    """Compare ordered ``(name, type)`` columns with the feature contract (reserved columns are ignored)."""
    cols = [(n, normalize_type(t)) for n, t in actual if n not in spec.reserved]
    want = [(f.name, f.type) for f in spec.features]
    findings: list[Finding] = []
    have = {n for n, _ in cols}
    missing = [n for n, _ in want if n not in have]
    extra = [n for n, _ in cols if n not in {w for w, _ in want}]
    if missing:
        findings.append(Finding("feature_missing", f"missing features: {', '.join(missing)}", len(missing)))
    if extra:
        findings.append(Finding("feature_unexpected", f"features outside the contract: {', '.join(extra)}", len(extra)))
    kinds = dict(want)
    wrong = [f"{n} is {t}, contract says {kinds[n]}" for n, t in cols if n in kinds and kinds[n] != t]
    if wrong:
        findings.append(Finding("feature_type", "; ".join(wrong), len(wrong)))
    expected = [n for n, _ in want if n in have]
    if [n for n, _ in cols if n in kinds] != expected:
        findings.append(Finding("feature_order", f"expected order {', '.join(expected)}"))
    return findings


def _day(row: Mapping[str, Any], column: str) -> date | None:
    value = row.get(column)
    return None if value is None else to_date(value)


def check_label_maturity(spec: ModelSpec, rows: Sequence[Mapping[str, Any]], reference: date) -> list[Finding]:
    """Diagnose labels not yet observable at ``reference`` and rows without a label."""
    horizon = timedelta(days=spec.label.maturity_days)
    immature = [r for r in rows if (s := _day(r, spec.snapshot)) is not None and s + horizon > reference]
    findings: list[Finding] = []
    if immature:
        text = f"label observed {spec.label.maturity_days} days after the snapshot is not mature at {reference}"
        findings.append(_finding("immature_label", text, immature, spec))
    missing = [r for r in rows if r.get(spec.label.name) is None]
    if missing:
        findings.append(_finding("label_missing", f"rows without {spec.label.name}", missing, spec))
    return findings


def check_point_in_time(spec: ModelSpec, rows: Sequence[Mapping[str, Any]], reference: date) -> list[Finding]:
    """Reject snapshots, features and labels observed after the reference date, and features newer than the snapshot."""
    rules = (
        (
            "snapshot_after_reference",
            f"snapshot after {reference}",
            lambda r: _after(_day(r, spec.snapshot), reference),
        ),
        (
            "feature_after_reference",
            f"feature observed after {reference}",
            lambda r: _after(_day(r, spec.feature_time), reference),
        ),
        (
            "label_after_reference",
            f"label observed after {reference}",
            lambda r: _after(_day(r, "label_time"), reference),
        ),
        (
            "feature_after_snapshot",
            "feature observed after its own snapshot",
            lambda r: _after(_day(r, spec.feature_time), _day(r, spec.snapshot)),
        ),
    )
    return [_finding(code, text, bad, spec) for code, text, test in rules if (bad := [r for r in rows if test(r)])]


def _after(value: date | None, limit: date | None) -> bool:
    return value is not None and limit is not None and value > limit


def check_split(spec: ModelSpec, rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    """Check train/evaluation separation per the declared split: windows, label horizon and (optionally) keys."""
    if spec.split is None:
        return []
    train = [r for r in rows if r.get("split") == "train"]
    evals = [r for r in rows if r.get("split") == "evaluation"]
    findings: list[Finding] = []
    stray = [r for r in train if _after(_day(r, spec.snapshot), spec.split.train_until)]
    stray += [r for r in evals if (d := _day(r, spec.snapshot)) is not None and d < spec.split.evaluation_from]
    if stray:
        findings.append(_finding("split_window", "rows outside their declared split window", stray, spec))
    if train and evals:
        first_eval = min(d for r in evals if (d := _day(r, spec.snapshot)))
        late = [r for r in train if _after(_day(r, spec.snapshot), first_eval) or _day(r, spec.snapshot) == first_eval]
        if late:
            findings.append(_finding("split_time_overlap", f"training snapshots at or after {first_eval}", late, spec))
        horizon = timedelta(days=spec.label.maturity_days)
        leaking = [r for r in train if (d := _day(r, spec.snapshot)) is not None and d + horizon > first_eval]
        if leaking:
            text = f"training labels become observable after the first evaluation snapshot {first_eval}"
            findings.append(_finding("split_label_overlap", text, leaking, spec))
    if spec.split.separate_keys:
        shared = {tuple(r.get(k) for k in spec.key) for r in train} & {tuple(r.get(k) for k in spec.key) for r in evals}
        if shared:
            bad = [r for r in evals if tuple(r.get(k) for k in spec.key) in shared]
            findings.append(_finding("split_key_overlap", "keys present in both train and evaluation", bad, spec))
    return findings


def validate_dataset(
    spec: ModelSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    columns: Sequence[tuple[str, str]] | None = None,
    reference_date: date | str | None = None,
) -> ValidationReport:
    """Run every check applicable to a training dataset; ``columns`` adds the feature-contract check."""
    reference = to_date(reference_date) if reference_date is not None else spec.reference_date
    findings: list[Finding] = []
    if columns is not None:
        findings += check_feature_contract(spec, columns)
    if reference is None:
        findings.append(Finding("reference_date_missing", "no reference date declared or given"))
    else:
        findings += check_label_maturity(spec, rows, reference)
        findings += check_point_in_time(spec, rows, reference)
    findings += check_split(spec, rows)
    return ValidationReport(spec.model, tuple(findings))


def validate_prediction(
    spec: ModelSpec,
    rows: Sequence[Mapping[str, Any]],
    *,
    as_of: date | str,
    columns: Sequence[tuple[str, str]] | None = None,
) -> ValidationReport:
    """Validate a prediction dataset: contract plus no feature or snapshot after ``as_of``."""
    day = to_date(as_of)
    findings = check_feature_contract(spec, columns) if columns is not None else []
    findings += check_point_in_time(spec, rows, day)
    return ValidationReport(spec.model, tuple(findings))


@dataclass(frozen=True)
class DatasetFingerprint:
    """Hash of the resolved configuration plus the pinned input versions; unpinned inputs are listed."""

    config: str
    inputs: Mapping[str, str | int | None]
    value: str

    @property
    def unpinned(self) -> tuple[str, ...]:
        """Inputs without a version (not Delta tables, or missing)."""
        return tuple(sorted(name for name, version in self.inputs.items() if version is None))

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {
            "config": self.config,
            "fingerprint": self.value,
            "inputs": dict(sorted(self.inputs.items())),
            "unpinned": list(self.unpinned),
        }


def fingerprint(
    spec: ModelSpec, inputs: Mapping[str, str | int | None] | None = None, *, role: str = ""
) -> DatasetFingerprint:
    """Fingerprint the spec, the dataset role and the input table versions; any change changes the value."""
    config = spec.config_hash()
    pinned = dict(sorted((inputs or {}).items()))
    payload = json.dumps({"config": config, "inputs": pinned, "role": role}, sort_keys=True)
    return DatasetFingerprint(config, pinned, hashlib.sha256(payload.encode()).hexdigest())


def rows_from(frame_rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Materialize Spark ``Row`` objects or mappings as plain dicts."""
    return [r.asDict() if hasattr(r, "asDict") else dict(r) for r in frame_rows]  # type: ignore[attr-defined]
