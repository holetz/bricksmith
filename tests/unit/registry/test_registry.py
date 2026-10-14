"""Synthetic acceptance tests for the registry capability."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from bricksmith.cli.main import main
from bricksmith.registry import (
    RegistryFormatError,
    default_preset,
    load_baseline,
    merge_comments,
    merge_properties,
    parse_preset,
    parse_registry,
    run_check,
    sensitivity_coverage,
    table_metadata,
    write_baseline,
)

if TYPE_CHECKING:
    from pathlib import Path

REGISTRY = """
version = 1
[[columns]]
name = "OrderId"
type = "bigint"
description = "Key."
[[columns]]
name = "amount"
type = "money"
description = "Amount."
sensitivity = "internal"
[[columns]]
name = "note"
type = "string"
[tables."main.sales.orders"]
grain = ["OrderId"]
[tables."main.sales.orders".columns.OrderId]
[tables."main.sales.orders".columns.amount]
[tables."main.sales.orders".columns.ghost]
"""
TODAY = dt.date(2026, 1, 1)


def _registry() -> object:
    import tomllib  # noqa: PLC0415

    return parse_registry(tomllib.loads(REGISTRY))


def test_findings_are_stable_and_sorted() -> None:
    first = run_check(_registry(), default_preset(), today=TODAY)  # type: ignore[arg-type]
    second = run_check(_registry(), default_preset(), today=TODAY)  # type: ignore[arg-type]
    ids = [f.id for f in first.findings]
    assert ids == sorted(ids)
    assert first.to_json() == second.to_json()
    assert "naming.case:column:OrderId" in ids
    assert "registry.type_unknown:amount" in ids
    assert "registry.column_unknown:main.sales.orders.ghost" in ids
    assert "registry.description_missing:note" in ids
    assert not first.ok


def test_ratchet_tolerates_existing_and_fails_new(tmp_path: Path) -> None:
    registry = _registry()
    report = run_check(registry, default_preset(), today=TODAY)  # type: ignore[arg-type]
    path = tmp_path / "baseline.json"
    write_baseline(path, report)
    again = run_check(registry, default_preset(), load_baseline(path), TODAY)  # type: ignore[arg-type]
    assert again.ok
    assert {f.status for f in again.findings if f.severity == "error"} == {"baselined"}
    worse = parse_registry({**json.loads("{}"), "version": 1, "columns": [{"name": "Bad", "type": "string"}]})
    new = run_check(worse, default_preset(), load_baseline(path), TODAY)
    assert not new.ok
    assert [f.id for f in new.failing] == ["naming.case:column:Bad"]
    assert "naming.case:column:OrderId" in new.stale_baseline


def test_expired_exception_fails_and_active_suppresses() -> None:
    def preset(expires: str) -> object:
        return parse_preset(
            {"version": 1, "exceptions": [{"finding": "naming.case:column:*", "reason": "legacy", "expires": expires}]}
        )

    registry = parse_registry({"version": 1, "columns": [{"name": "Bad", "type": "string", "description": "d"}]})
    ok = run_check(registry, preset("2026-06-01"), today=TODAY)  # type: ignore[arg-type]
    assert ok.ok
    assert ok.findings[0].status == "excepted"
    late = run_check(registry, preset("2025-12-31"), today=TODAY)  # type: ignore[arg-type]
    assert not late.ok
    assert {f.rule for f in late.failing} == {"exception.expired", "naming.case"}


def test_exception_requires_reason() -> None:
    with pytest.raises(RegistryFormatError):
        parse_preset({"version": 1, "exceptions": [{"finding": "x", "reason": " ", "expires": "2030-01-01"}]})


def test_preset_change_alters_results_without_code_change() -> None:
    registry = parse_registry({"version": 1, "columns": [{"name": "total_amt", "type": "string", "description": "d"}]})
    assert run_check(registry, default_preset(), today=TODAY).ok
    strict = parse_preset({"version": 1, "scopes": {"column": {"suffixes": ["_id"], "reserved": ["total_amt"]}}})
    rules = {f.rule for f in run_check(registry, strict, today=TODAY).findings}
    assert rules == {"naming.suffix", "naming.reserved"}
    vocab = parse_preset({"version": 1, "vocabulary": ["total"], "scopes": {"column": {"vocabulary": True}}})
    assert [f.rule for f in run_check(registry, vocab, today=TODAY).findings] == ["naming.token"]
    quiet = parse_preset({"version": 1, "severity": {"naming.*": "off"}, "scopes": {"column": {"case": "kebab"}}})
    assert run_check(registry, quiet, today=TODAY).findings == ()


def test_severity_warning_does_not_fail() -> None:
    registry = parse_registry({"version": 1, "columns": [{"name": "Bad", "type": "string", "description": "d"}]})
    preset = parse_preset({"version": 1, "severity": {"naming.case": "warning"}})
    report = run_check(registry, preset, today=TODAY)
    assert report.ok
    assert report.findings[0].severity == "warning"


def test_bad_files_are_rejected() -> None:
    with pytest.raises(RegistryFormatError):
        parse_registry({"version": 2})
    with pytest.raises(RegistryFormatError):
        parse_registry({"version": 1, "columns": [{"name": "a", "typo": 1}]})
    with pytest.raises(RegistryFormatError):
        parse_preset({"version": 1, "scopes": {"nope": {}}})


def test_metadata_merge_preserves_unrelated_keys() -> None:
    registry = parse_registry(
        {
            "version": 1,
            "columns": [{"name": "a", "type": "int", "description": "Col A.", "sensitivity": "public"}],
            "tables": {"c.s.t": {"description": "Tbl.", "grain": ["a"], "columns": {"a": {}, "b": {"type": "int"}}}},
        }
    )
    meta = table_metadata(
        registry,
        "c.s.t",
        existing_comments={"a": "old", "z": "keep"},
        existing_properties={"delta.appendOnly": "true", "bricksmith.sensitivity.gone": "x"},
    )
    assert meta.table_comment == "Tbl."
    assert meta.column_comments == {"a": "Col A.", "z": "keep"}
    assert meta.properties == {
        "delta.appendOnly": "true",
        "bricksmith.grain": "a",
        "bricksmith.sensitivity.a": "public",
        "bricksmith.sensitivity.b": "unclassified",
    }
    assert merge_comments({"x": "1"}, {"x": ""}) == {"x": "1"}
    assert merge_properties({"k": "v"}, {}) == {"k": "v"}


def test_sensitivity_coverage_states() -> None:
    registry = parse_registry(
        {
            "version": 1,
            "columns": [{"name": "a", "sensitivity": "public"}, {"name": "b"}],
            "tables": {"c.s.t": {"columns": {"a": {}, "b": {}, "c": {}}}},
        }
    )
    cov = sensitivity_coverage(registry)
    assert (cov.classified, cov.unclassified, cov.unknown) == (("c.s.t.a",), ("c.s.t.b",), ("c.s.t.c",))


def test_packaged_defaults_have_no_project_vocabulary() -> None:
    preset = default_preset()
    assert preset.vocabulary == ()
    assert preset.exemptions == ()
    for rule in preset.scopes.values():
        assert (rule.allowed_tokens, rule.prefixes, rule.suffixes, rule.vocabulary) == ((), (), (), False)


def test_check_runs_without_spark_importable() -> None:
    code = (
        "import sys; sys.modules['pyspark']=None; sys.modules['databricks']=None\n"
        "from bricksmith.registry import default_preset, parse_registry, run_check\n"
        "cols = [{'name': 'a', 'type': 'int', 'description': 'd'}]\n"
        "assert run_check(parse_registry({'version': 1, 'columns': cols}), default_preset()).ok\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_cli_check_json_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "columns.toml").write_text(REGISTRY, encoding="utf-8")
    base = [
        "registry", "check", "--json",
        "--registry", str(tmp_path / "columns.toml"),
        "--preset", str(tmp_path / "none.toml"),
        "--baseline", str(tmp_path / "baseline.json"),
        "--today", "2026-01-01",
    ]  # fmt: skip
    assert main(base) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert main([*base, "--write-baseline"]) == 0
    assert main(base) == 0
