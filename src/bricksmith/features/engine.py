"""The rows engine: long rows in, one row per entity and closing out, computed by the numpy reference.

:func:`compute_rows` takes plain rows (dicts); :func:`compute_frame` takes columnar numpy arrays and is shared
with the Arrow engine. Lags are whole periods between closings, so a missing closing is a null at its lag.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from bricksmith.features.catalog import INTEGER
from bricksmith.features.errors import FeatureError
from bricksmith.features.history import (
    check_point_in_time,
    duplicate_error,
    entity_key,
    period_index,
    to_date,
)
from bricksmith.features.reference import compute_many
from bricksmith.features.spec import CATEGORY, DIFFERENCE, RATIO, feature_name

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import date

    from numpy.typing import NDArray

    from bricksmith.features.spec import FeatureSpec


@dataclass(frozen=True)
class Frame:
    """Columnar input: entity codes, period indexes, numeric values (NaN null) and categorical values."""

    entities: NDArray[np.int64]
    periods: NDArray[np.int64]
    values: Mapping[str, NDArray[np.float64]]
    categories: Mapping[str, NDArray[np.object_]]


def lag_positions(frame: Frame, targets: NDArray[np.int64], window: int) -> NDArray[np.int64]:
    """Return, per target row and lag ``0 .. window - 1``, the row holding that closing, or ``-1``."""
    periods = frame.periods - frame.periods.min()
    span = int(periods.max()) + window + 1
    combined = frame.entities * span + periods
    order = np.argsort(combined, kind="stable")
    ordered = combined[order]
    if np.any(ordered[1:] == ordered[:-1]):
        msg = "an entity has more than one row at one closing"
        raise FeatureError(msg)
    positions = np.full((targets.size, window), -1, dtype=np.int64)
    for k in range(window):
        query = combined[targets] - k
        found = np.minimum(np.searchsorted(ordered, query), ordered.size - 1)
        hit = ordered[found] == query
        positions[:, k] = np.where(hit, order[found], -1)
    return positions


def _gather(values: NDArray[Any], positions: NDArray[np.int64], missing: Any) -> NDArray[Any]:  # noqa: ANN401 - NaN or None
    gathered = values[np.maximum(positions, 0)]
    return np.where(positions >= 0, gathered, missing)


def _operand(name: str, outputs: Mapping[str, NDArray[np.float64]], frame: Frame, targets: NDArray[np.int64]) -> Any:  # noqa: ANN401 - numpy array
    return outputs[name] if name in outputs else frame.values[name][targets]


def compute_frame(spec: FeatureSpec, frame: Frame, targets: NDArray[np.int64]) -> dict[str, NDArray[np.float64]]:
    """Return every output of ``spec`` for the ``targets`` rows of ``frame``, as float64 arrays with NaN null."""
    positions = lag_positions(frame, targets, spec.max_window)
    outputs: dict[str, NDArray[np.float64]] = {}
    wanted: dict[tuple[str, int], list[str]] = {}
    for output in spec.window_outputs():
        wanted.setdefault((output.column, output.window), []).append(output.aggregate)
    blocks = {c: _gather(frame.values[c], positions, np.nan).astype(np.float64) for c in spec.value_columns}
    for (column, window), names in wanted.items():
        values = compute_many(names, blocks[column][:, :window])
        for name in names:
            outputs[feature_name(column, name, window)] = values[name]
    gathered = {c: _gather(frame.categories[c], positions, None) for c in spec.categorical_columns}
    for output in spec.outputs:
        if output.kind == CATEGORY:
            window = gathered[output.column][:, : output.window]
            outputs[output.name] = (window == output.category).sum(axis=1).astype(np.float64)
        elif output.kind in (RATIO, DIFFERENCE):
            left = np.asarray(_operand(output.left, outputs, frame, targets), dtype=np.float64)
            right = np.asarray(_operand(output.right, outputs, frame, targets), dtype=np.float64)
            with np.errstate(invalid="ignore", divide="ignore"):
                value = left / right if output.kind == RATIO else left - right
            outputs[output.name] = np.where(right == 0, np.nan, value) if output.kind == RATIO else value
    return {o.name: outputs[o.name] for o in spec.outputs}


def to_python(value: float, dtype: str) -> int | float | None:
    """Return a computed value as a Python scalar: ``None`` for null, ``int`` for counts."""
    if np.isnan(value):
        return None
    return int(value) if dtype == INTEGER else float(value)


def _float(value: object) -> float:
    if value is None:
        return np.nan
    try:
        return float(value)  # pyright: ignore[reportArgumentType]
    except (TypeError, ValueError):
        msg = f"feature values must be numeric, got {value!r}"
        raise FeatureError(msg) from None


def compute_rows(
    rows: Iterable[Mapping[str, Any]], spec: FeatureSpec, as_of: date | str | None = None
) -> list[dict[str, Any]]:
    """Compute the features of every entity and closing of ``rows`` (or only at ``as_of``), in input order.

    Each output row carries the entity keys, the closing (a ``date``) and every output of the spec; counts are
    integers, other values floats, null is ``None``. Rows after ``as_of`` are refused (``point_in_time``).
    """
    data = list(rows)
    cutoff = to_date(as_of) if as_of is not None else None
    if not data:
        return []
    closings = [to_date(r[spec.closing]) for r in data]
    check_point_in_time(closings, cutoff)
    codes: dict[tuple[Any, ...], int] = {}
    entities = np.array([codes.setdefault(entity_key(r, spec.keys), len(codes)) for r in data], dtype=np.int64)
    frame = Frame(
        entities=entities,
        periods=np.array([period_index(c, spec.period) for c in closings], dtype=np.int64),
        values={c: np.array([_float(r.get(c)) for r in data], dtype=np.float64) for c in spec.value_columns},
        categories={
            c: np.array([None if r.get(c) is None else str(r[c]) for r in data], dtype=object)
            for c in spec.categorical_columns
        },
    )
    _refuse_duplicates(data, closings, spec, frame)
    targets = np.array(
        [i for i, c in enumerate(closings) if cutoff is None or c == cutoff],
        dtype=np.int64,
    )
    outputs = compute_frame(spec, frame, targets) if targets.size else {}
    dtypes = {o.name: o.dtype for o in spec.outputs}
    result: list[dict[str, Any]] = []
    for j, i in enumerate(targets.tolist()):
        row: dict[str, Any] = {k: data[i][k] for k in spec.keys}
        row[spec.closing] = closings[i]
        row.update({name: to_python(values[j], dtypes[name]) for name, values in outputs.items()})
        result.append(row)
    return result


def _refuse_duplicates(data: list[Mapping[str, Any]], closings: list[date], spec: FeatureSpec, frame: Frame) -> None:
    seen: set[tuple[int, int]] = set()
    for i, (entity, period) in enumerate(zip(frame.entities.tolist(), frame.periods.tolist(), strict=True)):
        if (entity, period) in seen:
            raise duplicate_error(entity_key(data[i], spec.keys), closings[i])
        seen.add((entity, period))
