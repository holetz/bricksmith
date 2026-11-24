"""The technical model document renderer, offline, over artifacts produced by the real selection and evaluation."""

from __future__ import annotations

import re
from typing import Any

from bricksmith.mlops.documentation import DocumentInputs, development_files, model_specification, render_document
from bricksmith.mlops.documentation.artifacts import NARRATIVE, plain
from bricksmith.mlops.documentation.render import NOT_RECORDED, SECTIONS, number
from bricksmith.mlops.evaluation import evaluate
from bricksmith.mlops.selection import select_features
from bricksmith.mlops.stages import parse_development
from bricksmith.mlops.stages.rows import composition

DEVELOPMENT: dict[str, Any] = {
    "model": "attrition",
    "entity_keys": ["account_id"],
    "as_of": "2024-12-31",
    "samples": {
        "train": {"role": "train", "start": "2024-01-31", "end": "2024-02-29"},
        "oot": {"role": "out_of_time", "start": "2024-05-31", "end": "2024-05-31"},
    },
    "label": {"name": "left", "horizon": 3, "definition": "the account closes within the next three closings"},
    "exclusions": [{"name": "closed", "where": {"column": "status", "op": "eq", "value": "closed"}}],
    "bands": [{"name": "A", "upper": 0.3}, {"name": "B", "upper": 0.6}, {"name": "C"}],
    "lookups": [{"table": "main.features.account_monthly", "features": ["income", "tenure"], "offset": 0}],
}
NARRATIVE_TEXT = {
    "purpose": "Rank active accounts by the likelihood of leaving.",
    "intended_use": "Retention campaigns; not for pricing <decisions> & limits.",
    "limitations": "Trained on a synthetic population.",
    "inherent_risk": "Low: the score only orders a contact list.",
    "materiality": "Moderate.",
    "population": "Active accounts at each closing.",
    "target": "Closure is observed from the account ledger.",
}
CLOSINGS = {"train": ("2024-01-31", "2024-02-29"), "oot": ("2024-05-31",)}


class Logistic:
    """A fitted linear model, as an estimator exposes it."""

    coef_ = ((-0.9, -0.4),)
    intercept_ = (-1.1,)


def population() -> list[dict[str, Any]]:
    out = []
    for sample, closings in CLOSINGS.items():
        for closing in closings:
            for i in range(80):
                score = round(((i * 7) % 20) / 20 + (0.05 if sample == "oot" else 0.0), 4)
                out.append({"account_id": i, "closing_date": closing, "sample": sample,
                            "status": "closed" if i % 16 == 0 else "open", "score": score,
                            "left": int((i * 3) % 20 < score * 20), "grade": "A" if score <= 0.3 else
                            "B" if score <= 0.6 else "C", "income": float(i % 9), "tenure": float(i % 5)})  # fmt: skip
    return out


def inputs() -> DocumentInputs:
    rows = population()
    development = parse_development(DEVELOPMENT)
    files = development_files(development, composition(rows, development, by=("sample",)))
    selection = select_features(
        [r for r in rows if r["sample"] == "train"] + [r for r in rows if r["sample"] == "oot"],
        {
            "name": "attrition",
            "closing": "closing_date",
            "target": "left",
            "features": ["income", "tenure"],
            "development": ["2024-01-31", "2024-02-29"],
            "out_of_time": ["2024-05-31", "2024-05-31"],
            "steps": {"redundancy": False},
        },
    ).to_dict()
    evaluation = evaluate(
        rows,
        {"name": "attrition", "score": "score", "label": "left", "grade": "grade", "features": ["income", "tenure"]},
        development,
    ).to_dict()
    spec = model_specification(Logistic(), ["income_woe", "tenure_woe"], woe=selection["woe"],
                               scaling={"pdo": 20, "base_score": 600, "base_odds": 50})  # fmt: skip
    return DocumentInputs.from_files(
        {
            **files,
            NARRATIVE: NARRATIVE_TEXT,
            "documentation/specification.json": plain(spec),
            "selection/ledger.json": selection["ledger"],
            "selection/variables.json": selection["variables"],
            "selection/woe.json": selection["woe"],
            "evaluation/results.json": evaluation["results"],
            "evaluation/distribution.json": evaluation["distribution"],
            "evaluation/grades.json": evaluation["grades"],
            "evaluation/evaluation.json": {k: evaluation[k] for k in ("config", "name", "samples")},
            "model/bricksmith_model.json": {
                "model": "attrition",
                "adapter": "sklearn",
                "code_revision": "abc123",
                "datasets": {"train": {"inputs": {"main.features.account_monthly": 7}}},
            },
        },
        "run-1",
    )


def test_rendering_is_byte_identical_for_the_same_artifacts_and_stamp() -> None:
    first = render_document(inputs(), generated_at="2026-11-23T00:00:00+00:00")
    second = render_document(inputs(), generated_at="2026-11-23T00:00:00+00:00")
    assert first.markdown.encode() == second.markdown.encode()
    assert first.html.encode() == second.html.encode()
    later = render_document(inputs(), generated_at="2026-11-24T00:00:00+00:00")
    changed = [(a, b) for a, b in zip(first.markdown.splitlines(), later.markdown.splitlines(), strict=True) if a != b]
    assert changed == [("| Generated at | 2026-11-23T00:00:00+00:00 |", "| Generated at | 2026-11-24T00:00:00+00:00 |")]


def test_every_section_is_present_in_order_and_filled() -> None:
    document = render_document(inputs(), generated_at="t")
    headings = re.findall(r"^## (.+)$", document.markdown, flags=re.MULTILINE)
    assert tuple(headings) == SECTIONS
    assert NOT_RECORDED not in document.markdown
    expectations = (
        "| train | 2 | 160 | 10 | 150 |",
        "| oot | closed |",
        "the account closes within the next",
        "| main.features.account_monthly | entity keys | 0 |",
        "| train | main.features.account_monthly | 7 |",
        "### Selection ledger",
        "information value",
        "### Score points",
        "| (intercept) | -1.1000 |",
        "### By closing",
        "Observed / expected",
        "Characteristic stability index",
    )
    for expected in expectations:
        assert expected in document.markdown, expected


def test_evaluation_numbers_come_from_the_artifacts() -> None:
    data = inputs()
    document = render_document(data, generated_at="t")
    results = data.evaluation["results"] if data.evaluation else []
    auc = next(r["value"] for r in results if (r["sample"], r["closing"], r["metric"]) == ("oot", "", "auc"))
    row = next(line for line in document.markdown.splitlines() if line.startswith("| oot | 80 |"))
    assert number(auc) in row


def test_html_is_self_contained_and_escaped() -> None:
    page = render_document(inputs(), generated_at="t").html
    assert page.startswith("<!DOCTYPE html>")
    assert "<script" not in page
    assert "<link" not in page
    assert "src=" not in page
    assert "http" not in page
    assert "not for pricing &lt;decisions&gt; &amp; limits" in page


def test_section_names_are_regulator_neutral() -> None:
    document = render_document(inputs(), generated_at="t")
    text = (document.markdown + document.html).lower()
    for word in ("compliant", "compliance", "sr 11-7", "sr 26-2", "basel", "ifrs", "regulat"):
        assert word not in text, word


def test_a_run_without_artifacts_renders_not_recorded() -> None:
    document = render_document(DocumentInputs(run_id="run-0"), generated_at="t")
    assert document.markdown.count(NOT_RECORDED) >= len(SECTIONS) - 1
    assert "# Technical model document: model" in document.markdown
    assert "Not recorded." in document.html
