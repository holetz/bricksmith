"""The row engine of model development stages: the reference the Spark engine is proved against.

Rows are plain mappings; closings may be dates or ISO strings and come back as dates. Every random choice is a
seeded SHA-256 position of the entity keys and the closing (``bricksmith.mlops.reference.sample_fraction``), so
both engines and every rerun select the same rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.mlops.errors import DatasetError, DatasetValidationError
from bricksmith.mlops.featurestore.config import require_closing, to_day
from bricksmith.mlops.featurestore.rows import TrainingSet, build_training_set
from bricksmith.mlops.reference import key_text, matches, sample_fraction
from bricksmith.mlops.stages.config import (
    CASCADE_COLUMNS,
    EXCLUDED,
    EXCLUSION_FIRST,
    EXCLUSION_RULES,
    GRADE,
    MODEL_ALIAS,
    MODEL_NAME,
    MODEL_VERSION,
    PREDICTION_COLUMNS,
    PURPOSES,
    SAMPLE,
    SAMPLE_ROLE,
    SCORE,
    TEST,
    TRAINING,
    Development,
    SampleWindow,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from datetime import date

    from bricksmith.mlops.featurestore.config import FeatureTable

type Row = dict[str, Any]
_EXAMPLES = 3


def _closing(row: Mapping[str, Any], development: Development) -> date:
    if development.closing not in row:
        msg = f"a row has no closing column {development.closing!r}"
        raise DatasetError(msg)
    day = to_day(row[development.closing], development.closing)
    return require_closing(day, development.frequency, development.closing)


def position(row: Mapping[str, Any], development: Development, seed: int) -> float:
    """Return the row's seeded position in [0, 1) over its entity keys and closing (ISO text)."""
    return sample_fraction(seed, key_text(row, development.spine_keys))


def _examples(rows: Sequence[Mapping[str, Any]], development: Development) -> str:
    return ", ".join(key_text(r, development.spine_keys) for r in rows[:_EXAMPLES])


def apply_cascade(rows: Iterable[Mapping[str, Any]], development: Development) -> list[Row]:
    """Add the cascade columns to every row; excluded rows are kept, never dropped.

    ``exclusion_rules`` lists every rule the row meets in cascade order (empty when none),
    ``exclusion_first`` is the first of them (``None`` when none) and ``excluded`` says whether any was met.
    """
    out: list[Row] = []
    for row in rows:
        met = [rule.name for rule in development.exclusions if matches(rule.where, row)]
        out.append({**row, EXCLUSION_RULES: met, EXCLUSION_FIRST: met[0] if met else None, EXCLUDED: bool(met)})
    return out


def _with_cascade(rows: Iterable[Mapping[str, Any]], development: Development) -> list[Row]:
    listed = [dict(r) for r in rows]
    if all(c in r for r in listed for c in CASCADE_COLUMNS):
        return listed
    return apply_cascade(listed, development)


def composition(rows: Iterable[Mapping[str, Any]], development: Development, *, by: Sequence[str] = ()) -> list[Row]:
    """Return the exclusion composition: one row per group, closing and rule, in cascade order.

    Each row carries ``total`` and ``excluded`` of its group and closing, and for its rule ``met`` (rows meeting
    it) and ``first`` (rows whose first rule it is) with ``first_share`` = ``first / total``. ``by`` adds group
    columns (for example the sample). Rows without the cascade columns are cascaded first.
    """
    groups: dict[tuple[object, ...], dict[str, Any]] = {}
    for row in _with_cascade(rows, development):
        key = (*(row.get(c) for c in by), _closing(row, development))
        group = groups.setdefault(key, {"total": 0, "excluded": 0, "met": {}, "first": {}})
        group["total"] += 1
        group["excluded"] += int(bool(row[EXCLUDED]))
        for name in row[EXCLUSION_RULES] or ():
            group["met"][name] = group["met"].get(name, 0) + 1
        if row[EXCLUSION_FIRST] is not None:
            group["first"][row[EXCLUSION_FIRST]] = group["first"].get(row[EXCLUSION_FIRST], 0) + 1
    out: list[Row] = []
    for key in sorted(groups, key=lambda k: tuple("" if v is None else str(v) for v in k)):
        group = groups[key]
        for rule in development.exclusions:
            first = group["first"].get(rule.name, 0)
            out.append(
                {
                    **dict(zip(by, key[:-1], strict=True)),
                    development.closing: key[-1],
                    "rule": rule.name,
                    "total": group["total"],
                    "excluded": group["excluded"],
                    "met": group["met"].get(rule.name, 0),
                    "first": first,
                    "first_share": first / group["total"],
                }
            )
    return out


def _in_split(row: Mapping[str, Any], day: date, window: SampleWindow, development: Development) -> bool:
    """Whether a row of ``window`` stays in it once closings shared by train and test are divided."""
    if development.split is None or not any(s.contains(day) for s in development.shared(window)):
        return True
    drawn = position(row, development, development.split.seed) < development.split.test_fraction
    return drawn if window.role == TEST else not drawn


def select_sample(rows: Iterable[Mapping[str, Any]], development: Development, sample: str) -> list[Row]:
    """Return the rows whose closing is in the sample's window, tagged with the sample name and role.

    On closings a train window shares with a test window, a row goes to test when its split position is below
    ``split.test_fraction`` and to train otherwise, so the two never hold the same row.
    """
    window = development.sample(sample)
    out: list[Row] = []
    for row in rows:
        day = _closing(row, development)
        if window.contains(day) and _in_split(row, day, window, development):
            out.append({**row, development.closing: day, SAMPLE: window.name, SAMPLE_ROLE: window.role})
    return out


def stratify(rows: Iterable[Mapping[str, Any]], development: Development) -> list[Row]:
    """Keep a row when its stratification position is below the fraction declared for its label value."""
    plan, label = development.stratify, development.label
    if plan is None or label is None:
        return [dict(r) for r in rows]
    return [dict(r) for r in rows if position(r, development, plan.seed) < plan.fraction(r.get(label.name))]


def _label_index(
    labels: Iterable[Mapping[str, Any]], development: Development, name: str
) -> dict[tuple[object, ...], object]:
    index: dict[tuple[object, ...], object] = {}
    for item in labels:
        key = (*(item.get(k) for k in development.entity_keys), _closing(item, development))
        if key in index:
            msg = f"label rows repeat the key {key!r}"
            raise DatasetValidationError(msg)
        index[key] = item.get(name)
    return index


def attach_labels(
    rows: Iterable[Mapping[str, Any]], development: Development, labels: Iterable[Mapping[str, Any]] | None = None
) -> list[Row]:
    """Return the rows with their label; refuse an immature, missing or duplicated label (never drop it).

    ``labels`` rows are keyed by the entity keys and the observation closing; without them the rows must already
    carry the label column. A label is mature when its observation closing plus the horizon is at most ``as_of``.
    """
    label, as_of = development.label, development.as_of
    if label is None or as_of is None:
        msg = "attaching labels needs a declared label and as_of"
        raise DatasetError(msg)
    index = None if labels is None else _label_index(labels, development, label.name)
    out: list[Row] = []
    missing: list[Row] = []
    immature: list[Row] = []
    for row in rows:
        day = _closing(row, development)
        if label.matures(day, development.frequency) > as_of:
            immature.append(dict(row))
        if index is None:
            value = row.get(label.name)
        else:
            value = index.get((*(row.get(k) for k in development.entity_keys), day))
        if value is None:
            missing.append(dict(row))
        out.append({**row, development.closing: day, label.name: value})
    if immature:
        msg = (
            f"{len(immature)} row(s) have a label that matures after as_of {as_of.isoformat()} "
            f"({label.horizon} periods), e.g. {_examples(immature, development)}"
        )
        raise DatasetValidationError(msg)
    if missing:
        msg = f"{len(missing)} row(s) have no {label.name!r} label, e.g. {_examples(missing, development)}"
        raise DatasetValidationError(msg)
    return out


def build_abt(  # noqa: PLR0913 - the sample, its purpose and the inputs of the feature store builder
    population: Iterable[Mapping[str, Any]],
    development: Development,
    sample: str,
    *,
    purpose: str = TRAINING,
    definitions: Mapping[str, FeatureTable] | None = None,
    data: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    labels: Iterable[Mapping[str, Any]] | None = None,
) -> TrainingSet:
    """Build the ABT of one sample: the population is the feature store spine, for training and scoring alike.

    Training (a labelled sample): the sample's rows that are not excluded, with their label, stratified when
    declared, joined to the declared lookups. Scoring: every row of the sample, excluded ones included with their
    cascade flags, joined to the same lookups. Population rows without the cascade columns are cascaded first.
    """
    if purpose not in PURPOSES:
        msg = f"purpose must be one of {', '.join(PURPOSES)}, got {purpose!r}"
        raise DatasetError(msg)
    window = development.sample(sample)
    rows = select_sample(_with_cascade(population, development), development, sample)
    if purpose == TRAINING:
        if not window.labelled:
            msg = f"sample {sample!r} has role {window.role!r}, which carries no label; build it for scoring"
            raise DatasetError(msg)
        rows = stratify(attach_labels([r for r in rows if not r[EXCLUDED]], development, labels), development)
    return build_training_set(rows, development.lookups, definitions or {}, data or {}, closing=development.closing)


def prediction_rows(
    scored: Iterable[Mapping[str, Any]],
    development: Development,
    *,
    model_version: str | int,
    model_alias: str | None = None,
    score: str = SCORE,
) -> list[Row]:
    """Return scored ABT rows in the prediction contract that model monitoring reads.

    Columns: entity keys, closing, model name, version and alias, score, grade (from the declared bands, else the
    row's own ``grade``), sample, sample role and the cascade columns.
    """
    out: list[Row] = []
    for row in scored:
        missing = [c for c in (score, SAMPLE, SAMPLE_ROLE, *CASCADE_COLUMNS) if c not in row]
        if missing:
            msg = f"a scored row misses {', '.join(missing)}; score rows of an ABT built by build_abt"
            raise DatasetError(msg)
        value = None if row[score] is None else float(row[score])
        out.append(
            {
                **{k: row.get(k) for k in development.entity_keys},
                development.closing: _closing(row, development),
                MODEL_NAME: development.model,
                MODEL_VERSION: str(model_version),
                MODEL_ALIAS: model_alias,
                SCORE: value,
                GRADE: development.grade(value) if development.bands else row.get(GRADE),
                SAMPLE: row[SAMPLE],
                SAMPLE_ROLE: row[SAMPLE_ROLE],
                EXCLUSION_RULES: list(row[EXCLUSION_RULES] or []),
                EXCLUSION_FIRST: row[EXCLUSION_FIRST],
                EXCLUDED: bool(row[EXCLUDED]),
            }
        )
    return check_prediction(out, development)


def check_prediction(rows: Iterable[Mapping[str, Any]], development: Development) -> list[Row]:
    """Return the rows when they hold the prediction contract; refuse missing columns, nulls and duplicates.

    Refused: a missing contract column, a null entity key, closing, model name, version or score, and the same
    entity, closing and model version twice.
    """
    checked: list[Row] = []
    seen: set[tuple[object, ...]] = set()
    required = (*development.spine_keys, MODEL_NAME, MODEL_VERSION, SCORE)
    for row in rows:
        missing = [c for c in (*development.spine_keys, *PREDICTION_COLUMNS) if c not in row]
        if missing:
            msg = f"prediction rows miss contract columns: {', '.join(missing)}"
            raise DatasetValidationError(msg)
        nulls = [c for c in required if row[c] is None]
        if nulls:
            msg = f"prediction row {key_text(row, development.spine_keys)} has null {', '.join(nulls)}"
            raise DatasetValidationError(msg)
        out = {**row, development.closing: _closing(row, development)}
        key = (*(out[k] for k in development.spine_keys), out[MODEL_VERSION])
        if key in seen:
            msg = f"prediction rows repeat entity, closing and model version {key!r}"
            raise DatasetValidationError(msg)
        seen.add(key)
        checked.append(out)
    return checked
