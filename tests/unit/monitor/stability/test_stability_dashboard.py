"""The generated dashboard definition: its structure, its data contract and the ``stability dashboard`` command."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bricksmith.cli.main import main
from bricksmith.monitor.stability.dashboard import (
    GRID_WIDTH,
    LVDASH_VERSION,
    dashboard_definition,
    dataset_columns,
    widget_fields,
)
from bricksmith.monitor.stability.tables import ALERTS, PROFILES, RUNS, SCHEMAS, VIEW_COLUMNS

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

PAGES = ["overview", "series", "bins", "alerts", "runs"]


def test_the_five_pages_cover_the_required_views() -> None:
    definition = dashboard_definition()
    assert [p["name"] for p in definition["pages"]] == PAGES
    assert [p["displayName"] for p in definition["pages"]] == [
        "Overview", "Series", "PSI bins", "Alerts and actions", "Runs and cost",
    ]  # fmt: skip


def test_every_widget_reads_a_field_its_dataset_carries() -> None:
    definition = dashboard_definition()
    known = {d["name"] for d in definition["datasets"]}
    seen = widget_fields(definition)
    assert seen
    for widget, dataset, column in seen:
        assert dataset in known, widget
        inner = column.split("(")[-1].rstrip(")") if "(" in column else column
        assert inner in dataset_columns(dataset), (widget, dataset, column)


def test_dataset_columns_come_from_the_view_contract_or_the_stored_tables() -> None:
    stored = {"bins": set(SCHEMAS[PROFILES]), "runs": set(SCHEMAS[RUNS])}
    for name, allowed in stored.items():
        assert set(dataset_columns(name)) <= allowed
    assert set(dataset_columns("series")) == set(VIEW_COLUMNS["series"])
    assert set(dataset_columns("datasets")) == set(VIEW_COLUMNS["datasets"])
    assert set(dataset_columns("queue")) - set(VIEW_COLUMNS["open_alerts"]) <= set(SCHEMAS[ALERTS])


def test_series_chart_draws_the_limits_and_the_trend_band() -> None:
    definition = dashboard_definition()
    chart = next(i["widget"] for p in definition["pages"] for i in p["layout"] if i["widget"]["name"] == "series_chart")
    drawn = {f["fieldName"] for f in chart["spec"]["encodings"]["y"]["fields"]}
    assert {"value", "green_limit", "red_limit", "trend_lower", "trend_upper"} <= drawn
    assert chart["spec"]["version"] == LVDASH_VERSION["line"]


def test_layout_stays_in_the_grid_without_overlap() -> None:
    for page in dashboard_definition()["pages"]:
        taken: set[tuple[int, int]] = set()
        for item in page["layout"]:
            pos = item["position"]
            assert pos["x"] >= 0 and pos["width"] > 0  # noqa: PT018
            assert pos["x"] + pos["width"] <= GRID_WIDTH
            cells = {
                (x, y)
                for x in range(pos["x"], pos["x"] + pos["width"])
                for y in range(pos["y"], pos["y"] + pos["height"])
            }
            assert not cells & taken, (page["name"], item["widget"]["name"])
            taken |= cells


def test_widget_names_are_unique() -> None:
    names = [i["widget"]["name"] for p in dashboard_definition()["pages"] for i in p["layout"]]
    assert len(names) == len(set(names))


def test_datasets_are_unqualified_by_default_and_qualified_with_a_scope() -> None:
    plain = {d["name"]: "".join(d["queryLines"]) for d in dashboard_definition()["datasets"]}
    scoped = {d["name"]: "".join(d["queryLines"]) for d in dashboard_definition("st_", "main.monitoring")["datasets"]}
    assert "FROM stability_series\n" in plain["series"]
    assert "FROM main.monitoring.st_series\n" in scoped["series"]
    assert "main." not in "".join(plain.values())


def test_the_structure_is_pinned() -> None:
    assert LVDASH_VERSION == {"counter": 2, "filter": 2, "line": 3, "bar": 3, "table": 1}
    definition = dashboard_definition()
    assert set(definition) == {"datasets", "pages"}
    assert all(p["pageType"] == "PAGE_TYPE_CANVAS" for p in definition["pages"])
    assert definition == json.loads(json.dumps(definition))  # plain JSON data


def test_dashboard_command_prints_and_writes_the_definition(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["stability", "dashboard", "--scope", "main.monitoring"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == dashboard_definition(scope="main.monitoring")
    target = tmp_path / "stability.lvdash.json"
    assert main(["stability", "dashboard", "--output", str(target)]) == 0
    capsys.readouterr()
    assert json.loads(target.read_text(encoding="utf-8")) == dashboard_definition()


def test_dashboard_command_refuses_a_bad_scope(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["stability", "dashboard", "--scope", "bad scope"]) != 0
    capsys.readouterr()
