from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

from bricksmith.cli.main import main
from bricksmith.clients import Clients
from bricksmith.context import resolve_context
from bricksmith.errors import MissingExtraError
from bricksmith.preflight import State, remove_owned_schema, run_preflight

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

HOST = "https://free.example.test"
ENVIRON = {
    "BRICKSMITH_FREE_PROFILE": "p",
    "BRICKSMITH_FREE_WORKSPACE_HOST": HOST,
    "BRICKSMITH_FREE_CATALOG": "cat",
    "BRICKSMITH_FREE_SCHEMA_PREFIX": "pre",
}


class FakeSchemas:
    def __init__(self, *, stuck: bool = False) -> None:
        self.names: list[str] = []
        self.stuck = stuck

    def create(self, name: str, catalog_name: str, comment: str) -> None:
        assert comment
        assert catalog_name == "cat"
        self.names.append(name)

    def delete(self, full_name: str, *, force: bool) -> None:
        assert force
        if not self.stuck:
            self.names.remove(full_name.split(".")[1])

    def list(self, catalog_name: str) -> list[SimpleNamespace]:
        assert catalog_name == "cat"
        return [SimpleNamespace(name=n) for n in self.names]


class FakeWorkspace:
    def __init__(self, host: str = HOST, *, stuck: bool = False, volume_error: bool = False) -> None:
        self.config = SimpleNamespace(host=host)
        self.current_user = SimpleNamespace(me=object)
        self.schemas = FakeSchemas(stuck=stuck)
        self.volume_error = volume_error
        self.volumes = SimpleNamespace(create=self._volume)

    def _volume(self, **_: str) -> None:
        if self.volume_error:
            msg = "boom"
            raise RuntimeError(msg)


class FakeSpark:
    def sql(self, _: str) -> FakeSpark:
        return self

    def collect(self) -> list[list[int]]:
        return [[1]]


def _profile_file(tmp_path: Path) -> dict[str, str]:
    cfg = tmp_path / "cfg"
    cfg.write_text("[p]\nhost = x\n")
    return {"DATABRICKS_CONFIG_FILE": str(cfg)}


def _states(workspace: FakeWorkspace, tmp_path: Path) -> dict[str, State]:
    environ = {**ENVIRON, **_profile_file(tmp_path)}
    context = resolve_context(environ=ENVIRON)
    clients = Clients(context, {"workspace": lambda _: workspace, "spark": lambda _: FakeSpark()})
    report = run_preflight(context, environ=environ, clients=clients, run_id="r1")
    return {s.stage: s.state for s in report.stages}


def test_all_stages_pass_and_cleanup_is_verified(tmp_path: Path) -> None:
    workspace = FakeWorkspace()
    states = _states(workspace, tmp_path)
    assert states["resources-tested"] is State.PASSED
    assert states["deployment-tested"] is State.NOT_RUN
    assert workspace.schemas.names == []


def test_injected_failure_still_cleans_up(tmp_path: Path) -> None:
    workspace = FakeWorkspace(volume_error=True)
    assert _states(workspace, tmp_path)["resources-tested"] is State.FAILED
    assert workspace.schemas.names == []


def test_leftovers_are_reported_not_hidden(tmp_path: Path) -> None:
    workspace = FakeWorkspace(stuck=True)
    assert _states(workspace, tmp_path)["resources-tested"] is State.FAILED
    assert remove_owned_schema(workspace, "cat", "pre_r1")


def test_host_mismatch_fails_authentication_and_stops(tmp_path: Path) -> None:
    states = _states(FakeWorkspace(host="https://other.example.test"), tmp_path)
    assert states["authenticated"] is State.FAILED
    assert states["compute-tested"] is State.NOT_RUN


def test_missing_extra_is_unsupported_not_failed(tmp_path: Path) -> None:
    def missing(_: object) -> object:
        msg = "install bricksmith[sdk]"
        raise MissingExtraError(msg)

    context = resolve_context(environ=ENVIRON)
    environ = {**ENVIRON, **_profile_file(tmp_path)}
    report = run_preflight(context, environ=environ, clients=Clients(context, {"workspace": missing}))
    assert report.stages[1].state is State.UNSUPPORTED


def test_missing_selection_fails_offline_without_default_fallback(tmp_path: Path) -> None:
    context = resolve_context(environ={})
    report = run_preflight(context, environ=_profile_file(tmp_path), offline_only=True)
    assert report.stages[0].state is State.FAILED
    assert {s.state for s in report.stages[1:]} == {State.NOT_RUN}


def test_cli_offline_reports_configuration_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BRICKSMITH_TEST_ENV=free\n")
    assert main(["env", "check", "--offline", "--json", "--env-file", str(env_file)]) == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["stages"][0]["state"] == "failed"
    assert [s["stage"] for s in payload["stages"]][-1] == "deployment-tested"
