"""The runner option ``--reprocess``: a job run carries the periods a backfill rewrote into the stability check."""
# ruff: noqa: PT018 - dense assertions

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from bricksmith.runner import service
from bricksmith.runner.service import add_run_options, reprocess_periods

if TYPE_CHECKING:
    import pytest


def test_reprocess_periods_reads_lists_and_ranges() -> None:
    assert reprocess_periods(None) == () and reprocess_periods("") == ()
    assert reprocess_periods("2026-01..2026-03, 2026-05,") == ("2026-01..2026-03", "2026-05")


def test_the_option_reaches_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class _Report:
        ok = True
        results = ()

        def to_dict(self) -> dict[str, object]:
            return {"counts": {}}

    def fake_apply(*_: object, **options: Any) -> _Report:  # noqa: ANN401
        seen.update(options)
        return _Report()

    monkeypatch.setattr(service, "apply_plan", fake_apply)
    monkeypatch.setattr(service, "context_from_args", lambda _: object())
    monkeypatch.setattr(service, "default_table_io", lambda _: object())
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--project", default=".")
    add_run_options(parser)
    args = parser.parse_args(["--reprocess", "2026-01,2026-02..2026-03"])
    assert service.apply_from_args(args, plan=_Plan(), graph=object()) == 0  # type: ignore[arg-type]
    assert seen["reprocess"] == ("2026-01", "2026-02..2026-03")


class _Plan:
    def authorize(self, *_: object) -> _Plan:
        return self
