"""Pure-Python reference for dataset construction over row dicts (the oracle for the Spark helpers)."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from bricksmith.mlops.spec import to_date

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import date

    from bricksmith.mlops.spec import ModelSpec

Row = dict[str, Any]


def key_text(row: Mapping[str, Any], key: Sequence[str]) -> str:
    """Return the string a key hashes as: values as text joined by ``|``, null as empty."""
    return "|".join("" if row.get(k) is None else str(row[k]) for k in key)


def sample_fraction(seed: int, text: str) -> float:
    """Return the key's position in [0, 1): the first 32 bits of ``sha256("<seed>:<key>")``."""
    return int(hashlib.sha256(f"{seed}:{text}".encode()).hexdigest()[:8], 16) / 2**32


def _coerce(left: object, right: object) -> object:
    if isinstance(right, str) and hasattr(left, "isoformat"):
        return to_date(right)
    return right


def matches(predicate: Mapping[str, Any], row: Mapping[str, Any]) -> bool:  # noqa: PLR0911 - one branch per operator
    """Evaluate a predicate on a row; comparisons with null are false."""
    if "all" in predicate:
        return all(matches(p, row) for p in predicate["all"])
    if "any" in predicate:
        return any(matches(p, row) for p in predicate["any"])
    if "not" in predicate:
        return not matches(predicate["not"], row)
    value, op = row.get(predicate["column"]), predicate["op"]
    if op == "is_null":
        return value is None
    if op == "not_null":
        return value is not None
    if value is None:
        return False
    expected = predicate["value"]
    if op == "in":
        return any(value == _coerce(value, item) for item in expected)
    expected = _coerce(value, expected)
    return {
        "eq": value == expected,
        "ne": value != expected,
        "lt": value < expected,
        "le": value <= expected,
        "gt": value > expected,
        "ge": value >= expected,
    }[op]


def in_sample(spec: ModelSpec, row: Mapping[str, Any]) -> bool:
    """Whether the row's key falls in the declared cohort sample (always true without a sample)."""
    if spec.sample is None:
        return True
    return sample_fraction(spec.sample.seed, key_text(row, spec.key)) < spec.sample.fraction


def label_time(spec: ModelSpec, snapshot: object) -> date:
    """Return the date the label of a snapshot becomes observable."""
    return to_date(snapshot) + timedelta(days=spec.label.maturity_days)


def build_population(spec: ModelSpec, rows: Iterable[Mapping[str, Any]]) -> tuple[list[Row], list[Row]]:
    """Return ``(population, exclusions)``: sampled rows split by the first matching exclusion reason.

    Rows outside the sample are not part of the cohort and appear in neither list. Every excluded row
    carries ``model`` and ``exclusion_reason``; every kept row carries ``segment`` (first match or null).
    """
    population: list[Row] = []
    exclusions: list[Row] = []
    for row in rows:
        if not in_sample(spec, row):
            continue
        base: Row = {c: row.get(c) for c in (*spec.key, spec.snapshot)}
        reason = next((r.name for r in spec.exclusions if matches(r.where, row)), None)
        if reason is not None:
            exclusions.append({"model": spec.model, **base, "exclusion_reason": reason})
            continue
        segment = next((s.name for s in spec.segments if matches(s.where, row)), None)
        population.append({**base, "segment": segment})
    return population, exclusions


def build_features(spec: ModelSpec, rows: Iterable[Mapping[str, Any]]) -> list[Row]:
    """Project key, snapshot, feature time and the features in contract order."""
    cols = (*spec.key, spec.snapshot, spec.feature_time, *(f.name for f in spec.features))
    return [{c: row.get(c) for c in cols} for row in rows]


def build_labels(spec: ModelSpec, rows: Iterable[Mapping[str, Any]]) -> list[Row]:
    """Project key, snapshot, the label and the date it becomes observable."""
    return [
        {
            **{c: row.get(c) for c in (*spec.key, spec.snapshot)},
            spec.label.name: row.get(spec.label.name),
            "label_time": label_time(spec, row[spec.snapshot]),
        }
        for row in rows
    ]


def assign_split(spec: ModelSpec, snapshot: object) -> str | None:
    """Return ``train``, ``evaluation`` or None (in the gap) for a snapshot date."""
    if spec.split is None:
        return "train"
    day = to_date(snapshot)
    if day <= spec.split.train_until:
        return "train"
    return "evaluation" if day >= spec.split.evaluation_from else None


def _join_key(spec: ModelSpec, row: Mapping[str, Any]) -> tuple[object, ...]:
    return (*(row.get(k) for k in spec.key), to_date(row[spec.snapshot]))


def build_training(
    spec: ModelSpec,
    population: Iterable[Mapping[str, Any]],
    features: Iterable[Mapping[str, Any]],
    labels: Iterable[Mapping[str, Any]],
    *,
    drop_immature: bool = False,
) -> list[Row]:
    """Join population, features and labels on key and snapshot, add the split; rows in the gap are dropped.

    Immature labels are kept unless ``drop_immature`` (with ``spec.reference_date``), so validation can
    diagnose them; a leak is never silently repaired.
    """
    feats = {_join_key(spec, r): r for r in features}
    labs = {_join_key(spec, r): r for r in labels}
    out: list[Row] = []
    for pop in population:
        joined = _join_key(spec, pop)
        if joined not in feats or joined not in labs:
            continue
        split = assign_split(spec, pop[spec.snapshot])
        lab = labs[joined]
        if split is None or (drop_immature and spec.reference_date and lab["label_time"] > spec.reference_date):
            continue
        out.append(
            {
                **{c: pop.get(c) for c in (*spec.key, spec.snapshot)},
                "segment": pop.get("segment"),
                spec.feature_time: feats[joined][spec.feature_time],
                **{f.name: feats[joined][f.name] for f in spec.features},
                spec.label.name: lab[spec.label.name],
                "label_time": lab["label_time"],
                "split": split,
            }
        )
    return out


def build_prediction(
    spec: ModelSpec,
    population: Iterable[Mapping[str, Any]],
    features: Iterable[Mapping[str, Any]],
    as_of: object,
) -> list[Row]:
    """Rows of the population snapshot ``as_of`` with their features, using only features observed by ``as_of``."""
    day = to_date(as_of)
    feats = {
        _join_key(spec, r): r
        for r in features
        if r[spec.feature_time] is not None and to_date(r[spec.feature_time]) <= day
    }
    out: list[Row] = []
    for pop in population:
        joined = _join_key(spec, pop)
        if to_date(pop[spec.snapshot]) == day and joined in feats:
            out.append(
                {
                    **{c: pop.get(c) for c in (*spec.key, spec.snapshot)},
                    "segment": pop.get("segment"),
                    spec.feature_time: feats[joined][spec.feature_time],
                    **{f.name: feats[joined][f.name] for f in spec.features},
                }
            )
    return out
