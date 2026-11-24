"""The engine choice record: always logged, collected inside ``collecting`` blocks."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from bricksmith.features.record import EngineChoice, collecting, record

if TYPE_CHECKING:
    import pytest


def test_a_fallback_is_a_warning_and_a_plain_choice_is_info(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="bricksmith.features")
    record(EngineChoice("auto", "spark", reason="session: refused"))
    record(EngineChoice("auto", "jvm", path="session"))
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.WARNING, "feature engine spark ran instead of the requested auto: session: refused"),
        (logging.INFO, "feature engine jvm ran (requested auto, session)"),
    ]


def test_collecting_keeps_choices_only_inside_its_block() -> None:
    record(EngineChoice("spark", "spark"))
    with collecting() as outer:
        record(EngineChoice("auto", "jvm", path="session", outputs=2))
        with collecting() as inner:
            record(EngineChoice("arrow", "arrow"))
        record(EngineChoice("spark", "spark"))
    assert [c.engine for c in outer] == ["jvm", "spark"]
    assert [c.engine for c in inner] == ["arrow"]
    assert outer[0].to_dict() == {"requested": "auto", "engine": "jvm", "path": "session", "reason": "", "outputs": 2}
    assert not outer[0].fallback
    assert EngineChoice("auto", "spark", reason="x").fallback
