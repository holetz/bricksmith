"""Sample-size-aware PSI limits: no-change noise stays near alpha and a real shift is still red (synthetic)."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.monitor.limits import GREEN, RED, Limits
from bricksmith.monitor.stability import MemoryStore, check, parse_config
from bricksmith.monitor.stability.metrics import Sample, effective_limits, noise_floor
from bricksmith.monitor.stats import psi_terms

if TYPE_CHECKING:
    from bricksmith.monitor.stability import CheckResult

BINS = 10


def _draw(rng: random.Random, n: int, shares: list[float]) -> dict[str, int]:
    counts = dict.fromkeys((str(i) for i in range(BINS)), 0)
    for pick in rng.choices(range(BINS), weights=shares, k=n):
        counts[str(pick)] += 1
    return counts


def _rates(n: int, *, fixed: bool, smoothing: float = 0.0, trials: int = 400) -> tuple[float, float]:
    rng = random.Random(7)  # noqa: S311 - a seeded simulation, not a secret
    config = parse_config({"period": "p", "psi_limits": "fixed" if fixed else "sample_size"})
    limits = config.limits["psi"]
    flat = [1.0] * BINS
    yellow_or_worse = red = 0
    for _ in range(trials):
        base, cur = _draw(rng, 3 * n, flat), _draw(rng, n, flat)
        value = sum(t["contribution"] for t in psi_terms(base, cur, smoothing=smoothing).values())
        band = effective_limits(config, "psi", limits, Sample(n, 3 * n, BINS)).band(value)  # type: ignore[union-attr]
        yellow_or_worse += band != GREEN
        red += band == RED
    return yellow_or_worse / trials, red / trials


@pytest.mark.parametrize(("n", "smoothing"), [(30, 1.0), (100, 0.0), (400, 0.0), (5000, 0.0)])
def test_no_change_noise_stays_near_alpha_under_sample_size_limits(n: int, smoothing: float) -> None:
    # 30 rows over 10 bins is under ``min_rows_per_bin`` (suppressed by default); with Laplace smoothing it is judged.
    yellow, red = _rates(n, fixed=False, smoothing=smoothing)
    assert yellow <= 0.12  # alpha 0.05 plus Monte Carlo slack
    assert red <= 0.05  # alpha 0.01 plus slack


def test_fixed_limits_turn_small_samples_red_by_noise() -> None:
    _, red = _rates(30, fixed=True)
    assert red > 0.5  # the problem this option fixes


def test_a_real_shift_on_a_large_segment_is_still_red() -> None:
    config = parse_config({"period": "p"})
    rng = random.Random(3)  # noqa: S311 - seeded simulation
    base = _draw(rng, 15000, [1.0] * BINS)
    shifted = _draw(rng, 5000, [1.0] * 5 + [5.0] * 5)
    value = sum(t["contribution"] for t in psi_terms(base, shifted).values())
    limits = effective_limits(config, "psi", config.limits["psi"], Sample(5000, 15000, BINS))
    assert limits is not None
    assert limits.band(value) == RED


def test_the_floor_never_lowers_a_declared_limit_and_fixed_is_untouched() -> None:
    sample = Sample(1_000_000, 1_000_000, BINS)
    config = parse_config({"period": "p"})
    limits = config.limits["psi"]
    assert limits is not None
    assert effective_limits(config, "psi", limits, sample) == limits  # the floor is tiny on a huge table
    fixed = parse_config({"period": "p", "psi_limits": "fixed"})
    assert effective_limits(fixed, "psi", limits, Sample(30, 90, BINS)) == limits
    raised = effective_limits(config, "psi", limits, Sample(30, 90, BINS))
    assert raised is not None
    assert raised.green > limits.green
    assert raised.red > raised.green


def _rows(period: str, n: int, shift: bool = False) -> list[dict[str, object]]:  # noqa: FBT001, FBT002
    values = [0.5, 1.0, 2.0, 2.5]
    out: list[dict[str, object]] = []
    for i in range(n):
        x = values[(i % 3) if shift else i % 4]
        out.append({"month": period, "x": x})
    return out


def _psi_row(found: CheckResult) -> dict[str, Any]:
    return next(r for r in found.tables["results"] if r["metric"] == "psi" and r["period"] == "2026-02")


def _check(rows: list[dict[str, object]], **options: object) -> CheckResult:
    declared = {"period": "month", "columns": {"x": {"edges": [0, 1.5, 3]}}, "baseline": "previous", "min_rows": 0}
    return check(rows, parse_config({**declared, **options}), MemoryStore(), dataset="d", run_id="r", now="t")


def test_results_store_the_effective_limits_and_report_insufficient_volume() -> None:
    rows = _rows("2026-01", 60) + _rows("2026-02", 60, shift=True)
    fixed, aware = _psi_row(_check(rows, psi_limits="fixed")), _psi_row(_check(rows))
    assert fixed["green_limit"] == 0.10
    assert aware["green_limit"] > 0.10  # the stored limit is the one that judged
    assert aware["red_limit"] > aware["green_limit"]
    small = _rows("2026-01", 12) + _rows("2026-02", 12)
    assert _psi_row(_check(small))["status"] == "insufficient_volume"  # 12 rows over 3 bins: under 10 per bin
    assert _psi_row(_check(small, min_rows_per_bin=0))["status"] != "insufficient_volume"


def test_noise_floor_raises_limits_and_never_lowers_them() -> None:
    limits = Limits(0.10, 0.25, "higher")
    small = noise_floor(limits, Sample(30, 90, BINS))
    assert small is not None
    assert small.green > 0.10
    assert small.red > 0.25
    large = noise_floor(limits, Sample(10**7, 10**7, BINS))
    assert large == limits
    assert noise_floor(limits, Sample(30, 90, 1)) == limits
    assert noise_floor(limits, None) == limits
    assert noise_floor(None, Sample(30, 90, BINS)) is None
