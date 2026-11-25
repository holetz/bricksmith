"""The dashboard views: the new datasets, series and incidents views and their column contract."""

from __future__ import annotations

import re

import pytest

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.stability.tables import RESULTS, SCHEMAS, VIEW_COLUMNS, table_name, view_statements

SCOPE = "main.monitoring"
NEW = ("datasets", "series", "incidents")


def _statement(name: str) -> str:
    target = f"CREATE OR REPLACE VIEW {table_name(SCOPE, 'stability_', name)} AS"
    return next(s for s in view_statements(SCOPE) if s.startswith(target))


def test_the_three_dashboard_views_are_created_after_the_views_they_read() -> None:
    statements = view_statements(SCOPE)
    heads = [re.match(r"CREATE OR REPLACE VIEW (\S+) AS", s).group(1) for s in statements]  # type: ignore[union-attr]
    for name in NEW:
        assert f"{SCOPE}.stability_{name}" in heads
    assert heads.index(f"{SCOPE}.stability_datasets") > heads.index(f"{SCOPE}.stability_dataset_status")
    assert heads.index(f"{SCOPE}.stability_series") > heads.index(f"{SCOPE}.stability_actions_latest")


@pytest.mark.parametrize("name", NEW)
def test_every_contract_column_is_selected_by_its_view(name: str) -> None:
    select = _statement(name)
    for column in VIEW_COLUMNS[name]:
        assert re.search(rf"(?<![A-Za-z_])`?{column}`?(?![A-Za-z_])", select), column


def test_series_carries_the_stored_band_limits_mark_and_action() -> None:
    result_columns = set(SCHEMAS[RESULTS])
    series = _statement("series")
    for column in ("trend_lower", "trend_upper", "trend_expected", "green_limit", "red_limit", "mark"):
        assert column in result_columns
        assert f"r.{column}" in series
    assert "l.action" in series
    assert "a.alert_id" in series
    assert "a.last_period = r.period" in series


def test_series_has_one_row_per_result_not_per_alert() -> None:
    # the alert join is on the series key and the period the alert was last seen: at most one alert per result
    assert "a.metric = r.metric" in _statement("series")


def test_incidents_group_alerts_per_dataset_and_opening_period() -> None:
    assert "GROUP BY dataset, opened_period" in _statement("incidents")


def test_datasets_view_has_no_owner_column() -> None:
    assert "owner" not in VIEW_COLUMNS["datasets"]


def test_new_views_reject_a_bad_scope() -> None:
    with pytest.raises(MonitorError):
        view_statements("bad scope")
