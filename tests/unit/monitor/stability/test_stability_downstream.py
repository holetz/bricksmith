"""Downstream suppression, ownership on the rows, the severity score and routing of stability alerts."""
# ruff: noqa: PT018 - dense assertions and boolean declaration values

from __future__ import annotations

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.config import CRITICALITY_WEIGHTS, parse_config


def test_declaration_of_ownership_and_suppression() -> None:
    config = parse_config({"period": "month", "owner": " sales ", "domain": "orders", "criticality": "high"})
    assert (config.owner, config.domain, config.criticality, config.suppress_downstream) == (
        "sales", "orders", "high", True,
    )  # fmt: skip
    data = config.to_dict()
    assert data["owner"] == "sales" and data["criticality"] == "high" and "suppress_downstream" not in data
    assert parse_config({"period": "month", "suppress_downstream": False}).to_dict()["suppress_downstream"] is False
    assert CRITICALITY_WEIGHTS == {"low": 1, "medium": 2, "high": 3, "critical": 4}


def test_declaration_without_ownership_keeps_its_serialized_form() -> None:
    data = parse_config({"period": "month"}).to_dict()
    assert not {"owner", "domain", "criticality", "suppress_downstream"} & set(data)


@pytest.mark.parametrize(
    "bad",
    [{"criticality": "urgent"}, {"owner": 3}, {"domain": ["a"]}, {"suppress_downstream": "yes"}],
)
def test_declaration_rejects_bad_ownership(bad: dict[str, object]) -> None:
    with pytest.raises(MonitorError):
        parse_config({"period": "month", **bad})
