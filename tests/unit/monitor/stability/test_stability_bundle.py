"""The stability dashboard as a native bundle resource."""

from __future__ import annotations

import json
from typing import Any

import pytest

from bricksmith.errors import ValidationError
from bricksmith.monitor.stability.bundle import load_resources, stability_dashboard
from bricksmith.monitor.stability.dashboard import dashboard_definition

pytest.importorskip("databricks.bundles")


def _dashboard(resources: Any, key: str = "stability") -> Any:  # noqa: ANN401 - a native resource
    return resources.dashboards[key]


def test_the_dashboard_carries_the_generated_definition_and_the_scope() -> None:
    resources = stability_dashboard(dataset_catalog="main", dataset_schema="monitoring", warehouse_id="w1")
    dashboard = _dashboard(resources)
    assert dashboard.display_name == "Data stability"
    assert (dashboard.dataset_catalog, dashboard.dataset_schema, dashboard.warehouse_id) == ("main", "monitoring", "w1")
    assert json.loads(dashboard.serialized_dashboard) == dashboard_definition()


def test_the_prefix_reaches_the_datasets() -> None:
    dashboard = _dashboard(stability_dashboard(dataset_catalog="c", dataset_schema="s", prefix="st_"))
    assert "FROM st_series" in dashboard.serialized_dashboard


def test_load_resources_defers_every_target_to_bundle_variables() -> None:
    from databricks.bundles.core import Bundle  # noqa: PLC0415  # pyright: ignore[reportMissingImports]

    for target in ("dev", "prod"):
        dashboard = _dashboard(load_resources(Bundle(target=target)))
        assert str(dashboard.dataset_catalog) == "${var.dataset_catalog}"
        assert str(dashboard.dataset_schema) == "${var.dataset_schema}"
        assert str(dashboard.warehouse_id) == "${var.warehouse_id}"


def test_permissions_and_location_pass_through() -> None:
    resources = stability_dashboard(
        dataset_catalog="c", dataset_schema="s", parent_path="/Shared/stability",
        permissions=[{"level": "CAN_READ", "group_name": "analysts"}],
    )  # fmt: skip
    dashboard = _dashboard(resources)
    assert dashboard.parent_path == "/Shared/stability"
    assert dashboard.permissions[0].group_name == "analysts"


def test_a_missing_scope_is_refused() -> None:
    with pytest.raises(ValidationError):
        stability_dashboard(dataset_catalog="", dataset_schema="s")


def test_two_dashboards_can_share_a_bundle_under_different_keys() -> None:
    resources = stability_dashboard(dataset_catalog="c", dataset_schema="s", key="a")
    resources.add_resources(stability_dashboard(dataset_catalog="c", dataset_schema="s", key="b"))
    assert set(resources.dashboards) == {"a", "b"}
