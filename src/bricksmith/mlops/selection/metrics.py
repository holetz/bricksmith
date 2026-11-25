"""Pure statistics of the selection funnel, computed from counts and sums so every engine shares one definition.

Weight of evidence follows the credit-scoring convention ``WoE = ln(share of non-events / share of events)`` and
``IV = sum((share of non-events - share of events) * WoE)``; a group with an empty class gets ``smoothing`` added
to both of its counts. Gini is ``|2 * AUC - 1|`` and its direction is the sign of ``AUC - 0.5``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.bins import NULL, BinSpec, is_null
from bricksmith.monitor.stats import auc_groups, chi2_ppf, gamma_p, ks_groups, psi_critical

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

RIDGE = 1e-9
SINGULAR = 1e-12
#: Variance below this share of the raw second moment is treated as zero (a constant column).
FLAT = 1e-12


def quality_metrics(counts: Mapping[Any, int]) -> dict[str, float]:
    """Return ``missing_share``, ``constant_share`` (top non-null value share) and ``cardinality`` of a count map."""
    total = sum(counts.values())
    present = {k: c for k, c in counts.items() if not is_null(k) and c > 0}
    non_null = sum(present.values())
    return {
        "missing_share": (total - non_null) / total if total else 1.0,
        "constant_share": max(present.values()) / non_null if non_null else 1.0,
        "cardinality": float(len(present)),
    }


@dataclass(frozen=True)
class Moments:
    """Sums of a pair of series over the rows where both are present: enough for a Pearson correlation."""

    n: float = 0.0
    sx: float = 0.0
    sy: float = 0.0
    sxx: float = 0.0
    syy: float = 0.0
    sxy: float = 0.0

    @classmethod
    def of(cls, pairs: Iterable[tuple[float, float]]) -> Moments:
        """Return the moments of ``(x, y)`` pairs."""
        n = sx = sy = sxx = syy = sxy = 0.0
        for x, y in pairs:
            n += 1
            sx += x
            sy += y
            sxx += x * x
            syy += y * y
            sxy += x * y
        return cls(n, sx, sy, sxx, syy, sxy)

    def correlation(self) -> float | None:
        """Return the Pearson correlation, or ``None`` with fewer than two rows or a constant series."""
        if self.n < 2:  # noqa: PLR2004 - a correlation needs two rows
            return None
        vx = self.sxx - self.sx * self.sx / self.n
        vy = self.syy - self.sy * self.sy / self.n
        if vx <= FLAT * max(abs(self.sxx), 1.0) or vy <= FLAT * max(abs(self.syy), 1.0):
            return None
        value = (self.sxy - self.sx * self.sy / self.n) / math.sqrt(vx * vy)
        return max(-1.0, min(1.0, value))


def mid_rank_map(counts: Mapping[float, int]) -> dict[float, float]:
    """Return ``value -> mid rank`` (1-based, ties share the mean of their positions) from value counts."""
    ranks: dict[float, float] = {}
    position = 0
    for value in sorted(counts):
        count = counts[value]
        ranks[value] = position + (count + 1) / 2
        position += count
    return ranks


def rank_moments(pairs: Sequence[tuple[float, float]]) -> Moments:
    """Return the moments of the mid ranks of ``(x, y)`` pairs; their correlation is Spearman's rho."""
    x_counts: dict[float, int] = {}
    y_counts: dict[float, int] = {}
    for x, y in pairs:
        x_counts[x] = x_counts.get(x, 0) + 1
        y_counts[y] = y_counts.get(y, 0) + 1
    rx, ry = mid_rank_map(x_counts), mid_rank_map(y_counts)
    return Moments.of((rx[x], ry[y]) for x, y in pairs)


def eta(groups: Mapping[Any, tuple[float, float, float]]) -> float | None:
    """Return the correlation ratio from ``category -> (rows, sum y, sum y^2)``: ``sqrt(between SS / total SS)``."""
    n = sum(g[0] for g in groups.values())
    if n < 2:  # noqa: PLR2004 - a ratio needs two rows
        return None
    total = sum(g[1] for g in groups.values())
    squares = sum(g[2] for g in groups.values())
    sst = squares - total * total / n
    if sst <= FLAT * max(abs(squares), 1.0):
        return None
    ssb = sum(s * s / c for c, s, _ in groups.values() if c) - total * total / n
    return math.sqrt(max(0.0, min(1.0, ssb / sst)))


def discrimination(groups: Mapping[float, tuple[int, int]]) -> dict[str, float] | None:
    """Return ``ks``, ``gini`` and ``direction`` (+1/-1/0) from ``score -> (events, non-events)``.

    Returns ``None`` when only one class is present.
    """
    try:
        auc = auc_groups(groups)
        ks = ks_groups(groups)
    except ZeroDivisionError:
        return None
    direction = 1.0 if auc > 0.5 else -1.0 if auc < 0.5 else 0.0  # noqa: PLR2004 - AUC of a random score
    return {"ks": ks, "gini": abs(2 * auc - 1), "direction": direction}


def rate_groups(by_category: Mapping[Any, tuple[int, int]]) -> dict[float, tuple[int, int]]:
    """Order categories by event rate: ``event rate -> (events, non-events)``, equal rates pooled."""
    groups: dict[float, tuple[int, int]] = {}
    for events, non_events in by_category.values():
        if events + non_events == 0:
            continue
        rate = events / (events + non_events)
        e, n = groups.get(rate, (0, 0))
        groups[rate] = (e + events, n + non_events)
    return groups


@dataclass(frozen=True)
class WoeGroup:
    """One weight-of-evidence group: the bin ids it pools, its class counts, WoE and IV contribution."""

    bins: tuple[str, ...]
    events: int
    non_events: int
    woe: float
    iv: float

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {"bins": list(self.bins), "events": self.events, "iv": self.iv, "non_events": self.non_events,
                "woe": self.woe}  # fmt: skip


@dataclass(frozen=True)
class WoeTable:
    """A fitted weight-of-evidence encoding of one variable (fitted on development; never stored as a feature)."""

    variable: str
    spec: BinSpec
    groups: tuple[WoeGroup, ...]
    _index: dict[str, float] = field(default_factory=dict, compare=False, repr=False)

    def __post_init__(self) -> None:
        """Index bin id -> WoE once."""
        self._index.update({b: g.woe for g in self.groups for b in g.bins})

    @property
    def iv(self) -> float:
        """Return the information value."""
        return sum(g.iv for g in self.groups)

    def woe_of(self, value: object) -> float:
        """Return the WoE of ``value``; a bin with no development rows (unseen, out of range) is neutral (0)."""
        return self._index.get(self.spec.assign(value), 0.0)

    def to_dict(self) -> dict[str, Any]:
        """Return the plain form."""
        return {"bins": self.spec.to_dict(), "groups": [g.to_dict() for g in self.groups], "iv": self.iv,
                "variable": self.variable}  # fmt: skip


def monotone_groups(groups: Sequence[tuple[tuple[str, ...], int, int]], *, increasing: bool) -> list[tuple]:
    """Pool adjacent groups until the event rate is monotone in ``increasing`` direction (pool adjacent violators)."""
    stack: list[tuple[tuple[str, ...], int, int]] = []
    for group in groups:
        stack.append(group)
        while len(stack) > 1:
            (bins_a, e_a, n_a), (bins_b, e_b, n_b) = stack[-2], stack[-1]
            rate_a, rate_b = e_a / (e_a + n_a), e_b / (e_b + n_b)
            if (rate_a <= rate_b) if increasing else (rate_a >= rate_b):
                break
            stack[-2:] = [((*bins_a, *bins_b), e_a + e_b, n_a + n_b)]
    return stack


def woe_table(
    spec: BinSpec,
    class_counts: Mapping[str, tuple[int, int]],
    *,
    smoothing: float = 0.5,
    monotone: bool = False,
    increasing: bool = True,
) -> WoeTable:
    """Fit a WoE table from ``bin id -> (events, non-events)``; empty bins are left out, the null bin stays apart."""
    ordered = [
        ((b,), *class_counts.get(b, (0, 0)))
        for b in spec.bin_ids()
        if b != NULL and sum(class_counts.get(b, (0, 0))) > 0
    ]
    if monotone and spec.kind == "numeric":
        ordered = monotone_groups(ordered, increasing=increasing)
    null = class_counts.get(NULL, (0, 0))
    if sum(null) > 0:
        ordered.append(((NULL,), *null))
    adjusted = [(e + smoothing, n + smoothing) if e == 0 or n == 0 else (float(e), float(n)) for _, e, n in ordered]
    total_e = sum(e for e, _ in adjusted)
    total_n = sum(n for _, n in adjusted)
    groups = []
    for (bins, events, non_events), (e, n) in zip(ordered, adjusted, strict=True):
        share_e, share_n = e / total_e, n / total_n
        woe = math.log(share_n / share_e)
        groups.append(WoeGroup(tuple(bins), int(events), int(non_events), woe, (share_n - share_e) * woe))
    return WoeTable(spec.column, spec, tuple(groups))


def _inverse(matrix: Sequence[Sequence[float]]) -> list[list[float]]:
    size = len(matrix)
    work = [[*map(float, row), *(1.0 if i == j else 0.0 for j in range(size))] for i, row in enumerate(matrix)]
    for col in range(size):
        pivot = max(range(col, size), key=lambda r: abs(work[r][col]))
        if abs(work[pivot][col]) < SINGULAR:
            raise ZeroDivisionError
        work[col], work[pivot] = work[pivot], work[col]
        head = work[col][col]
        work[col] = [v / head for v in work[col]]
        for row in range(size):
            if row != col and work[row][col] != 0:
                factor = work[row][col]
                work[row] = [a - factor * b for a, b in zip(work[row], work[col], strict=True)]
    return [row[size:] for row in work]


def vif(correlations: Sequence[Sequence[float]]) -> list[float]:
    """Return each variable's variance inflation factor: the diagonal of the inverse correlation matrix.

    A tiny ridge keeps exactly collinear sets invertible, so their VIFs come out huge instead of failing.
    """
    size = len(correlations)
    if size < 2:  # noqa: PLR2004 - one variable has nothing to be collinear with
        return [1.0] * size
    ridged = [[v + (RIDGE if i == j else 0.0) for j, v in enumerate(row)] for i, row in enumerate(correlations)]
    try:
        inverse = _inverse(ridged)
    except ZeroDivisionError:
        return [math.inf] * size
    return [inverse[i][i] for i in range(size)]


def correlation_matrix(columns: Sequence[Sequence[float]]) -> list[list[float]]:
    """Return the Pearson correlation matrix of equal-length columns; an undefined correlation counts as 0."""
    size = len(columns)
    matrix = [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]
    for i in range(size):
        for j in range(i + 1, size):
            value = Moments.of(zip(columns[i], columns[j], strict=True)).correlation() or 0.0
            matrix[i][j] = matrix[j][i] = value
    return matrix


__all__ = [
    "Moments",
    "WoeGroup",
    "WoeTable",
    "chi2_ppf",
    "correlation_matrix",
    "discrimination",
    "eta",
    "gamma_p",
    "mid_rank_map",
    "monotone_groups",
    "psi_critical",
    "quality_metrics",
    "rank_moments",
    "rate_groups",
    "vif",
    "woe_table",
]
