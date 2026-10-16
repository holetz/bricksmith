"""Render a :class:`CatalogReport` as sorted JSON or escaped Markdown."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from bricksmith.catalogdocs.model import CATALOG_VERSION

if TYPE_CHECKING:
    from bricksmith.catalogdocs.model import CatalogReport


def to_dict(report: CatalogReport) -> dict[str, object]:
    """Return the serializable form."""
    return {
        "coverage": {
            "classified": list(report.classified),
            "unclassified": list(report.unclassified),
            "unknown": list(report.unknown),
        },
        "diagnostics": [
            {"code": code, "message": message, "severity": severity, "subject": subject}
            for severity, code, subject, message in report.diagnostics
        ],
        "incomplete": list(report.incomplete),
        "lineage": [
            {
                "consumer": x.consumer,
                "kind": x.kind,
                "producer": x.producer or None,
                "provenance": x.provenance,
                "target": x.target,
            }
            for x in report.lineage
        ],
        "models": [
            {"name": m.name, "tasks": [{"alias": a, "id": i, "role": r} for i, r, a in m.tasks]} for m in report.models
        ],
        "registry_loaded": report.registry_loaded,
        "registry_only": list(report.registry_only),
        "tables": [
            {
                "columns": [
                    {"description": c.description, "name": c.name, "sensitivity": c.sensitivity, "type": c.type}
                    for c in t.columns
                ],
                "description": t.description,
                "grain": list(t.grain),
                "name": t.name,
                "physical": t.physical,
                "producers": list(t.producers),
                "scope": t.scope,
                "status": t.status,
            }
            for t in report.tables
        ],
        "version": CATALOG_VERSION,
    }


def to_json(report: CatalogReport) -> str:
    """Return sorted, indented JSON, identical for identical reports."""
    return json.dumps(to_dict(report), indent=2, sort_keys=True) + "\n"


def _cell(text: object) -> str:
    value = str(text).replace("\\", "\\\\").replace("|", "\\|").replace("`", "\\`")
    for char in "*_<>[]":
        value = value.replace(char, "\\" + char)
    return " ".join(value.split()) or "-"


def _row(*cells: object) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _grid(header: tuple[str, ...], rows: list[str]) -> list[str]:
    return [_row(*header), "|" + " --- |" * len(header), *rows, ""]


def to_markdown(report: CatalogReport) -> str:
    """Return a Markdown catalog with every table cell escaped."""
    lines = ["# Catalog", ""]
    if not report.registry_loaded:
        lines += ["Registry: not provided; column metadata is incomplete.", ""]
    lines += ["## Tables", ""]
    lines += _grid(
        ("Table", "Physical", "Scope", "Producers", "Grain", "Status"),
        [
            _row(t.name, t.physical, t.scope, ", ".join(t.producers), ", ".join(t.grain), t.status)
            for t in report.tables
        ],
    )
    for t in report.tables:
        if t.columns:
            lines += [f"### {_cell(t.name)}", ""]
            if t.description:
                lines += [_cell(t.description), ""]
            lines += _grid(
                ("Column", "Type", "Sensitivity", "Description"),
                [_row(c.name, c.type, c.sensitivity, c.description) for c in t.columns],
            )
    lines += ["## Models", ""]
    lines += _grid(
        ("Model", "Task", "Role", "Alias"),
        [_row(m.name, i, r, a) for m in report.models for i, r, a in m.tasks],
    )
    lines += ["## Lineage", ""]
    lines += _grid(
        ("Consumer", "Target", "Producer", "Kind", "Provenance"),
        [_row(x.consumer, x.target, x.producer, x.kind, x.provenance) for x in report.lineage],
    )
    lines += ["## Sensitivity coverage", ""]
    lines += _grid(
        ("State", "Columns"),
        [
            _row("classified", ", ".join(report.classified)),
            _row("unclassified", ", ".join(report.unclassified)),
            _row("unknown", ", ".join(report.unknown)),
        ],
    )
    if report.registry_only:
        lines += ["## Registry tables without a producer", "", *(f"- {_cell(n)}" for n in report.registry_only), ""]
    if report.diagnostics:
        lines += ["## Diagnostics", ""]
        lines += _grid(("Severity", "Code", "Subject", "Message"), [_row(*d) for d in report.diagnostics])
    return "\n".join(lines).rstrip("\n") + "\n"
