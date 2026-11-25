"""The stability AI/BI (Lakeview) dashboard definition, generated as plain data.

:func:`dashboard_definition` returns the structure a ``.lvdash.json`` file holds, as a dict, so nothing generated is
committed and the bundle resource (:mod:`bricksmith.monitor.stability.bundle`) hands it to Databricks directly.
Datasets select unqualified view and table names: the catalog and schema come from the dashboard's own
``dataset_catalog`` / ``dataset_schema`` (or from ``scope`` for a standalone file), so one definition deploys to
every target. Every field a widget reads is checked against the columns its dataset carries
(:func:`dataset_columns`), the contract shared with :data:`bricksmith.monitor.stability.tables.VIEW_COLUMNS`.
"""

from __future__ import annotations

from typing import Any

from bricksmith.monitor.stability.tables import (
    PROFILES,
    RUNS,
    VIEW_COLUMNS,
    table_name,
)

#: The widget spec versions this definition uses; a Lakeview format change shows up as a diff here.
LVDASH_VERSION = {"counter": 2, "filter": 2, "line": 3, "bar": 3, "table": 1}
GRID_WIDTH = 6
_SUM = "SUM"
DISPLAY_NAME = "Data stability"
OVERVIEW_NOTE = "Latest period of every dataset: status, red and yellow results."

#: dataset name -> (view or table suffix, columns it selects, optional WHERE clause)
_DATASETS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "datasets": ("datasets", VIEW_COLUMNS["datasets"], ""),
    "status": ("dataset_status", VIEW_COLUMNS["dataset_status"], ""),
    "series": ("series", VIEW_COLUMNS["series"], ""),
    "bins": (
        PROFILES,
        ("dataset", "period", "column", "bin_label", "bin_order", "share", "baseline_share", "contribution"),
        "segment = ''",
    ),
    "incidents": ("incidents", VIEW_COLUMNS["incidents"], ""),
    "queue": (
        "open_alerts",
        (*VIEW_COLUMNS["open_alerts"], "metric", "column", "last_period", "opened_period", "last_value"),
        "",
    ),
    "runs": (
        RUNS,
        (
            "run_id",
            "dataset",
            "task_id",
            "status",
            "gate",
            "periods_profiled",
            "red",
            "yellow",
            "open_alerts",
            "started_at",
            "finished_at",
        ),
        "",
    ),
}


def dataset_columns(name: str) -> tuple[str, ...]:
    """Return the columns the dashboard dataset ``name`` carries (its select list)."""
    return _DATASETS[name][1]


def _source(name: str, prefix: str, scope: str | None) -> str:
    suffix = _DATASETS[name][0]
    return table_name(scope, prefix, suffix) if scope else f"{prefix}{suffix}"


def _dataset(name: str, prefix: str, scope: str | None) -> dict[str, Any]:
    _, columns, where = _DATASETS[name]
    lines = [f"SELECT {', '.join(f'`{c}`' for c in columns)}", f"FROM {_source(name, prefix, scope)}"]
    if where:
        lines.append(f"WHERE {where}")
    return {"name": name, "displayName": name.replace("_", " "), "queryLines": [f"{line}\n" for line in lines]}


def _field(column: str) -> dict[str, str]:
    return {"name": column, "expression": f"`{column}`"}


def _aggregate(function: str, column: str) -> dict[str, str]:
    return {"name": f"{function.lower()}({column})", "expression": f"{function.upper()}(`{column}`)"}


def _frame(title: str) -> dict[str, Any]:
    return {"showTitle": True, "title": title}


def _widget(name: str, dataset: str, fields: list[dict[str, str]], spec: dict[str, Any], *, detail: bool) -> dict:
    return {
        "name": name,
        "queries": [
            {"name": "main_query", "query": {"datasetName": dataset, "fields": fields, "disaggregated": detail}}
        ],
        "spec": spec,
    }


def _place(widget: dict[str, Any], x: int, y: int, width: int, height: int) -> dict[str, Any]:
    return {"widget": widget, "position": {"x": x, "y": y, "width": width, "height": height}}


def _filter(name: str, dataset: str, column: str) -> dict[str, Any]:
    spec = {
        "version": LVDASH_VERSION["filter"],
        "widgetType": "filter-multi-select",
        "encodings": {"fields": [{"fieldName": column, "displayName": column, "queryName": "main_query"}]},
        "frame": {"showTitle": True, "title": column},
    }
    return _widget(name, dataset, [_field(column)], spec, detail=False)


def _counter(name: str, dataset: str, function: str, column: str, title: str) -> dict[str, Any]:
    fields = [_aggregate(function, column)]
    spec = {
        "version": LVDASH_VERSION["counter"],
        "widgetType": "counter",
        "encodings": {"value": {"fieldName": fields[0]["name"], "displayName": title}},
        "frame": _frame(title),
    }
    return _widget(name, dataset, fields, spec, detail=False)


def _table(name: str, dataset: str, columns: tuple[str, ...], title: str) -> dict[str, Any]:
    spec = {
        "version": LVDASH_VERSION["table"],
        "widgetType": "table",
        "encodings": {"columns": [{"fieldName": c, "displayName": c} for c in columns]},
        "frame": _frame(title),
    }
    return _widget(name, dataset, [_field(c) for c in columns], spec, detail=True)


def _chart(  # noqa: PLR0913 - a chart is its axes, series, title and aggregate
    name: str, kind: str, dataset: str, *, x: str, ys: tuple[str, ...], title: str, aggregate: str = ""
) -> dict[str, Any]:
    if aggregate:
        fields = [_field(x), *(_aggregate(aggregate, y) for y in ys)]
        names = [f["name"] for f in fields[1:]]
    else:
        fields = [_field(x), *(_field(y) for y in ys)]
        names = list(ys)
    spec = {
        "version": LVDASH_VERSION[kind],
        "widgetType": kind,
        "encodings": {
            "x": {"fieldName": x, "scale": {"type": "categorical"}, "displayName": x},
            "y": {
                "scale": {"type": "quantitative"},
                "fields": [{"fieldName": n, "displayName": n} for n in names],
            },
        },
        "frame": _frame(title),
    }
    return _widget(name, dataset, fields, spec, detail=not aggregate)


def _text(name: str, lines: list[str]) -> dict[str, Any]:
    return {"name": name, "multilineTextboxSpec": {"lines": lines}}


def _page(name: str, title: str, layout: list[dict[str, Any]]) -> dict[str, Any]:
    return {"name": name, "displayName": title, "pageType": "PAGE_TYPE_CANVAS", "layout": layout}


def _overview() -> dict[str, Any]:
    return _page(
        "overview",
        "Overview",
        [
            _place(_text("overview_note", [OVERVIEW_NOTE]), 0, 0, 6, 1),
            _place(_filter("overview_dataset", "datasets", "dataset"), 0, 1, 2, 1),
            _place(_counter("overview_red", "datasets", "SUM", "red", "Red results"), 0, 2, 2, 2),
            _place(_counter("overview_yellow", "datasets", "SUM", "yellow", "Yellow results"), 2, 2, 2, 2),
            _place(_counter("overview_alerts", "datasets", "SUM", "open_alerts", "Open alerts"), 4, 2, 2, 2),
            _place(
                _chart(
                    "overview_reds",
                    "bar",
                    "status",
                    x="period",
                    ys=("red", "yellow"),
                    title="Red and yellow by period",
                    aggregate=_SUM,
                ),
                0,
                4,
                6,
                4,
            ),
            _place(
                _table(
                    "overview_table",
                    "datasets",
                    ("dataset", "latest_period", "status", "red", "yellow", "open_alerts", "last_run_at", "gate"),
                    "Datasets",
                ),
                0,
                8,
                6,
                5,
            ),
        ],
    )


def _series() -> dict[str, Any]:
    return _page(
        "series",
        "Series",
        [
            _place(_filter("series_dataset", "series", "dataset"), 0, 0, 2, 1),
            _place(_filter("series_column", "series", "column"), 2, 0, 2, 1),
            _place(_filter("series_metric", "series", "metric"), 4, 0, 2, 1),
            _place(
                _chart(
                    "series_chart",
                    "line",
                    "series",
                    x="period",
                    ys=("value", "green_limit", "red_limit", "trend_lower", "trend_upper", "trend_expected"),
                    title="Value, limits and trend band",
                ),
                0,
                1,
                6,
                6,
            ),
            _place(
                _table(
                    "series_table",
                    "series",
                    ("period", "segment_label", "column", "metric", "value", "light", "trend_status", "mark", "action"),
                    "Judgments, marks and actions",
                ),
                0,
                7,
                6,
                5,
            ),
        ],
    )


def _bins() -> dict[str, Any]:
    return _page(
        "bins",
        "PSI bins",
        [
            _place(_filter("bins_dataset", "bins", "dataset"), 0, 0, 2, 1),
            _place(_filter("bins_column", "bins", "column"), 2, 0, 2, 1),
            _place(_filter("bins_period", "bins", "period"), 4, 0, 2, 1),
            _place(
                _chart(
                    "bins_share",
                    "bar",
                    "bins",
                    x="bin_label",
                    ys=("share", "baseline_share"),
                    title="Share against baseline",
                ),
                0,
                1,
                3,
                5,
            ),
            _place(
                _chart(
                    "bins_contribution",
                    "bar",
                    "bins",
                    x="bin_label",
                    ys=("contribution",),
                    title="PSI contribution by bin",
                ),
                3,
                1,
                3,
                5,
            ),
        ],
    )


def _alerts() -> dict[str, Any]:
    return _page(
        "alerts",
        "Alerts and actions",
        [
            _place(_filter("alerts_dataset", "queue", "dataset"), 0, 0, 2, 1),
            _place(_counter("alerts_open", "incidents", "SUM", "open_alerts", "Open alerts"), 0, 1, 3, 2),
            _place(_counter("alerts_critical", "incidents", "SUM", "critical_alerts", "Critical alerts"), 3, 1, 3, 2),
            _place(
                _table(
                    "alerts_queue",
                    "queue",
                    ("dataset", "alert_id", "severity", "metric", "column", "effective_state", "action", "message"),
                    "Alerts and actions queue",
                ),
                0,
                3,
                6,
                5,
            ),
            _place(
                _table(
                    "alerts_incidents",
                    "incidents",
                    ("dataset", "period", "alerts", "open_alerts", "critical_alerts", "severity", "metrics"),
                    "Incidents",
                ),
                0,
                8,
                6,
                4,
            ),
        ],
    )


def _runs() -> dict[str, Any]:
    return _page(
        "runs",
        "Runs and cost",
        [
            _place(_filter("runs_dataset", "runs", "dataset"), 0, 0, 2, 1),
            _place(_counter("runs_count", "runs", "COUNT", "run_id", "Runs"), 0, 1, 3, 2),
            _place(_counter("runs_periods", "runs", "SUM", "periods_profiled", "Periods profiled"), 3, 1, 3, 2),
            _place(
                _chart(
                    "runs_profiled",
                    "bar",
                    "runs",
                    x="started_at",
                    ys=("periods_profiled",),
                    title="Periods profiled per run",
                    aggregate=_SUM,
                ),
                0,
                3,
                6,
                4,
            ),
            _place(
                _table(
                    "runs_table",
                    "runs",
                    ("run_id", "dataset", "task_id", "status", "gate", "periods_profiled", "started_at", "finished_at"),
                    "Runs",
                ),
                0,
                7,
                6,
                5,
            ),
        ],
    )


def dashboard_definition(prefix: str = "stability_", scope: str | None = None) -> dict[str, Any]:
    """Return the dashboard (the content of a ``.lvdash.json``) over the stability tables named by ``prefix``.

    Without ``scope`` the datasets name views and tables unqualified, for a dashboard that sets ``dataset_catalog``
    and ``dataset_schema``; with ``scope`` (``catalog.schema``) they are fully qualified, for a standalone file.
    """
    datasets = [_dataset(name, prefix, scope) for name in _DATASETS]
    pages = [_overview(), _series(), _bins(), _alerts(), _runs()]
    return {"datasets": datasets, "pages": pages}


def widget_fields(definition: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return ``(widget, dataset, column)`` for every plain column a widget reads (aggregates are unwrapped)."""
    found: list[tuple[str, str, str]] = []
    for page in definition["pages"]:
        for item in page["layout"]:
            widget = item["widget"]
            for query in widget.get("queries", []):
                body = query["query"]
                for field in body["fields"]:
                    column = (
                        field["expression"].rsplit("`", 2)[-2] if "`" in field["expression"] else field["expression"]
                    )
                    found.append((widget["name"], body["datasetName"], column))
    return found


__all__ = [
    "DISPLAY_NAME",
    "GRID_WIDTH",
    "LVDASH_VERSION",
    "dashboard_definition",
    "dataset_columns",
    "widget_fields",
]
