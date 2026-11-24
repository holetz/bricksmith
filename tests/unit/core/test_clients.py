"""Clients are lazy, injectable and per instance; invalid input never reaches a provider."""

from __future__ import annotations

import pytest

from bricksmith import clients as clients_module
from bricksmith.clients import Clients, import_extra
from bricksmith.context import ExecutionContext, resolve_context
from bricksmith.errors import ConfigError, MissingExtraError, ValidationError
from bricksmith.params import parse_window


def test_fakes_are_created_lazily_once_per_instance() -> None:
    calls: list[str] = []
    ctx = resolve_context(environ={})

    def fake(context: ExecutionContext) -> object:
        calls.append(context.environment)
        return object()

    clients = Clients(ctx, {"spark": fake})
    assert calls == []
    assert clients.spark() is clients.spark()
    assert calls == ["free"]
    other = Clients(ctx, {"spark": fake})
    assert other.spark() is not clients.spark()


def test_secrets_and_io_derive_from_injected_workspace() -> None:
    class Workspace:
        secrets = "S"
        files = "F"

    clients = Clients(resolve_context(environ={}), {"workspace": lambda _: Workspace()})
    assert (clients.secrets(), clients.io()) == ("S", "F")


def test_unknown_kind_rejected() -> None:
    with pytest.raises(ConfigError):
        Clients(resolve_context(environ={}), {"bogus": lambda _: object()})


def test_missing_extra_names_the_extra() -> None:
    with pytest.raises(MissingExtraError, match=r"bricksmith\[sdk\]"):
        import_extra("bricksmith_not_installed_module", "sdk")


def test_invalid_input_fails_before_any_client_is_created() -> None:
    created: list[str] = []
    ctx = resolve_context(environ={})
    clients = Clients(ctx, {"spark": lambda _: created.append("spark")})
    with pytest.raises(ValidationError, match="start"):
        parse_window(start="2026-01-01")
    assert clients is not None
    assert created == []


def test_cluster_compute_clears_serverless_inherited_from_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    # A profile may carry serverless_compute_id=auto; Connect refuses a config with both compute kinds.
    class Config:
        # Mirrors the SDK: attributes live in one dict and assignment stringifies, so None becomes "None".
        def __init__(self) -> None:
            self._inner: dict[str, str] = {"serverless_compute_id": "auto"}

        def as_dict(self) -> dict[str, str]:
            return self._inner

        def __getattr__(self, name: str) -> str | None:
            return self.__dict__["_inner"].get(name)

        def __setattr__(self, name: str, value: object) -> None:
            if name == "_inner":
                object.__setattr__(self, name, value)
            else:
                self._inner[name] = str(value)

    seen: list[Config] = []

    class Builder:
        def sdkConfig(self, config: Config) -> Builder:  # noqa: N802 - mirrors the Connect builder
            seen.append(config)
            return self

        def getOrCreate(self) -> str:  # noqa: N802 - mirrors the Connect builder
            return "session"

    class Connect:
        class DatabricksSession:
            builder = Builder()

    monkeypatch.setattr(clients_module, "_sdk_config", lambda _: Config())
    monkeypatch.setattr(clients_module, "import_extra", lambda *_: Connect)
    ctx = resolve_context(environ={}, compute="cluster:0000-000000-synthetic")
    assert Clients(ctx).spark() == "session"
    assert (seen[0].cluster_id, seen[0].serverless_compute_id) == ("0000-000000-synthetic", None)
