"""A per-model monitoring report built from the stored tables and rendered as HTML or Markdown.

:func:`build_report` keeps the trusted rows of a model (the latest finished run per model version and monitoring
period, the rule the dashboard views apply) and arranges them as plain data: the period axis is the sorted union of
the periods found in the data, one section per metric family holds one series per ``(column, segment, metric)``,
and each series carries the development and out-of-time reference values beside its monitored periods. The alerts
are the ones the model's latest finished run stored (so the counts equal the run's), with the human action on each
one; a state with no stored alerts is replayed as the ``alerts`` verb does, with the persistence policy of the
optional ``config``.

:func:`render_html` returns one self-contained page (CSS tabs, inline SVG, no script and no external asset) and
:func:`render_markdown` the same sections as tables. Both escape every value they print.
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from bricksmith.monitor.errors import MonitorError
from bricksmith.monitor.model.actions import apply_actions
from bricksmith.monitor.model.alerts import replay_alerts, trusted
from bricksmith.monitor.model.config import ALL_FAMILIES, FAMILY_METRICS
from bricksmith.monitor.model.engine import open_counts

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from bricksmith.monitor.model.config import ModelMonitorConfig

GREEN, YELLOW, RED = "green", "yellow", "red"
_COLORS = {GREEN: "#2e7d32", YELLOW: "#f9a825", RED: "#c62828"}
_NEUTRAL = "#757575"
_FAMILY_OF = {metric: family for family, metrics in FAMILY_METRICS.items() for metric in metrics}


@dataclass(frozen=True)
class Point:
    """One monitored period of a series."""

    value: float | None
    light: str
    reason: str = ""
    rows: int | None = None


@dataclass(frozen=True)
class Series:
    """One ``(column, segment, metric)`` over the report's period axis, with its reference values."""

    column: str
    segment: str
    metric: str
    development: float | None
    out_of_time: float | None
    points: dict[str, Point]

    @property
    def label(self) -> str:
        """Return the series name: metric, column and segment, skipping the empty ones."""
        return " · ".join(part for part in (self.metric, self.column, self.segment) if part)


@dataclass(frozen=True)
class Section:
    """The series of one metric family."""

    family: str
    series: list[Series]


@dataclass(frozen=True)
class AlertLine:
    """An alert with the state a human action gave it."""

    alert_id: str
    severity: str
    kind: str
    metric: str
    column: str
    segment: str
    period: str
    state: str
    message: str
    action: str
    author: str
    reason: str
    valid_until: str


@dataclass(frozen=True)
class Report:
    """The data of one model's report."""

    model: str
    versions: list[str]
    periods: list[str]
    latest_period: str
    generated: str
    lights: dict[str, int]
    run: dict[str, Any]
    sections: list[Section]
    alerts: list[AlertLine]
    open_alerts: int = 0
    critical_alerts: int = 0
    notes: list[str] = field(default_factory=list)


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _family(row: Mapping[str, Any]) -> str:
    return str(row.get("family") or _FAMILY_OF.get(str(row.get("metric")), "other"))


def _order(family: str) -> tuple[int, str]:
    return (ALL_FAMILIES.index(family), family) if family in ALL_FAMILIES else (len(ALL_FAMILIES), family)


def _reference(points: Sequence[Mapping[str, Any]], column: str) -> float | None:
    """Return the latest stored reference value of a series (references are constant within a binning)."""
    for row in sorted(points, key=lambda r: str(r["monitoring_period"]), reverse=True):
        found = _number(row.get(column))
        if found is not None:
            return found
    return None


def _series(rows: Sequence[Mapping[str, Any]]) -> list[Series]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("column") or ""), str(row.get("segment") or ""), str(row["metric"]))
        groups.setdefault(key, []).append(row)
    built = []
    for (column, segment, metric), found in sorted(groups.items(), key=lambda kv: (kv[0][2], kv[0][0], kv[0][1])):
        points = {
            str(r["monitoring_period"]): Point(
                _number(r.get("value")), str(r.get("light") or ""), str(r.get("reason") or ""),
                None if r.get("rows") is None else int(r["rows"]),
            )
            for r in found
        }  # fmt: skip
        built.append(
            Series(
                column,
                segment,
                metric,
                _reference(found, "development_value"),
                _reference(found, "out_of_time_value"),
                points,
            )
        )
    return built


def _alert_line(alert: Mapping[str, Any]) -> AlertLine:
    return AlertLine(
        alert_id=str(alert.get("alert_id") or ""),
        severity=str(alert.get("severity") or ""),
        kind=str(alert.get("kind") or ""),
        metric=str(alert.get("metric") or ""),
        column=str(alert.get("column") or ""),
        segment=str(alert.get("segment") or ""),
        period=str(alert.get("monitoring_period") or ""),
        state=str(alert.get("state") or "open"),
        message=str(alert.get("message") or ""),
        action=str(alert.get("action") or ""),
        author=str(alert.get("action_author") or ""),
        reason=str(alert.get("action_reason") or ""),
        valid_until=str(alert.get("valid_until") or ""),
    )


def _model_alerts(  # noqa: PLR0913 - one keyword per input
    state: Mapping[str, Iterable[Mapping[str, Any]]], model: str, last: Mapping[str, Any], *, stamp: str, as_of: str,
    config: ModelMonitorConfig | None,
) -> list[dict[str, Any]]:  # fmt: skip
    """Return the alerts of ``model``: the stored ones of its latest finished run, else a replay."""
    actions = list(state.get("actions", []))
    if "alerts" in state:
        mine = [a for a in state["alerts"] if a.get("model") == model and a.get("run_id") == last.get("run_id")]
        return apply_actions(mine, actions, as_of or stamp[:10])
    _, alerts = replay_alerts(
        list(state.get("results", [])), list(state.get("runs", [])), config=config, created_at=stamp,
        actions=actions, as_of=as_of or stamp[:10],
    )  # fmt: skip
    return [a for a in alerts if a["model"] == model]


def build_report(
    state: Mapping[str, Iterable[Mapping[str, Any]]],
    model: str,
    *,
    as_of: str = "",
    generated: str = "",
    config: ModelMonitorConfig | None = None,
) -> Report:
    """Return the report of ``model`` from stored rows.

    ``state`` holds the ``results`` and ``runs`` row lists, and optionally ``actions`` and ``alerts``. With
    ``alerts`` the report lists those of the model's latest finished run, so its counts equal the run's; without,
    the alerts are replayed from the stored lights with the persistence policy of ``config`` (default policy
    when none). ``as_of`` is the date
    risk acceptances are evaluated on (default: the generation date); ``generated`` stamps the report (default now).
    A model with no trusted result is a :class:`MonitorError`: an empty report would read as a healthy model.
    """
    results, runs = list(state.get("results", [])), list(state.get("runs", []))
    stamp = generated or datetime.now(UTC).isoformat(timespec="seconds")
    mine = [r for r in trusted(results, runs) if r["model"] == model]
    if not mine:
        models = sorted({str(r["model"]) for r in trusted(results, runs)})
        found = ", ".join(models) or "none"
        msg = f"model monitoring report: no finished-run results for model {model!r} (found: {found})"
        raise MonitorError(msg)
    periods = sorted({str(r["monitoring_period"]) for r in mine})
    latest = periods[-1]
    by_family: dict[str, list[Mapping[str, Any]]] = {}
    for row in mine:
        by_family.setdefault(_family(row), []).append(row)
    sections = [Section(f, _series(by_family[f])) for f in sorted(by_family, key=_order)]
    lights = {GREEN: 0, YELLOW: 0, RED: 0}
    for row in mine:
        if row["monitoring_period"] == latest and row.get("light") in lights:
            lights[str(row["light"])] += 1
    finished = [r for r in runs if r.get("model") == model and r.get("status") in ("success", "partial")]
    last = max(finished, key=lambda r: str(r.get("finished_at") or ""), default={})
    alerts = _model_alerts(state, model, last, stamp=stamp, as_of=as_of, config=config)
    lines = [_alert_line(a) for a in alerts]
    opened, critical = open_counts(alerts)
    run = {k: last.get(k) for k in ("run_id", "status", "gate", "finished_at", "model_version")}
    versions = sorted({str(r.get("model_version") or "") for r in mine})
    return Report(model, versions, periods, latest, stamp, lights, run, sections, lines, opened, critical)


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.4g}"


class _Scale:
    """Map periods and values to SVG coordinates."""

    width, height, left, top, bottom = 640, 150, 48, 10, 24

    def __init__(self, count: int, low: float, high: float) -> None:
        self.count = count
        self.low, self.high = (low - 0.5, high + 0.5) if high == low else (low, high)
        self.inner_w = self.width - self.left - 10
        self.inner_h = self.height - self.top - self.bottom

    def x(self, index: int) -> float:
        return self.left + (self.inner_w * index / (self.count - 1) if self.count > 1 else self.inner_w / 2)

    def y(self, value: float) -> float:
        return self.top + self.inner_h * (1 - (value - self.low) / (self.high - self.low))


def _lines(series: Series, periods: Sequence[str], scale: _Scale) -> list[str]:
    """Return the polylines of the series, broken at a period with no value."""
    out: list[str] = []
    run: list[str] = []
    for index, period in enumerate([*periods, ""]):
        point = series.points.get(period)
        if point is not None and point.value is not None:
            run.append(f"{scale.x(index):.1f},{scale.y(point.value):.1f}")
            continue
        if len(run) > 1:
            out.append(f'<polyline points="{" ".join(run)}" fill="none" stroke="{_NEUTRAL}"/>')
        run = []
    return out


def _dots(series: Series, periods: Sequence[str], scale: _Scale) -> list[str]:
    out = []
    for index, period in enumerate(periods):
        point = series.points.get(period)
        if point is None or point.value is None:
            continue
        title = html.escape(f"{period}: {_fmt(point.value)} ({point.light or 'no light'})")
        color = _COLORS.get(point.light, _NEUTRAL)
        cx, cy = f"{scale.x(index):.1f}", f"{scale.y(point.value):.1f}"
        out.append(f'<circle cx="{cx}" cy="{cy}" r="4" fill="{color}"><title>{title}</title></circle>')
    return out


def _chart(series: Series, periods: Sequence[str]) -> str:
    """Return an inline SVG of the series over ``periods`` with its reference values as horizontal lines."""
    values = [p.value for p in series.points.values() if p.value is not None]
    if not values:
        return ""
    references = [("development", series.development), ("out of time", series.out_of_time)]
    shown = [v for _, v in references if v is not None]
    scale = _Scale(len(periods), min(values + shown), max(values + shown))
    top, bottom, left = scale.top, scale.top + scale.inner_h, scale.left
    parts = [f'<svg viewBox="0 0 {scale.width} {scale.height}" role="img" aria-label="{html.escape(series.label)}">']
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#bbb"/>')
    parts += [
        f'<text x="{left - 4}" y="{scale.y(v) + 4:.1f}" text-anchor="end" font-size="10">{_fmt(v)}</text>'
        for v in (scale.high, scale.low)
    ]
    for (name, value), dash in zip(references, ("6 3", "2 3"), strict=True):
        if value is not None:
            line = f'<line x1="{left}" y1="{scale.y(value):.1f}" x2="{scale.width - 10}" y2="{scale.y(value):.1f}"'
            tip = html.escape(f"{name} {_fmt(value)}")
            parts.append(f'{line} stroke="#1565c0" stroke-dasharray="{dash}"><title>{tip}</title></line>')
    parts += _lines(series, periods, scale) + _dots(series, periods, scale)
    parts.append("</svg>")
    return "".join(parts)


def _chip(light: str) -> str:
    color = _COLORS.get(light, _NEUTRAL)
    return f'<span class="chip" style="background:{color}">{html.escape(light or "none")}</span>'


def _series_html(series: Series, periods: Sequence[str]) -> str:
    head = "".join(f"<th>{html.escape(p)}</th>" for p in periods)
    values = "".join(f"<td>{_cell(series.points.get(p))}</td>" for p in periods)
    reasons = "; ".join(f"{p}: {pt.reason}" for p, pt in series.points.items() if pt.reason and pt.value is None)
    note = f'<p class="note">{html.escape(reasons)}</p>' if reasons else ""
    return (
        f"<h4>{html.escape(series.label)}</h4>{_chart(series, periods)}"
        f"<table><tr><th>development</th><th>out of time</th>{head}</tr>"
        f"<tr><td>{_fmt(series.development)}</td><td>{_fmt(series.out_of_time)}</td>{values}</tr></table>{note}"
    )


def _cell(point: Point | None) -> str:
    if point is None:
        return ""
    value = html.escape(_fmt(point.value)) if point.value is not None else "-"
    return f"{value} {_chip(point.light)}"


_ALERT_COLUMNS = ("severity", "kind", "series", "period", "state", "action", "message")


def _alert_cells(a: AlertLine) -> tuple[str, ...]:
    series = " · ".join(p for p in (a.metric, a.column, a.segment) if p)
    action = f"{a.action} by {a.author}: {a.reason}" if a.action else ""
    if a.action and a.valid_until:
        action += f" (until {a.valid_until})"
    return (a.severity, a.kind, series, a.period, a.state, action, a.message)


def _alerts_html(report: Report) -> str:
    if not report.alerts:
        return "<p>No alerts.</p>"
    head = "<tr>" + "".join(f"<th>{c}</th>" for c in _ALERT_COLUMNS) + "</tr>"
    rows = []
    for a in report.alerts:
        series = " · ".join(p for p in (a.metric, a.column, a.segment) if p)
        action = (
            f"{a.action} by {a.author}: {a.reason}" + (f" (until {a.valid_until})" if a.valid_until else "")
            if a.action
            else ""
        )
        cells = (a.severity, a.kind, series, a.period, a.state, action, a.message)
        rows.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
    return f"<table>{head}{''.join(rows)}</table>"


_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:1.5rem;color:#212121}h1{margin:0 0 .5rem}"
    "table{border-collapse:collapse;margin:.4rem 0 1rem;font-size:.85rem}"
    "td,th{border:1px solid #ccc;padding:.2rem .5rem}"
    ".chip{color:#fff;border-radius:.6rem;padding:0 .4rem;font-size:.75rem}.note{color:#555;font-size:.8rem}"
    "input.tab{display:none}nav label{display:inline-block;padding:.3rem .8rem;border:1px solid #ccc;cursor:pointer;"
    "margin:0 .2rem .2rem 0}"
    ".panel{display:none}svg{max-width:100%;height:auto;font-family:inherit}"
)


def render_html(report: Report) -> str:
    """Return the report as one self-contained HTML page (CSS tabs, inline SVG, no script, no external asset)."""
    names = ["Overview", *[s.family for s in report.sections], "Alerts"]
    radios = "".join(
        f'<input class="tab" type="radio" name="tab" id="t{i}"{" checked" if i == 0 else ""}>'
        for i in range(len(names))
    )
    labels = "".join(f'<label for="t{i}">{html.escape(n)}</label>' for i, n in enumerate(names))
    rules = "".join(
        f"#t{i}:checked~#p{i}{{display:block}}#t{i}:checked~nav label[for=t{i}]{{background:#e3f2fd;font-weight:600}}"
        for i in range(len(names))
    )
    lights = " ".join(f"{_chip(k)} {v}" for k, v in report.lights.items())
    overview = (
        f"<p>Model <b>{html.escape(report.model)}</b>, version(s) {html.escape(', '.join(report.versions) or '-')}. "
        f"Periods {html.escape(report.periods[0])} to {html.escape(report.periods[-1])} ({len(report.periods)}). "
        f"Latest period {html.escape(report.latest_period)}: {lights}.</p>"
        f"<p>Last finished run: {html.escape(str(report.run.get('run_id') or '-'))} "
        f"({html.escape(str(report.run.get('status') or '-'))}, "
        f"gate {html.escape(str(report.run.get('gate') or '-'))}). "
        f"Open alerts: {report.open_alerts}, critical: {report.critical_alerts}.</p>"
    )
    panels = [f'<div class="panel" id="p0"><h2>Overview</h2>{overview}</div>']
    for i, section in enumerate(report.sections, start=1):
        body = "".join(_series_html(s, report.periods) for s in section.series)
        panels.append(f'<div class="panel" id="p{i}"><h2>{html.escape(section.family)}</h2>{body}</div>')
    last = len(names) - 1
    panels.append(f'<div class="panel" id="p{last}"><h2>Alerts</h2>{_alerts_html(report)}</div>')
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Model monitoring: {html.escape(report.model)}</title><style>{_STYLE}{rules}</style></head><body>"
        f"<h1>Model monitoring: {html.escape(report.model)}</h1>"
        f'<p class="note">Generated {html.escape(report.generated)}. '
        "Dashed lines: development and out-of-time references.</p>"
        f"{radios}<nav>{labels}</nav>{''.join(panels)}</body></html>\n"
    )


def _md(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: Report) -> str:
    """Return the report as Markdown: one table per family with ``dev`` and ``oot`` beside the periods."""
    out = [f"# Model monitoring: {report.model}", ""]
    out.append(
        f"- Versions: {', '.join(report.versions) or '-'}; periods {report.periods[0]} to {report.periods[-1]} "
        f"({len(report.periods)}); generated {report.generated}"
    )
    out.append(f"- Latest period {report.latest_period}: " + ", ".join(f"{v} {k}" for k, v in report.lights.items()))
    out.append(f"- Open alerts: {report.open_alerts} (critical {report.critical_alerts})")
    out.append("")
    for section in report.sections:
        out += [f"## {section.family}", ""]
        out.append("| series | dev | oot | " + " | ".join(_md(p) for p in report.periods) + " |")
        out.append("|---|---|---|" + "---|" * len(report.periods))
        for s in section.series:
            cells = []
            for p in report.periods:
                point = s.points.get(p)
                cells.append("" if point is None else f"{_fmt(point.value) or '-'} {point.light}".strip())
            out.append(f"| {_md(s.label)} | {_fmt(s.development)} | {_fmt(s.out_of_time)} | {' | '.join(cells)} |")
        out.append("")
    out += ["## Alerts", ""]
    if not report.alerts:
        out.append("No alerts.")
    else:
        out += ["| severity | kind | series | period | state | action | message |", "|---|---|---|---|---|---|---|"]
        for a in report.alerts:
            series = " · ".join(p for p in (a.metric, a.column, a.segment) if p)
            action = (
                f"{a.action} by {a.author}: {a.reason}" + (f" (until {a.valid_until})" if a.valid_until else "")
                if a.action
                else ""
            )
            out.append(
                "| "
                + " | ".join(_md(c) for c in (a.severity, a.kind, series, a.period, a.state, action, a.message))
                + " |"
            )
    return "\n".join(out) + "\n"


__all__ = [
    "AlertLine",
    "Point",
    "Report",
    "Section",
    "Series",
    "build_report",
    "render_html",
    "render_markdown",
]
