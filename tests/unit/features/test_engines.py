"""Engine dispatch: ``auto`` runs the JVM kernel, falls back to native with the reason recorded, never silently."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from bricksmith.features import CategoricalCount, FeatureSpec, Ratio, WindowFeature, engines, kernel, spark
from bricksmith.features.engines import ARROW, AUTO, JVM, MIXED, NATIVE, compute_features
from bricksmith.features.kernel import SESSION, KernelStatus, KernelUnavailableError
from bricksmith.features.record import EngineChoice, collecting

SPEC = FeatureSpec(
    keys=("entity",),
    closing="closing",
    features=(WindowFeature(("x",), (3,), ("mean", "slope")),),
    ratios=(Ratio("ratio", "x_mean_3p", "x"),),
    categorical=(CategoricalCount("status", ("a",), (3,)),),
)


@dataclass
class Frame:
    label: str
    joined: list[str] = field(default_factory=list)
    sparkSession: object = "session"  # noqa: N815 - the pyspark attribute name

    def join(self, other: Frame, on: list[str], how: str) -> Frame:
        assert (on, how) == (["entity", "closing"], "inner")
        return Frame(self.label, [*self.joined, other.label])


@dataclass
class Calls:
    probes: list[tuple[object, str | None]] = field(default_factory=list)
    jvm: list[tuple[list[str], str]] = field(default_factory=list)
    native: list[list[str] | None] = field(default_factory=list)
    arrow: list[list[str]] = field(default_factory=list)
    status: KernelStatus = field(default_factory=lambda: KernelStatus(available=True, path=SESSION, function="k"))


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    from bricksmith.features import arrow  # noqa: PLC0415 - needs numpy, present in the dev group

    found = Calls()

    def probe(spark_session: object, function: str | None = None) -> KernelStatus:
        found.probes.append((spark_session, function))
        return found.status

    def compute_jvm(_df: Frame, spec: FeatureSpec, _as_of: object, function: str) -> Frame:
        found.jvm.append(([o.name for o in spec.outputs], function))
        return Frame("jvm")

    def compute_spark(_df: Frame, _spec: FeatureSpec, _as_of: object = None, outputs: list[str] | None = None) -> Frame:
        found.native.append(outputs)
        return Frame("native")

    def compute_arrow(_df: Frame, spec: FeatureSpec, _as_of: object = None) -> Frame:
        found.arrow.append([o.name for o in spec.outputs])
        return Frame("arrow")

    monkeypatch.setattr(kernel, "probe", probe)
    monkeypatch.setattr(kernel, "compute_jvm", compute_jvm)
    monkeypatch.setattr(spark, "compute_spark", compute_spark)
    monkeypatch.setattr(engines, "_apply_combined", lambda result, *_: result)
    monkeypatch.setattr(arrow, "compute_arrow", compute_arrow)
    return found


def run(**options: Any) -> tuple[Frame, list[EngineChoice]]:  # noqa: ANN401 - compute_features options
    with collecting() as choices:
        result = compute_features(Frame("source"), SPEC, **options)  # pyright: ignore[reportArgumentType]
    return result, choices  # pyright: ignore[reportReturnType]


def test_auto_runs_every_window_output_on_the_kernel(calls: Calls) -> None:
    result, choices = run(kernel_function="main.feat.kernel")
    assert calls.probes == [("session", "main.feat.kernel")]
    assert calls.jvm == [(["x_mean_3p", "x_slope_3p"], "k")]
    assert calls.native == [["status_count_a_3p"]]
    assert (result.label, result.joined) == ("jvm", ["native"])
    assert choices == [EngineChoice(AUTO, JVM, SESSION, "", 2)]


def test_auto_falls_back_to_native_and_records_why(calls: Calls, caplog: pytest.LogCaptureFixture) -> None:
    calls.status = KernelStatus(available=False, reason="session: RuntimeError: refused")
    result, choices = run()
    assert calls.jvm == []
    assert calls.native == [None]
    assert result.label == "native"
    assert choices == [EngineChoice(AUTO, NATIVE, "", "session: RuntimeError: refused", 2)]
    assert "ran instead of the requested auto: session: RuntimeError: refused" in caplog.text


def test_an_explicit_jvm_request_never_falls_back(calls: Calls) -> None:
    calls.status = KernelStatus(available=False, reason="session: refused")
    with pytest.raises(KernelUnavailableError, match="unavailable on this compute: session: refused"):
        run(engine=JVM)
    assert calls.native == []


def test_native_and_arrow_requests_skip_the_probe(calls: Calls) -> None:
    _, native = run(engine=NATIVE)
    assert calls.probes == []
    assert native == [EngineChoice(NATIVE, NATIVE, "", "", 2)]
    result, arrow = run(engine=ARROW)
    assert calls.arrow == [["x_mean_3p", "x_slope_3p"]]
    assert (result.label, result.joined) == ("arrow", ["native"])
    assert arrow[0].engine == ARROW
    assert calls.probes == []


def test_arrow_is_reached_from_auto_only_through_engines(calls: Calls) -> None:
    result, choices = run(engines={"trend": ARROW})
    assert calls.jvm == [(["x_mean_3p"], "k")]
    assert calls.arrow == [["x_slope_3p"]]
    assert (result.label, result.joined) == ("jvm", ["arrow", "native"])
    assert choices[0].engine == MIXED
