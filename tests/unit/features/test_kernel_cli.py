"""``bricksmith features install-kernel`` and its library twin, over fake clients."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from bricksmith import clients as clients_module
from bricksmith.cli.main import main
from bricksmith.features import kernel
from bricksmith.features.cli import install_kernel
from bricksmith.io.volumes import FilesApiVolumeClient

if TYPE_CHECKING:
    import pytest


class FakeClients:
    def __init__(self, *_: object) -> None:
        self.session = object()
        self.files = object()

    def spark(self) -> object:
        return self.session

    def io(self) -> object:
        return self.files


def fake_install(seen: list[tuple[Any, ...]]) -> Any:  # noqa: ANN401 - a stand-in for install_uc_function
    def install(spark: object, volumes: object, volume: str, function: str) -> str:
        seen.append((spark, volumes, volume, function))
        return f"/Volumes/{volume.replace('.', '/')}/bricksmith-kernel-0.0.0-abc.jar"

    return install


def test_install_kernel_uploads_through_the_files_api(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(kernel, "install_uc_function", fake_install(seen))
    fake = FakeClients()
    result = install_kernel(fake, "main.feat.jars", "main.feat.kernel")  # pyright: ignore[reportArgumentType]
    assert result == {"function": "main.feat.kernel", "jar": "/Volumes/main/feat/jars/bricksmith-kernel-0.0.0-abc.jar"}
    spark, volumes, volume, function = seen[0]
    assert spark is fake.session
    assert isinstance(volumes, FilesApiVolumeClient)
    assert (volume, function) == ("main.feat.jars", "main.feat.kernel")


def test_install_kernel_command(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    seen: list[tuple[Any, ...]] = []
    monkeypatch.setattr(kernel, "install_uc_function", fake_install(seen))
    monkeypatch.setattr(clients_module, "Clients", FakeClients)
    code = main(
        ["features", "install-kernel", "--volume", "main.feat.jars", "--function", "main.feat.kernel", "--json"]
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["function"] == "main.feat.kernel"
    assert payload["jar"].startswith("/Volumes/main/feat/jars/")
    assert main(["features", "install-kernel", "--volume", "main.feat.jars"]) == 2
