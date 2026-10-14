"""Offline tests for model datasets: reproducibility, leakage, maturity, identity, split overlap, fingerprint, examples."""
# ruff: noqa: E501, PT018 - dense assertions on hand-derived values

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from bricksmith.cli.main import main
from bricksmith.errors import EXIT_OK, EXIT_VALIDATION
from bricksmith.graph import load_graph
from bricksmith.mlops import (
    DatasetError,
    DatasetValidationError,
    fingerprint,
    load_spec_file,
    parse_spec,
    validate_dataset,
)
from bricksmith.mlops.project import scan_project
from bricksmith.mlops.reference import (
    build_features,
    build_labels,
    build_population,
    build_prediction,
    build_training,
    sample_fraction,
)
from bricksmith.mlops.validate import check_feature_contract

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"
SHARED = EXAMPLES / "shared_schema"

# customer_id, age, active, tenure_months, spend, plan, churned, upsold
CUSTOMERS = [
    (1, 34, True, 12, 50.0, "basic", 0, 0),
    (2, 17, True, 3, 20.0, "basic", 0, 0),
    (3, 45, False, 40, 150.0, "pro", 1, 0),
    (4, 29, True, 8, 120.0, "pro", 1, 1),
    (5, 61, True, 60, 300.0, "pro", 0, 1),
    (6, 16, False, 2, 10.0, "basic", 0, 0),
    (7, 38, True, 24, 99.0, "basic", 0, 1),
    (8, 52, True, 18, 100.0, "pro", 1, 0),
]
NAMES = ("customer_id", "age", "active", "tenure_months", "spend", "plan", "churned", "upsold")


def source() -> list[dict[str, Any]]:
    return [
        {**dict(zip(NAMES, row, strict=True)), "snapshot_date": day, "feature_time": day}
        for day in ("2026-01-31", "2026-03-31")
        for row in CUSTOMERS
    ]


def churn() -> Any:  # noqa: ANN401
    return load_spec_file(SHARED / "models/churn_v1.toml")


def upsell() -> Any:  # noqa: ANN401
    return load_spec_file(SHARED / "models/upsell_v1.toml")


def training(spec: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    pop, _ = build_population(spec, source())
    return build_training(spec, pop, build_features(spec, source()), build_labels(spec, source()))


def test_seeded_sampling_and_exclusions_reproduce_hand_derived_membership() -> None:
    # sha256("7:<id>") first 32 bits / 2**32: ids 3, 4, 7, 8 fall below 0.5 (0.067, 0.011, 0.034, 0.076).
    assert [i for i in range(1, 9) if sample_fraction(7, str(i)) < 0.5] == [3, 4, 7, 8]
    pop, excluded = build_population(upsell(), source())
    assert sorted({r["customer_id"] for r in pop}) == [3, 7, 8]
    assert [(r["customer_id"], r["exclusion_reason"]) for r in excluded] == [(4, "low_tenure")] * 2
    assert {r["customer_id"]: r["segment"] for r in pop} == {3: "pro", 7: "basic", 8: "pro"}
    assert build_population(upsell(), source()) == (pop, excluded)  # repeatable
    other = replace(upsell(), sample=replace(upsell().sample, seed=8))
    assert build_population(other, source())[0] != pop  # a different seed selects differently


def test_exclusions_record_the_first_matching_reason_for_every_excluded_row() -> None:
    pop, excluded = build_population(churn(), source())
    reasons = sorted((r["customer_id"], r["snapshot_date"], r["exclusion_reason"]) for r in excluded)
    assert reasons == [
        (2, "2026-01-31", "minor"),
        (2, "2026-03-31", "minor"),
        (3, "2026-01-31", "inactive"),
        (3, "2026-03-31", "inactive"),
        (6, "2026-01-31", "minor"),  # also inactive; minor is declared first
        (6, "2026-03-31", "minor"),
    ]
    assert len(pop) + len(excluded) == 16 and {r["segment"] for r in pop} == {"high_spend", "standard"}


def test_training_prediction_and_valid_dataset() -> None:
    spec = churn()
    rows = training(spec)
    assert len(rows) == 10 and sum(r["split"] == "train" for r in rows) == 5
    assert sum(r["churned"] for r in rows) == 4
    assert validate_dataset(spec, rows).ok
    pop, _ = build_population(spec, source())
    predicted = build_prediction(spec, pop, build_features(spec, source()), "2026-03-31")
    assert sorted(r["customer_id"] for r in predicted) == [1, 4, 5, 7, 8] and "churned" not in predicted[0]


def test_future_features_and_labels_are_rejected() -> None:
    spec = churn()
    rows = training(spec)
    rows[0] = {**rows[0], "feature_time": "2026-06-01"}
    codes = validate_dataset(spec, rows).codes()
    assert {"feature_after_reference", "feature_after_snapshot"} <= set(codes)
    late = [{**r, "snapshot_date": "2026-06-01"} for r in training(spec)[:1]]
    assert "snapshot_after_reference" in validate_dataset(spec, late).codes()
    leaky = [{**r, "label_time": date(2026, 6, 1)} for r in training(spec)[:1]]
    assert "label_after_reference" in validate_dataset(spec, leaky).codes()
    with pytest.raises(DatasetValidationError, match="feature_after_reference"):
        validate_dataset(spec, rows).require()


def test_immature_label_is_diagnosed_before_fitting() -> None:
    spec = replace(churn(), reference_date=date(2026, 4, 15))  # 2026-03-31 + 30 days = 2026-04-30
    report = validate_dataset(spec, training(spec))
    finding = next(f for f in report.findings if f.code == "immature_label")
    assert finding.count == 5 and finding.examples[0].endswith("2026-03-31")
    kept = build_training(
        spec,
        build_population(spec, source())[0],
        build_features(spec, source()),
        build_labels(spec, source()),
        drop_immature=True,
    )
    assert len(kept) == 5 and validate_dataset(spec, kept).ok
    assert "label_missing" in validate_dataset(spec, [{**training(spec)[0], "churned": None}]).codes()


def test_split_overlaps_are_detected() -> None:
    spec = churn()
    rows = training(spec)
    assert "split_key_overlap" not in validate_dataset(spec, rows).codes()  # same keys across time is allowed
    strict = replace(spec, split=replace(spec.split, separate_keys=True))
    assert "split_key_overlap" in validate_dataset(strict, rows).codes()
    bad = [dict(r) for r in rows]
    bad[-1]["split"] = "train"  # a 2026-03-31 row mislabelled as train while evaluation rows remain
    codes = validate_dataset(spec, bad).codes()
    assert {"split_window", "split_time_overlap"} <= set(codes)
    long_horizon = replace(spec, label=replace(spec.label, maturity_days=60))
    assert "split_label_overlap" in validate_dataset(long_horizon, rows).codes()


def test_feature_contract_types_and_order() -> None:
    spec = churn()
    good = [
        ("customer_id", "int"),
        ("snapshot_date", "date"),
        ("tenure_months", "int"),
        ("spend", "double"),
        ("split", "string"),
    ]
    assert check_feature_contract(spec, good) == []
    assert check_feature_contract(spec, [*good[:2], ("tenure_months", "integer"), good[3]]) == []
    swapped = [good[0], good[3], good[2]]
    assert [f.code for f in check_feature_contract(spec, swapped)] == ["feature_order"]
    typed = [good[0], ("tenure_months", "bigint"), good[3]]
    assert [f.code for f in check_feature_contract(spec, typed)] == ["feature_type"]
    codes = {f.code for f in check_feature_contract(spec, [good[0], good[2], ("extra", "int")])}
    assert codes == {"feature_missing", "feature_unexpected"}


def test_fingerprint_is_stable_and_sensitive() -> None:
    spec = churn()
    base = fingerprint(spec, {"t.a": 3, "t.b": 1}, role="training")
    assert base == fingerprint(spec, {"t.b": 1, "t.a": 3}, role="training")
    assert fingerprint(spec, {"t.a": 4, "t.b": 1}, role="training").value != base.value
    assert (
        fingerprint(replace(spec, reference_date=date(2026, 5, 16)), {"t.a": 3, "t.b": 1}, role="training").value
        != base.value
    )
    assert fingerprint(spec, {"t.a": 3, "t.b": 1}, role="prediction").value != base.value
    assert fingerprint(spec, {"t.a": None}).unpinned == ("t.a",)
    assert fingerprint(upsell()).config != fingerprint(spec).config


def test_spec_requires_explicit_identity_and_valid_pieces() -> None:
    data = json.loads(
        json.dumps(
            {
                "columns": {"key": "id"},
                "features": [{"name": "x", "type": "int"}],
                "label": {"name": "y", "type": "int", "maturity_days": 1},
            }
        )
    )
    with pytest.raises(DatasetError, match="explicit identifier 'model'"):
        parse_spec(data)
    with pytest.raises(DatasetError, match="seed"):
        parse_spec({**data, "model": "m", "sample": {"fraction": 0.5}})
    with pytest.raises(DatasetError, match="collide"):
        parse_spec({**data, "model": "m", "features": [{"name": "id", "type": "int"}]})
    with pytest.raises(DatasetError, match="op must be"):
        parse_spec(
            {**data, "model": "m", "exclusions": [{"name": "r", "where": {"column": "a", "op": "like", "value": 1}}]}
        )


def copy_example(tmp_path: Path, name: str = "shared_schema") -> Path:
    target = tmp_path / name
    shutil.copytree(EXAMPLES / name, target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def selection(root: Path) -> dict[tuple[str, str], tuple[str, ...]]:
    return {(t.model, t.role): tuple(o.rsplit(".", 1)[-1] for o in t.outputs) for t in scan_project(root).tasks}


def test_identity_is_explicit_and_survives_renames_and_mapping(tmp_path: Path) -> None:
    root = copy_example(tmp_path)
    before = selection(root)
    assert {m for m, _ in before} == {"churn_v1", "upsell_v1"} and len(before) == 10
    (root / "catalogs/demo/ml").rename(root / "catalogs/demo/renamed_dir")
    after = selection(root)
    assert after == before and scan_project(root).ok
    (root / "bricksmith.toml").write_text('[mappings.schemas]\nml = "other_physical"\n', encoding="utf-8")
    assert selection(root) == before
    # A moved spec or a swapped identity is an error, never a silent switch to another model.
    shutil.copy(root / "models/upsell_v1.toml", root / "models/churn_v1.toml")
    findings = {f.code for f in scan_project(root).findings}
    assert "dataset_declaration" in findings


def test_scan_reports_missing_graph_dependencies_and_duplicates(tmp_path: Path) -> None:
    root = copy_example(tmp_path)
    path = root / "catalogs/demo/ml/churn.py"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('ctx.read("churn_labels")', 'ctx.read("churn_population")'), encoding="utf-8")
    scan = scan_project(root)
    assert any(f.code == "missing_dependency" and "labels" in f.message for f in scan.findings)
    twin = root / "catalogs/demo/ml/churn_twin.py"
    twin.write_text(re.sub(r"\bchurn_(?!v1)", "twin_", text), encoding="utf-8")
    assert "duplicate_role" in {f.code for f in scan_project(root).findings}


@pytest.mark.parametrize("name", ["shared_schema", "dedicated_scope"])
def test_examples_discover_and_validate_offline(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    root = EXAMPLES / name
    assert scan_project(root).ok
    graph = load_graph(root)
    assert graph.ok
    by_name = {t.name: t for t in graph.tasks}
    deps = {t: {graph.task(d).name for d in graph.dependencies(by_name[t].id)} for t in by_name}
    assert deps["churn_population"] == {"customer_features"} and deps["customer_features"] == {"customer_snapshots"}
    assert deps["churn_training"] == {"churn_population", "churn_features", "churn_labels"}
    assert deps["check_customer_features"] == {"customer_features"}
    assert deps["churn_prediction"] == {"churn_population", "churn_features"}
    schemas = {t.schema for t in graph.tasks}
    assert (schemas == {"ml"}) is (name == "shared_schema")
    assert {t.kind for t in graph.tasks} >= {"table", "feature", "check", "population", "training_dataset"}
    assert main(["dataset", "validate", "--project", str(root), "--json"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["dataset", "describe", "--project", str(root)]) == EXIT_OK
    assert "churn_v1" in capsys.readouterr().out


def test_cli_validate_fails_with_findings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = copy_example(tmp_path)
    (root / "models/upsell_v1.toml").unlink()
    assert main(["dataset", "validate", "--project", str(root)]) == EXIT_VALIDATION
    assert "dataset_declaration" in capsys.readouterr().out
