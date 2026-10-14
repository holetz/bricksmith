"""Parameter validation rejects ambiguous or invalid input."""

from __future__ import annotations

from datetime import date

import pytest

from bricksmith.errors import ValidationError
from bricksmith.params import (
    DateWindow,
    parse_segments,
    parse_selector,
    parse_window,
    parse_write_options,
    validate_parameters,
)


def test_window_forms() -> None:
    assert parse_window() is None
    assert parse_window(start="2026-01-01", end="2026-01-31") == DateWindow(date(2026, 1, 1), date(2026, 1, 31))
    assert parse_window(as_of="2026-03-10", lookback_days=3) == DateWindow(date(2026, 3, 8), date(2026, 3, 10))
    one = parse_window(as_of="2026-03-10")
    assert one is not None
    assert one.days == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start": "2026-01-01"},
        {"start": "2026-02-01", "end": "2026-01-01"},
        {"start": "2026-01-01", "end": "2026-01-02", "as_of": "2026-01-02"},
        {"lookback_days": 3},
        {"as_of": "2026-01-01", "lookback_days": 0},
        {"as_of": "01/02/2026"},
    ],
)
def test_window_rejections(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_window(**kwargs)  # type: ignore[arg-type]


def test_selector() -> None:
    sel = parse_selector(["silver.*", "cat.gold.t1"], ["silver.tmp"])
    assert sel.to_dict() == {"exclude": ["silver.tmp"], "include": ["silver.*", "cat.gold.t1"]}
    for include, exclude in (([], []), (["a"], []), (["a.b", "a.b"], []), (["a.b"], ["a.b"])):
        with pytest.raises(ValidationError):
            parse_selector(include, exclude)


def test_segments() -> None:
    assert dict(parse_segments(["tier=a,b", "region=south"])) == {"region": ("south",), "tier": ("a", "b")}
    for bad in (["x"], ["x="], ["x=a,,b"], ["x=a", "x=b"], ["x=a,a"], ["1x=a"]):
        with pytest.raises(ValidationError):
            parse_segments(bad)


def test_write_options() -> None:
    window = parse_window(as_of="2026-01-01")
    assert parse_write_options("merge", merge_keys=["id"]).merge_keys == ("id",)
    assert parse_write_options("replace_window", window=window).window == window
    assert parse_write_options("append").to_dict()["window"] is None
    bad: list[tuple[str, dict[str, object]]] = [
        ("upsert", {}),
        ("merge", {}),
        ("append", {"merge_keys": ["id"]}),
        ("replace_window", {}),
        ("overwrite", {"window": window}),
        ("merge", {"merge_keys": ["id", "id"]}),
    ]
    for mode, kwargs in bad:
        with pytest.raises(ValidationError):
            parse_write_options(mode, **kwargs)  # type: ignore[arg-type]


def test_parameters() -> None:
    assert validate_parameters({"a": 1, "b": None}) == {"a": 1, "b": None}
    with pytest.raises(ValidationError):
        validate_parameters({"a": [1]})  # type: ignore[dict-item]
