"""Model monitoring report and governance views on the selected workspace: tables and offline rows agree."""
# ruff: noqa: PT018 - dense assertions on view rows

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from bricksmith.monitor.model import parse_config
from bricksmith.monitor.model.actions import action_row
from bricksmith.monitor.model.cli import _table_rows  # pyright: ignore[reportPrivateUsage]
from bricksmith.monitor.model.engine import MemoryStore, check
from bricksmith.monitor.model.report import build_report, render_html, render_markdown
from bricksmith.monitor.model.tables import ACTIONS, ALERTS, DETAILS, RESULTS, RUNS, to_frame, view_statements

if TYPE_CHECKING:
    from argparse import Namespace

    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

MONTHS = ("2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06")
AS_OF = "2026-06-01"


def synthetic(drift_from: str) -> list[dict[str, Any]]:
    """Return scored rows; from ``drift_from`` the scores shift up, so the score PSI turns red."""
    rows = []
    for month in MONTHS:
        shift = 0.35 if month >= drift_from else 0.0
        for i in range(120):
            score = min(0.99, (i % 40) / 80 + shift)
            rows.append({"month": month, "sample": "dev" if month <= MONTHS[1] else "prod", "score": score,
                         "label": int((i * 7) % 100 < score * 100)})  # fmt: skip
    return rows


def declaration(model: str) -> dict[str, Any]:
    return {
        "model": model, "period": "month", "score": "score", "label": "label", "sample": "sample",
        "references": {"development": {"sample": "dev"}}, "model_version": "1", "min_rows": 10,
    }  # fmt: skip


def run_model(model: str, months: tuple[str, ...], drift_from: str, now: str) -> dict[str, list[dict[str, Any]]]:
    rows = [r for r in synthetic(drift_from) if r["month"] in months]
    return check(rows, parse_config(declaration(model)), MemoryStore(), run_id=f"run-{model}", now=now).tables


def test_governance_views_and_the_table_report_equal_the_offline_render(
    remote_context: ExecutionContext, remote_clients: Clients, test_schema: str
) -> None:
    spark = remote_clients.spark()
    scope = f"{remote_context.catalog}.{test_schema}"
    a = run_model("alpha", MONTHS, "2025-05", "2026-05-01T00:00:00")
    b = run_model("beta", MONTHS[:-1], "2026-01", "2026-05-02T00:00:00")  # one closing behind
    tables = {kind: a[kind] + b[kind] for kind in (RESULTS, DETAILS, ALERTS, RUNS)}
    red = next(x for x in a[ALERTS] if x["light"] == "red" and x["kind"] == "light")
    accept = action_row(model="alpha", alert_id=red["alert_id"], action="risk_accepted", reason="known shift",
                        author="ana", created_at="2026-05-03T00:00:00", valid_until="2999-12-31")  # fmt: skip
    tables[ACTIONS] = [accept]
    for kind, rows in tables.items():
        to_frame(spark, kind, rows).write.saveAsTable(f"{scope}.model_monitoring_{kind}")
    for statement in view_statements(scope, declared=("gamma",)):
        spark.sql(statement)

    def read(view: str) -> list[Any]:
        return spark.table(f"{scope}.model_monitoring_{view}").collect()

    coverage = {r["model"]: (r["monitored"], r["latest_period"]) for r in read("coverage_latest")}
    assert coverage == {"alpha": (1, "2025-06"), "beta": (0, "2025-05"), "gamma": (0, None)}
    summary = read("coverage_summary")[0]
    assert (summary["declared"], summary["monitored"], summary["latest_closing"]) == (3, 1, "2025-06")
    assert abs(summary["share_monitored"] - 1 / 3) < 1e-9
    assert any(r["model"] == "alpha" and r["severity"] == "critical" for r in read("open_alerts_by_severity"))
    assert {r["model"] for r in read("alert_recurrence")} <= {"alpha", "beta"}
    accepted = read("acceptance_summary")
    assert [(r["model"], r["active"], r["expired"]) for r in accepted] == [("alpha", 1, 0)]

    args: Namespace = type("Args", (), {"scope": scope, "prefix": "model_monitoring_", "model": "alpha"})()  # type: ignore[assignment]
    state = {kind: _table_rows(args, spark, kind) for kind in (RESULTS, RUNS, ACTIONS)}
    offline = {kind: tables[kind] for kind in (RESULTS, RUNS, ACTIONS)}
    from_tables = build_report(state, "alpha", as_of=AS_OF, generated="t")
    from_rows = build_report(offline, "alpha", as_of=AS_OF, generated="t")
    assert from_tables.periods == list(MONTHS[2:]) and from_tables.sections
    assert render_markdown(from_tables) == render_markdown(from_rows)
    assert render_html(from_tables) == render_html(from_rows)
    assert any(line.state == "accepted" for line in from_tables.alerts)
