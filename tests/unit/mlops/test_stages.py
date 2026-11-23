"""Offline tests for model development stages: the declaration, the row engine and the import surface."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.mlops.errors import DatasetError, DatasetValidationError
from bricksmith.mlops.featurestore import parse_feature_table
from bricksmith.mlops.stages import (
    CASCADE_COLUMNS,
    PREDICTION_COLUMNS,
    Development,
    load_development_file,
    parse_development,
)
from bricksmith.mlops.stages.rows import (
    apply_cascade,
    attach_labels,
    build_abt,
    check_prediction,
    composition,
    prediction_rows,
    select_sample,
    stratify,
)

if TYPE_CHECKING:
    from pathlib import Path

HEAVY = ("pyspark", "databricks", "mlflow", "sklearn", "pandas", "numpy", "pyarrow")


def declaration(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 - plain metadata values
    base: dict[str, Any] = {
        "model": "churn",
        "entity_keys": ["account_id"],
        "as_of": "2024-12-31",
        "samples": {
            "dev": {"role": "train", "start": "2024-01-31", "end": "2024-04-30"},
            "holdout": {"role": "test", "start": "2024-03-31", "end": "2024-04-30"},
            "oot": {"role": "out_of_time", "start": "2024-05-31", "end": "2024-06-30"},
            "ttd": {"role": "through_the_door", "start": "2024-07-31", "end": "2024-09-30"},
            "live": {"role": "prediction", "start": "2024-10-31", "end": "2024-12-31"},
        },
        "label": {"name": "bad", "horizon": 3, "definition": "any 90-day delinquency in the next three closings"},
        "split": {"seed": 7, "test_fraction": 0.3},
        "exclusions": [
            {"name": "closed", "where": {"column": "status", "op": "eq", "value": "closed"}},
            {"name": "young", "where": {"column": "tenure", "op": "lt", "value": 3}},
        ],
        "bands": [{"name": "A", "upper": 0.1}, {"name": "B", "upper": 0.4}, {"name": "C"}],
    }
    base.update(overrides)
    return base


def test_importing_the_package_loads_no_heavy_module() -> None:
    modules = "bricksmith.mlops.stages, bricksmith.mlops.stages.rows, bricksmith.mlops.stages.spark"
    code = "\n".join(
        [f"import sys, {modules}", f"bad = [m for m in {HEAVY!r} if m in sys.modules]", "assert not bad, bad"]
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_declaration_parses_samples_roles_label_and_bands() -> None:
    dev = parse_development(declaration())
    assert dev.model == "churn" and dev.closing == "closing_date" and dev.frequency == "monthly"
    assert dev.spine_keys == ("account_id", "closing_date")
    assert [s.role for s in dev.samples] == ["train", "test", "out_of_time", "through_the_door", "prediction"]
    assert dev.sample("dev").labelled and not dev.sample("live").labelled
    assert dev.label is not None and dev.label.matures(date(2024, 6, 30), "monthly") == date(2024, 9, 30)
    assert [s.name for s in dev.shared(dev.sample("dev"))] == ["holdout"]
    assert dev.shared(dev.sample("oot")) == ()
    assert [dev.grade(x) for x in (0.05, 0.1, 0.2, 0.99, None)] == ["A", "A", "B", "C", None]
    assert dev.to_dict()["samples"][0] == {"end": "2024-04-30", "name": "dev", "role": "train", "start": "2024-01-31"}
    assert [r.name for r in dev.exclusions] == ["closed", "young"]


def test_out_of_time_overlapping_or_preceding_training_is_refused() -> None:
    samples = declaration()["samples"]
    for start in ("2024-04-30", "2024-02-29"):
        bad = {**samples, "oot": {"role": "out_of_time", "start": start, "end": "2024-06-30"}}
        with pytest.raises(DatasetError, match="must start after the last closing of training sample 'dev'"):
            parse_development(declaration(samples=bad))


def test_a_label_immature_at_the_upper_closing_is_refused() -> None:
    with pytest.raises(DatasetError, match=r"'oot'.*matures at 2024-09-30.*after as_of 2024-08-31"):
        parse_development(declaration(as_of="2024-08-31"))
    assert parse_development(declaration(as_of="2024-09-30")).as_of == date(2024, 9, 30)
    with pytest.raises(DatasetError, match="need a label and as_of"):
        parse_development(declaration(as_of=None))


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"samples": {"x": {"role": "validation", "start": "2024-01-31", "end": "2024-01-31"}}}, "role must be one"),
        ({"samples": {"x": {"role": "train", "start": "2024-01-15", "end": "2024-01-31"}}}, "not a monthly closing"),
        ({"samples": {"x": {"role": "train", "start": "2024-03-31", "end": "2024-01-31"}}}, "is after end"),
        ({"samples": {}}, "non-empty mapping"),
        ({"split": None}, "declare split"),
        ({"split": {"seed": 1, "test_fraction": 1}}, "below 1"),
        ({"exclusions": [{"name": "a", "where": {"column": "x", "op": "eq", "value": 1}}] * 2}, "unique"),
        ({"exclusions": [{"name": "a", "where": {"column": "x", "op": "like", "value": 1}}]}, "op must be one"),
        ({"bands": [{"name": "A", "upper": 0.5}, {"name": "B", "upper": 0.9}]}, "must be open"),
        ({"bands": [{"name": "A", "upper": 0.5}, {"name": "B", "upper": 0.4}, {"name": "C"}]}, "must increase"),
        ({"label": {"name": "bad", "horizon": 0, "definition": "x"}}, "label.horizon"),
        ({"label": {"name": "bad", "horizon": 2}}, "label.definition"),
        ({"label": {"name": "excluded", "horizon": 1, "definition": "x"}}, "used twice"),
        ({"stratify": {"seed": 1, "fractions": {"0": 1.5}}}, r"fractions\['0'\]"),
        ({"frequency": "hourly"}, "frequency must be one"),
        ({"entity_keys": []}, "entity_keys"),
        ({"model": "two words"}, "model must be an identifier"),
        ({"segments": []}, "unknown keys: segments"),
    ],
)
def test_malformed_declarations_are_refused(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(DatasetError, match=message):
        parse_development(declaration(**overrides))


def test_stratify_needs_a_label_and_normalizes_label_values(tmp_path: Path) -> None:
    unlabelled = declaration(samples={"live": {"role": "prediction", "start": "2024-10-31", "end": "2024-10-31"}})
    unlabelled.pop("label")
    unlabelled["stratify"] = {"seed": 3, "fractions": {"0": 0.5}}
    with pytest.raises(DatasetError, match="stratify needs a label"):
        parse_development(unlabelled)
    dev = parse_development(declaration(stratify={"seed": 3, "fractions": {0: 0.25, "1": 1}}))
    assert dev.stratify is not None
    assert dev.stratify.fraction(0) == 0.25 and dev.stratify.fraction(0.0) == 0.25
    assert dev.stratify.fraction(True)  # noqa: FBT003 - a label value == 1.0 and dev.stratify.fraction("other") == 1.0
    file = tmp_path / "dev.json"
    file.write_text(json.dumps(declaration()), encoding="utf-8")
    assert load_development_file(file) == parse_development(declaration())
    with pytest.raises(DatasetError, match="cannot read"):
        load_development_file(tmp_path / "missing.toml")


# --- the row engine ---------------------------------------------------------------------------------------

MONTHS = [date(2024, m, d) for m, d in ((1, 31), (2, 29), (3, 31), (4, 30), (5, 31), (6, 30), (7, 31))]
FEATURES = {
    "entity_keys": ["account_id"],
    "features": {"balance": {"description": "Closing balance", "type": "double", "owner": "team-a", "default": 0.0}},
}


def population() -> list[dict[str, Any]]:
    rows = []
    for account in range(1, 41):
        for m, closing in enumerate(MONTHS):
            status = "closed" if account % 10 == 0 else "open"
            rows.append({"account_id": account, "closing_date": closing.isoformat(), "status": status,
                         "tenure": (account % 7) + m})  # fmt: skip
    return rows


def labels() -> list[dict[str, Any]]:
    return [{"account_id": r["account_id"], "closing_date": r["closing_date"], "bad": r["account_id"] % 3 == 0}
            for r in population()]  # fmt: skip


def features() -> list[dict[str, Any]]:
    return [{"account_id": a, "closing_date": c, "balance": float(a * 100 + i)}
            for a in range(1, 40) for i, c in enumerate(MONTHS)]  # fmt: skip


def development(**overrides: Any) -> Development:  # noqa: ANN401 - plain metadata values
    return parse_development(declaration(lookups=[{"table": "fs.balances"}], **overrides))


def test_the_cascade_keeps_every_rule_met_the_first_and_the_flag() -> None:
    dev = development()
    rows = apply_cascade([{"status": "closed", "tenure": 1}, {"status": "closed", "tenure": 5},
                          {"status": "open", "tenure": 2}, {"status": "open", "tenure": None}], dev)  # fmt: skip
    assert [(r["exclusion_rules"], r["exclusion_first"], r["excluded"]) for r in rows] == [
        (["closed", "young"], "closed", True),
        (["closed"], "closed", True),
        (["young"], "young", True),
        ([], None, False),
    ]
    assert apply_cascade([{"x": 1}], development(exclusions=None)) == [
        {"x": 1, "exclusion_rules": [], "exclusion_first": None, "excluded": False}
    ]


def test_composition_counts_per_closing_and_rule_in_cascade_order() -> None:
    dev = development()
    rows = apply_cascade(
        [{"closing_date": "2024-01-31", "status": "closed", "tenure": 1, "s": "a"},
         {"closing_date": "2024-01-31", "status": "open", "tenure": 1, "s": "a"},
         {"closing_date": "2024-01-31", "status": "open", "tenure": 9, "s": "b"},
         {"closing_date": "2024-02-29", "status": "closed", "tenure": 9, "s": "a"}], dev)  # fmt: skip
    found = composition(rows, dev)
    assert [(r["closing_date"], r["rule"], r["total"], r["excluded"], r["met"], r["first"]) for r in found] == [
        (date(2024, 1, 31), "closed", 3, 2, 1, 1),
        (date(2024, 1, 31), "young", 3, 2, 2, 1),
        (date(2024, 2, 29), "closed", 1, 1, 1, 1),
        (date(2024, 2, 29), "young", 1, 1, 0, 0),
    ]
    assert found[0]["first_share"] == pytest.approx(1 / 3)
    by_sample = composition(rows, dev, by=["s"])
    assert [(r["s"], r["rule"], r["total"]) for r in by_sample][:2] == [("a", "closed", 2), ("a", "young", 2)]
    assert composition([{k: v for k, v in r.items() if k not in CASCADE_COLUMNS} for r in rows], dev) == found


def test_samples_select_closings_and_split_shared_closings_without_overlap() -> None:
    dev = development()
    rows = apply_cascade(population(), dev)
    train = select_sample(rows, dev, "dev")
    test = select_sample(rows, dev, "holdout")
    oot = select_sample(rows, dev, "oot")
    assert {r["closing_date"] for r in train} == set(MONTHS[:4])
    assert {(r["sample"], r["sample_role"]) for r in test} == {("holdout", "test")}
    assert {r["closing_date"] for r in oot} == set(MONTHS[4:6])
    keys = lambda rs: {(r["account_id"], r["closing_date"]) for r in rs}  # noqa: E731
    assert not keys(train) & keys(test)
    shared = {(r["account_id"], date.fromisoformat(r["closing_date"])) for r in rows_in(rows, MONTHS[2:4])}
    assert keys(train) | keys(test) >= shared
    assert len(keys(test)) == len(test)
    assert 0.15 < len(test) / len(shared) < 0.45  # test_fraction 0.3 of the shared closings
    assert len(train) == 80 + len(shared) - len(test)
    again = select_sample(list(reversed(rows)), dev, "holdout")
    assert keys(again) == keys(test)  # deterministic, independent of row order
    with pytest.raises(DatasetError, match="not a monthly closing"):
        select_sample([{"account_id": 1, "closing_date": "2024-01-15"}], dev, "dev")
    with pytest.raises(DatasetError, match="'nope' is not declared"):
        select_sample(rows, dev, "nope")


def rows_in(rows: list[dict[str, Any]], closings: list[date]) -> list[dict[str, Any]]:
    return [r for r in rows if date.fromisoformat(str(r["closing_date"])) in closings]


def test_labels_attach_by_exact_closing_and_refuse_immature_missing_or_repeated() -> None:
    dev = development()
    rows = select_sample(population(), dev, "oot")
    found = attach_labels(rows, dev, labels())
    assert all(r["bad"] == (r["account_id"] % 3 == 0) for r in found)
    assert attach_labels([{**r, "bad": False} for r in rows], dev)[0]["bad"] is False
    early = dataclasses.replace(dev, as_of=date(2024, 8, 31))
    with pytest.raises(DatasetValidationError, match=r"40 row\(s\) have a label that matures after as_of 2024-08-31"):
        attach_labels(rows, early, labels())
    partial = [r for r in labels() if (r["account_id"], r["closing_date"]) != (5, "2024-06-30")]
    with pytest.raises(DatasetValidationError, match=r"1 row\(s\) have no 'bad' label, e.g. 5\|2024-06-30"):
        attach_labels(rows, dev, partial)
    with pytest.raises(DatasetValidationError, match="repeat the key"):
        attach_labels(rows, dev, labels() + labels()[:1])
    unlabelled = declaration(samples={"live": {"role": "prediction", "start": "2024-10-31", "end": "2024-10-31"}})
    unlabelled.pop("label")
    with pytest.raises(DatasetError, match="needs a declared label"):
        attach_labels(rows, parse_development(unlabelled))


def test_stratification_is_deterministic_by_label_value() -> None:
    dev = development(stratify={"seed": 11, "fractions": {False: 0.5}})
    rows = attach_labels(select_sample(population(), dev, "dev"), dev, labels())
    kept = stratify(rows, dev)
    assert all(r in kept for r in rows if r["bad"])  # bad rows: no fraction declared, all kept
    goods = [r for r in rows if not r["bad"]]
    kept_goods = [r for r in kept if not r["bad"]]
    assert 0.3 < len(kept_goods) / len(goods) < 0.7
    assert stratify(list(reversed(rows)), dev) == list(reversed(kept))
    assert stratify(rows, development()) == rows


def test_one_abt_code_path_for_training_and_scoring() -> None:
    dev = development()
    table = parse_feature_table(FEATURES)
    inputs = {"definitions": {"fs.balances": table}, "data": {"fs.balances": features()}}
    training = build_abt(population(), dev, "oot", labels=labels(), **inputs)
    scoring = build_abt(population(), dev, "oot", purpose="scoring", **inputs)
    assert scoring.reads == training.reads and set(training.reads) == {"fs.balances"}
    assert len(scoring.data) == 80 and len(training.data) == 72  # accounts 10, 20, 30, 40 excluded (closed)
    assert all(not r["excluded"] for r in training.data) and any(r["excluded"] for r in scoring.data)
    assert "bad" in training.data[0] and "bad" not in scoring.data[0]
    by_key = {(r["account_id"], r["closing_date"]): r for r in scoring.data}
    for row in training.data:
        twin = by_key[row["account_id"], row["closing_date"]]
        assert {k: v for k, v in row.items() if k != "bad"} == twin  # same spine, same lookups
    assert by_key[40, date(2024, 5, 31)]["balance"] == 0.0  # missing entity: declared default
    assert by_key[3, date(2024, 6, 30)]["balance"] == 305.0
    assert {r["sample_role"] for r in scoring.data} == {"out_of_time"}
    live = build_abt(population(), dev, "ttd", purpose="scoring", **inputs)
    assert {r["closing_date"] for r in live.data} == {MONTHS[6]}
    with pytest.raises(DatasetError, match="carries no label; build it for scoring"):
        build_abt(population(), dev, "ttd", **inputs)
    with pytest.raises(DatasetError, match="purpose must be one of"):
        build_abt(population(), dev, "oot", purpose="serving", **inputs)


def test_prediction_rows_hold_the_contract_monitoring_reads() -> None:
    dev = development()
    table = parse_feature_table(FEATURES)
    abt = build_abt(population(), dev, "oot", purpose="scoring", definitions={"fs.balances": table},
                    data={"fs.balances": features()})  # fmt: skip
    scored = [{**r, "score": (r["account_id"] % 10) / 10} for r in abt.data]
    rows = prediction_rows(scored, dev, model_version=3, model_alias="champion")
    first = rows[0]
    assert list(first) == ["account_id", "closing_date", *PREDICTION_COLUMNS]
    assert (first["model_name"], first["model_version"], first["model_alias"]) == ("churn", "3", "champion")
    assert {r["grade"] for r in rows} == {"A", "B", "C"}
    assert {r["sample"] for r in rows} == {"oot"}
    assert sum(r["excluded"] for r in rows) == 8
    with pytest.raises(DatasetError, match="misses score"):
        prediction_rows(abt.data, dev, model_version=3)
    with pytest.raises(DatasetValidationError, match="null score"):
        prediction_rows([{**scored[0], "score": None}], dev, model_version=3)
    with pytest.raises(DatasetValidationError, match="repeat entity, closing and model version"):
        prediction_rows(scored[:1] * 2, dev, model_version=3)
    with pytest.raises(DatasetValidationError, match="miss contract columns: grade"):
        check_prediction([{k: v for k, v in first.items() if k != "grade"}], dev)
    plain = dataclasses.replace(dev, bands=())
    assert prediction_rows([{**scored[0], "grade": "x"}], plain, model_version=1)[0]["grade"] == "x"
