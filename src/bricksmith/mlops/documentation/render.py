"""Render the technical model document as Markdown and as self-contained HTML, deterministically.

The document is built once as sections of paragraphs, lists and tables, then written in either format. Every
number comes from an artifact and is formatted the same way every time; the only value that is not an artifact
is the explicit ``generated_at``. A section whose artifact the run does not carry says "Not recorded." and invents
nothing. Section names describe content and name no regulation.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from bricksmith.mlops.documentation.artifacts import DocumentInputs

NOT_RECORDED = "Not recorded."
#: The whole-sample metrics of the evaluation table, in column order.
EVALUATION_METRICS = ("row_count", "auc", "gini", "ks", "psi", "grade_psi", "brier", "ece", "inversions")
CLOSING_METRICS = ("row_count", "auc", "gini", "ks", "psi")
COUNTS = frozenset({"row_count", "inversions"})
SECTIONS = (
    "Document information",
    "Purpose and use",
    "Population and exclusions",
    "Target definition and maturity",
    "Sample windows",
    "Feature sources",
    "Feature selection",
    "Binning and weight of evidence",
    "Model specification",
    "Evaluation by sample",
    "Calibration by grade",
    "Stability",
    "Limitations",
    "Inherent risk and materiality",
)
_STYLE = (
    "body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:72rem;color:#1b1b1b;line-height:1.45}"
    "h1{font-size:1.7rem}h2{font-size:1.3rem;border-bottom:1px solid #ccc;padding-bottom:.2rem;margin-top:2rem}"
    "h3{font-size:1.05rem}table{border-collapse:collapse;margin:.6rem 0 1rem;font-size:.88rem}"
    "th,td{border:1px solid #ccc;padding:.25rem .5rem;text-align:left;vertical-align:top}"
    "th{background:#f2f2f2}td.n{text-align:right;font-variant-numeric:tabular-nums}"
)


@dataclass(frozen=True)
class Table:
    """A table: header names and rows of already formatted cells; ``numeric`` columns align right in HTML."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    numeric: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Section:
    """One document section: a title and blocks (text paragraphs, ``(heading,)`` subheadings, lists and tables)."""

    title: str
    blocks: tuple[str | tuple[str] | list[str] | Table, ...] = ()


@dataclass(frozen=True)
class Document:
    """The rendered document in both formats."""

    markdown: str
    html: str
    sections: tuple[Section, ...] = field(default=(), compare=False, repr=False)


def number(value: object) -> str:
    """Format a value for a cell: integers as is, floats with four decimals, ``None`` as ``n/a``."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "n/a" if value != value else f"{value:.4f}"  # noqa: PLR0124 - NaN check without math
    return str(value)


def _table(headers: Sequence[str], rows: Iterable[Sequence[object]], numeric: Iterable[int] = ()) -> Table:
    return Table(tuple(headers), tuple(tuple(number(v) for v in row) for row in rows), frozenset(numeric))


def _get(data: Mapping[str, Any] | None, key: str) -> Any:  # noqa: ANN401 - any JSON value
    return None if data is None else data.get(key)


# -- sections ------------------------------------------------------------------------------------------------------
def _information(inputs: DocumentInputs, generated_at: str) -> Section:
    meta = inputs.metadata or {}
    facts = [
        ("Model", inputs.title),
        ("Tracking run", inputs.run_id),
        ("Generated at", generated_at),
        ("Adapter", meta.get("adapter")),
        ("Task", meta.get("task")),
        ("Trained at", meta.get("trained_at")),
        ("Code revision", meta.get("code_revision")),
        ("Training snapshot", meta.get("snapshot")),
    ]
    return Section(SECTIONS[0], (_table(("Field", "Value"), [(k, v) for k, v in facts if v]),))


def _narrative(inputs: DocumentInputs, title: str, keys: Sequence[tuple[str, str]]) -> Section:
    blocks: list[str | tuple[str]] = []
    for key, heading in keys:
        if len(keys) > 1:
            blocks.append((heading,))
        blocks.append(inputs.narrative.get(key, NOT_RECORDED))
    return Section(title, tuple(blocks))


def _population(inputs: DocumentInputs) -> Section:
    blocks: list[Any] = [inputs.narrative.get("population", NOT_RECORDED)]
    rows = inputs.composition
    if not rows:
        return Section(SECTIONS[2], (*blocks, ("Counts by sample",), NOT_RECORDED))
    closing = str(_get(inputs.development, "closing") or "closing_date")
    totals: dict[str, dict[str, Any]] = {}
    seen: set[tuple[str, str]] = set()
    rules: dict[tuple[str, str], list[int]] = {}
    for row in rows:
        sample = str(row.get("sample") or "all")
        total = totals.setdefault(sample, {"closings": 0, "rows": 0, "excluded": 0})
        if (sample, str(row.get(closing))) not in seen:
            seen.add((sample, str(row.get(closing))))
            total["closings"] += 1
            total["rows"] += int(row["total"])
            total["excluded"] += int(row["excluded"])
        rule = rules.setdefault((sample, str(row["rule"])), [0, 0])
        rule[0] += int(row["met"])
        rule[1] += int(row["first"])
    counts = [
        (s, t["closings"], t["rows"], t["excluded"], t["rows"] - t["excluded"]) for s, t in sorted(totals.items())
    ]
    blocks += [
        ("Counts by sample",),
        _table(("Sample", "Closings", "Rows", "Excluded", "Retained"), counts, (1, 2, 3, 4)),
    ]
    order = [str(r["name"]) for r in _get(inputs.development, "exclusions") or ()]
    cascade = sorted(
        rules.items(),
        key=lambda item: (item[0][0], order.index(item[0][1]) if item[0][1] in order else len(order), item[0][1]),
    )
    blocks += [
        ("Exclusion cascade",),
        _table(
            ("Sample", "Rule", "Rows meeting it", "Rows excluded first by it"),
            [(s, r, m, f) for (s, r), (m, f) in cascade],
            (2, 3),
        ),
    ]
    return Section(SECTIONS[2], tuple(blocks))


def _target(inputs: DocumentInputs) -> Section:
    label = _get(inputs.development, "label")
    blocks: list[Any] = []
    if label:
        facts = [("Label", label.get("name")), ("Definition", label.get("definition")),
                 ("Forward window (periods)", label.get("horizon")),
                 ("Closing frequency", _get(inputs.development, "frequency")),
                 ("Labels observed as of", _get(inputs.development, "as_of"))]  # fmt: skip
        blocks.append(_table(("Field", "Value"), facts))
    else:
        blocks.append(NOT_RECORDED)
    if "target" in inputs.narrative:
        blocks.append(inputs.narrative["target"])
    return Section(SECTIONS[3], tuple(blocks))


def _windows(inputs: DocumentInputs) -> Section:
    samples = _get(inputs.development, "samples")
    if not samples:
        return Section(SECTIONS[4], (NOT_RECORDED,))
    rows = [(s["name"], s["role"], s["start"], s["end"]) for s in samples]
    return Section(SECTIONS[4], (_table(("Sample", "Role", "First closing", "Last closing"), rows),))


def _sources(inputs: DocumentInputs) -> Section:
    blocks: list[Any] = [("Feature store lookups",)]
    lookups = _get(inputs.development, "lookups")
    if lookups:
        rows = [(x["table"], ", ".join(x["keys"]) or "entity keys", x["offset"], x["prefix"], ", ".join(x["features"]))
                for x in lookups]  # fmt: skip
        blocks.append(_table(("Table", "Keys", "Closing offset", "Prefix", "Features"), rows, (2,)))
    else:
        blocks.append(NOT_RECORDED)
    blocks.append(("Input versions",))
    datasets = _get(inputs.metadata, "datasets") or {}
    versions = sorted(
        (role, table, version)
        for role, info in datasets.items()
        for table, version in (info.get("inputs") or {}).items()
    )
    blocks.append(_table(("Dataset", "Table", "Version"), versions) if versions else NOT_RECORDED)
    return Section(SECTIONS[5], tuple(blocks))


def _selection(inputs: DocumentInputs) -> Section:
    selection = inputs.selection or {}
    variables = selection.get("variables")
    if not variables:
        return Section(SECTIONS[6], (NOT_RECORDED,))
    selected = [v["variable"] for v in variables if v["selected"]]
    blocks: list[Any] = [
        f"Candidates: {len(variables)}. Selected: {len(selected)}.",
        ("Selected features",),
        list(selected) if selected else "None.",
        ("Outcome per variable",),
        _table(("Variable", "Kind", "Selected", "Left at", "Reason"),
               [(v["variable"], v["kind"], v["selected"], v["left_at"], v["reason"]) for v in variables]),
    ]  # fmt: skip
    ledger = selection.get("ledger") or []
    if ledger:
        ordered = sorted(ledger, key=lambda e: (e.get("step_order", 0), e["variable"], e["metric"]))
        blocks += [
            ("Selection ledger",),
            _table(("Variable", "Step", "Metric", "Value", "Threshold", "Rule", "Decision", "Reason"),
                   [(e["variable"], e["step"], e["metric"], e["value"], e["threshold"], e["rule"], e["decision"],
                     e["reason"]) for e in ordered], (3, 4)),
        ]  # fmt: skip
    return Section(SECTIONS[6], tuple(blocks))


def _bin_label(spec: Mapping[str, Any], bin_id: str) -> str:
    edges = spec.get("edges") or []
    if bin_id.startswith("bin_") and edges:
        i = int(bin_id.removeprefix("bin_"))
        return (
            f"({number(float(edges[i]))}, {number(float(edges[i + 1]))}]"
            if i
            else f"[{number(float(edges[0]))}, {number(float(edges[1]))}]"
        )
    return bin_id.removeprefix("cat:")


def _woe(inputs: DocumentInputs) -> Section:
    tables = (inputs.selection or {}).get("woe")
    if not tables:
        return Section(SECTIONS[7], (NOT_RECORDED,))
    blocks: list[Any] = []
    for variable, table in sorted(tables.items()):
        spec = table.get("bins") or {}
        rows = [(", ".join(_bin_label(spec, b) for b in g["bins"]), g["events"], g["non_events"], g["woe"], g["iv"])
                for g in table.get("groups", ())]  # fmt: skip
        blocks += [(f"{variable} (information value {number(float(table.get('iv', 0.0)))})",),
                   _table(("Bins", "Events", "Non-events", "WoE", "IV"), rows, (1, 2, 3, 4))]  # fmt: skip
    return Section(SECTIONS[7], tuple(blocks))


def _specification(inputs: DocumentInputs) -> Section:
    spec = inputs.specification
    if not spec:
        return Section(SECTIONS[8], (NOT_RECORDED,))
    blocks: list[Any] = [_table(("Field", "Value"), [("Estimator", spec.get("estimator")), ("Kind", spec.get("kind")),
                                                      ("Inputs", ", ".join(spec.get("features", ())))])]  # fmt: skip
    if spec.get("coefficients"):
        rows = [("(intercept)", spec.get("intercept"))] + [
            (c["feature"], c["coefficient"]) for c in spec["coefficients"]
        ]
        blocks += [("Coefficients",), _table(("Term", "Coefficient"), rows, (1,))]
    if spec.get("points"):
        scaling = spec.get("scaling") or {}
        blocks += [
            ("Score points",),
            (
                f"Scaling: {number(scaling.get('pdo'))} points to double the odds; "
                f"score {number(scaling.get('base_score'))} at odds {number(scaling.get('base_odds'))} "
                "(non-event to event)."
            ),
            _table(("Input", "Bins", "WoE", "Points"),
                   [(p["feature"], ", ".join(p["bins"]), p["woe"], p["points"]) for p in spec["points"]], (2, 3)),
        ]  # fmt: skip
    if spec.get("importance"):
        importance = [(r["feature"], r["importance"]) for r in spec["importance"]]
        blocks += [("Importance",), _table(("Feature", "Importance"), importance, (1,))]
    return Section(SECTIONS[8], tuple(blocks))


def _results(inputs: DocumentInputs) -> list[Mapping[str, Any]]:
    return list((inputs.evaluation or {}).get("results") or [])


def _samples(inputs: DocumentInputs, rows: Iterable[Mapping[str, Any]]) -> list[str]:
    declared = list(_get((inputs.evaluation or {}).get("evaluation"), "samples") or [])
    return declared + sorted({str(r["sample"]) for r in rows} - set(declared))


def _pivot(rows: Sequence[Mapping[str, Any]], key: tuple[str, str], metrics: Sequence[str]) -> dict[str, float | None]:
    """Return ``metric -> value`` of one sample and closing; counts (rows, inversions) as integers."""
    return {
        r["metric"]: int(r["value"]) if r["metric"] in COUNTS and r["value"] is not None else r["value"]
        for r in rows
        if (r["sample"], r["closing"]) == key and r["metric"] in metrics
    }


def _evaluation(inputs: DocumentInputs) -> Section:
    rows = [r for r in _results(inputs) if r["metric"] != "csi"]
    if not rows:
        return Section(SECTIONS[9], (NOT_RECORDED,))
    samples = _samples(inputs, rows)
    whole = [(s, *(_pivot(rows, (s, ""), EVALUATION_METRICS).get(m) for m in EVALUATION_METRICS)) for s in samples]
    present = [i for i, m in enumerate(EVALUATION_METRICS) if any(r[i + 1] is not None for r in whole)]
    headers = ("Sample", *(EVALUATION_METRICS[i] for i in present))
    reference = _get(_get((inputs.evaluation or {}).get("evaluation"), "config"), "reference") or "not recorded"
    blocks: list[Any] = [
        f"Every sample is compared with the reference sample ({reference}).",
        _table(headers, [(r[0], *(r[i + 1] for i in present)) for r in whole], range(1, len(headers))),
    ]
    closings = sorted({(str(r["sample"]), str(r["closing"])) for r in rows if r["closing"]},
                      key=lambda k: (samples.index(k[0]) if k[0] in samples else len(samples), k[1]))  # fmt: skip
    if closings:
        per = [(s, c, *(_pivot(rows, (s, c), CLOSING_METRICS).get(m) for m in CLOSING_METRICS)) for s, c in closings]
        blocks += [
            ("By closing",),
            _table(("Sample", "Closing", *CLOSING_METRICS), per, range(2, 2 + len(CLOSING_METRICS))),
        ]
    unavailable = sorted(
        {(r["sample"], r["metric"], r["reason"]) for r in rows if r["closing"] == "" and r["value"] is None}
    )
    if unavailable:
        blocks += [("Unavailable metrics",), _table(("Sample", "Metric", "Reason"), unavailable)]
    return Section(SECTIONS[9], tuple(blocks))


def _calibration(inputs: DocumentInputs) -> Section:
    grades = [g for g in (inputs.evaluation or {}).get("grades") or [] if g["closing"] == ""]
    if not grades:
        return Section(SECTIONS[10], (NOT_RECORDED,))
    samples = _samples(inputs, grades)
    ordered = sorted(
        grades, key=lambda g: (samples.index(g["sample"]), g["grade_order"] < 0, g["grade_order"], g["grade"])
    )
    rows = [(g["sample"], g["grade"], g["rows"], g["events"], g["observed_rate"], g["expected_rate"],
             g["observed_expected"], g["brier"], g["inversion"]) for g in ordered]  # fmt: skip
    headers = ("Sample", "Grade", "Rows", "Events", "Observed rate", "Expected rate", "Observed / expected", "Brier",
               "Inversion")  # fmt: skip
    return Section(SECTIONS[10], (_table(headers, rows, range(2, 8)),))


def _stability(inputs: DocumentInputs) -> Section:
    evaluation = inputs.evaluation or {}
    distribution = [d for d in evaluation.get("distribution") or [] if d["closing"] == "" and d["metric"] == "psi"]
    csi = [r for r in _results(inputs) if r["metric"] == "csi" and r["closing"] == ""]
    if not distribution and not csi:
        return Section(SECTIONS[11], (NOT_RECORDED,))
    blocks: list[Any] = []
    samples = _samples(inputs, [*distribution, *csi])
    if distribution:
        rows = sorted(distribution, key=lambda d: (samples.index(d["sample"]), d["bin"]))
        blocks += [("Score distribution against the reference",),
                   _table(("Sample", "Bin", "Reference share", "Sample share", "PSI contribution"),
                          [(d["sample"], d["bin"], d["reference_share"], d["current_share"], d["contribution"])
                           for d in rows], (2, 3, 4))]  # fmt: skip
    if csi:
        features = sorted({str(r["subject"]) for r in csi})
        index = {(r["sample"], r["subject"]): r["value"] for r in csi}
        blocks += [("Characteristic stability index (CSI) by feature",),
                   _table(("Feature", *samples), [(f, *(index.get((s, f)) for s in samples)) for f in features],
                          range(1, 1 + len(samples)))]  # fmt: skip
    return Section(SECTIONS[11], tuple(blocks))


def sections(inputs: DocumentInputs, generated_at: str) -> tuple[Section, ...]:
    """Return the document sections in order."""
    return (
        _information(inputs, generated_at),
        _narrative(inputs, SECTIONS[1], (("purpose", "Purpose"), ("intended_use", "Intended use"))),
        _population(inputs),
        _target(inputs),
        _windows(inputs),
        _sources(inputs),
        _selection(inputs),
        _woe(inputs),
        _specification(inputs),
        _evaluation(inputs),
        _calibration(inputs),
        _stability(inputs),
        _narrative(inputs, SECTIONS[12], (("limitations", "Limitations"),)),
        _narrative(inputs, SECTIONS[13], (("inherent_risk", "Inherent risk"), ("materiality", "Materiality"))),
    )


# -- formats -------------------------------------------------------------------------------------------------------
def _md_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def to_markdown(title: str, parts: Sequence[Section]) -> str:
    """Write sections as Markdown."""
    lines = [f"# Technical model document: {title}", ""]
    for section in parts:
        lines += [f"## {section.title}", ""]
        for block in section.blocks:
            if isinstance(block, Table):
                lines.append("| " + " | ".join(_md_cell(h) for h in block.headers) + " |")
                lines.append(
                    "|" + "|".join("---:" if i in block.numeric else "---" for i in range(len(block.headers))) + "|"
                )
                lines += ["| " + " | ".join(_md_cell(c) for c in row) + " |" for row in block.rows]
            elif isinstance(block, tuple):
                lines.append(f"### {block[0]}")
            elif isinstance(block, list):
                lines += [f"- {item}" for item in block]
            else:
                lines.append(block)
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def to_html(title: str, parts: Sequence[Section]) -> str:
    """Write sections as one self-contained HTML page: inline style, escaped text, no script or external resource."""
    e = html.escape
    out = ["<!DOCTYPE html>", '<html lang="en">', "<head>", '<meta charset="utf-8">',
           f"<title>Technical model document: {e(title)}</title>", f"<style>{_STYLE}</style>", "</head>", "<body>",
           f"<h1>Technical model document: {e(title)}</h1>"]  # fmt: skip
    for section in parts:
        out.append(f"<h2>{e(section.title)}</h2>")
        for block in section.blocks:
            if isinstance(block, Table):
                out.append("<table>")
                out.append("<tr>" + "".join(f"<th>{e(h)}</th>" for h in block.headers) + "</tr>")
                for row in block.rows:
                    cells = "".join(
                        f'<td class="n">{e(c)}</td>' if i in block.numeric else f"<td>{e(c)}</td>"
                        for i, c in enumerate(row)
                    )
                    out.append(f"<tr>{cells}</tr>")
                out.append("</table>")
            elif isinstance(block, tuple):
                out.append(f"<h3>{e(block[0])}</h3>")
            elif isinstance(block, list):
                out.append("<ul>" + "".join(f"<li>{e(item)}</li>" for item in block) + "</ul>")
            else:
                out.append(f"<p>{e(block)}</p>")
    out += ["</body>", "</html>"]
    return "\n".join(out) + "\n"


def now_text() -> str:
    """Return the current UTC time as ISO text to the second (the default ``generated_at``)."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def render_document(inputs: DocumentInputs, *, generated_at: str | None = None) -> Document:
    """Render the document; with the same inputs and ``generated_at`` the bytes are identical."""
    stamp = generated_at if generated_at is not None else now_text()
    parts = sections(inputs, stamp)
    return Document(markdown=to_markdown(inputs.title, parts), html=to_html(inputs.title, parts), sections=parts)


__all__ = ["NOT_RECORDED", "SECTIONS", "Document", "Section", "Table", "number", "render_document", "sections"]
