"""Regression reports, the gate and the CLI group."""

# ruff: noqa: PT018 - dense assertions in synthetic tests

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from bricksmith.cli.main import main
from bricksmith.quality import (
    Check,
    GatePolicy,
    QualityError,
    RegressionReport,
    build_regression_report,
    compare_rows,
    plan_comparison,
    run_check,
)
from bricksmith.quality import cli as quality_cli

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

SCHEMA = {"id": "int", "v": "int"}
PLAN = plan_comparison(SCHEMA, SCHEMA, ["id"])
SAME = [{"id": 1, "v": 1}, {"id": 2, "v": 2}]
CHANGED = [{"id": 1, "v": 1}, {"id": 2, "v": 3}, {"id": 3, "v": 9}]


def test_gate_passes_identical_and_fails_each_kind_of_difference() -> None:
    ok = build_regression_report("same", comparisons={"t": compare_rows(SAME, SAME, PLAN)})
    assert ok.verdict == "pass" and ok.exit_code == 0 and ok.failures == ()
    bad = build_regression_report("changed", comparisons={"t": compare_rows(SAME, CHANGED, PLAN)})
    assert bad.verdict == "fail" and bad.exit_code == 4
    assert len(bad.failures) == 2  # one extra key and one differing row
    lenient = GatePolicy(max_extra_keys=1, max_differing_rows=1)
    assert (
        build_regression_report("changed", comparisons={"t": compare_rows(SAME, CHANGED, PLAN)}, policy=lenient).verdict
        == "pass"
    )
    ratio = GatePolicy(max_extra_keys=1, max_differing_ratio=0.25)  # 1 of 2 matched rows differs: 0.5
    assert (
        build_regression_report("r", comparisons={"t": compare_rows(SAME, CHANGED, PLAN)}, policy=ratio).verdict
        == "fail"
    )
    missing = build_regression_report("m", comparisons={"t": compare_rows(CHANGED, SAME, PLAN)})
    assert any("missing in right" in f for f in missing.failures)


def test_an_invalid_comparison_always_fails_the_gate() -> None:
    duplicate = compare_rows([*SAME, SAME[0]], SAME, PLAN)
    lenient = GatePolicy(max_missing_keys=99, max_extra_keys=99, max_differing_rows=99, allow_unavailable=True)
    report = build_regression_report("dup", comparisons={"t": duplicate}, policy=lenient)
    assert report.verdict == "fail" and "invalid" in report.failures[0]


def test_checks_in_the_gate_and_empty_reports() -> None:
    passed = run_check(Check.grain("id"), SAME)
    unavailable = run_check(Check.grain("nope"), SAME)
    failed = run_check(Check.not_null("v"), [{"v": None}])
    assert build_regression_report("c", checks=[passed]).verdict == "pass"
    assert build_regression_report("c", checks=[failed]).verdict == "fail"
    assert build_regression_report("c", checks=[unavailable]).verdict == "fail"
    assert (
        build_regression_report("c", checks=[unavailable], policy=GatePolicy(allow_unavailable=True)).verdict == "pass"
    )
    assert build_regression_report("empty").verdict == "fail"  # nothing proven is not a pass


def test_reports_are_reproducible_and_tamper_evident() -> None:
    def build() -> RegressionReport:
        return build_regression_report(
            "r",
            comparisons={"t": compare_rows(SAME, CHANGED, PLAN, sample_limit=2)},
            checks=[run_check(Check.grain("id"), SAME)],
        )

    first, second = build(), build()
    assert first.to_json() == second.to_json() and first.fingerprint == second.fingerprint
    assert list(json.loads(first.to_json())) == sorted(json.loads(first.to_json()))
    assert RegressionReport.from_dict(first.to_dict()).to_json() == first.to_json()
    other = build_regression_report("r", comparisons={"t": compare_rows(SAME, SAME, PLAN)})
    assert other.fingerprint != first.fingerprint
    tampered = first.to_dict()
    tampered["name"] = "other"
    with pytest.raises(QualityError, match="fingerprint"):
        RegressionReport.from_dict(tampered)
    assert "FAIL" in first.to_text()
    with pytest.raises(QualityError):
        GatePolicy.from_dict({"nope": 1})


def _fake_tables(monkeypatch: pytest.MonkeyPatch, tables: Mapping[str, Sequence[Mapping[str, object]]]) -> None:
    monkeypatch.setattr(quality_cli, "_table", lambda _args, name: tables[name])
    monkeypatch.setattr(
        quality_cli,
        "plan_for_frames",
        lambda _l, _r, keys, **o: plan_comparison(
            SCHEMA,
            SCHEMA,
            keys,
            columns=o["columns"],
            exclude=o["exclude"],
            tolerances=o["tolerances"],
            default_tolerance=o["default_tolerance"],
        ),
    )


def test_cli_compare_gate_exit_codes_and_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _fake_tables(monkeypatch, {"a": SAME, "b": CHANGED, "c": SAME})
    out = tmp_path / "report.json"
    base = ["quality", "compare", "--key", "id", "--json"]
    assert main([*base, "--left", "a", "--right", "c"]) == 0
    assert json.loads(capsys.readouterr().out)["verdict"] == "pass"
    assert main([*base, "--left", "a", "--right", "b", "--sample-limit", "5", "--report-out", str(out)]) == 4
    printed = json.loads(capsys.readouterr().out)
    assert printed["verdict"] == "fail" and json.loads(out.read_text()) == printed
    assert printed["comparisons"]["comparison"]["samples"]["extra_in_right"] == [{"id": 3}]
    policy = tmp_path / "policy.json"
    policy.write_text('{"max_extra_keys": 1, "max_differing_rows": 1}')
    assert main([*base, "--left", "a", "--right", "b", "--policy", str(policy), "--tolerance", "v=abs:1"]) == 0
    capsys.readouterr()
    assert main(["quality", "compare", "--key", "id", "--left", "a", "--right", "b"]) == 4
    assert "regression" in capsys.readouterr().out
    assert main([*base, "--left", "a", "--right", "b", "--tolerance", "v=oops:1"]) == 4


def test_cli_check_reads_a_declaration_file(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _fake_tables(monkeypatch, {"a": [*SAME, {"id": 2, "v": 2}]})
    declared = tmp_path / "checks.toml"
    declared.write_text(
        '[[check]]\nkind = "grain"\ncolumns = ["id"]\n[[check]]\nkind = "nullability"\ncolumns = ["v"]\n'
    )
    assert main(["quality", "check", "--table", "a", "--checks", str(declared), "--json"]) == 4
    report = json.loads(capsys.readouterr().out)
    assert [c["status"] for c in report["checks"]] == ["failed", "passed"]
