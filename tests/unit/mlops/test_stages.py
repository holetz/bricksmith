"""Offline tests for model development stages: the declaration, the row engine and the import surface."""
# ruff: noqa: PT018 - dense assertions on hand-derived values

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.mlops.errors import DatasetError
from bricksmith.mlops.stages import load_development_file, parse_development

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
    code = (
        f"import sys, bricksmith.mlops.stages\nbad = [m for m in {HEAVY!r} if m in sys.modules]\nassert not bad, bad\n"
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
