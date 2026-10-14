"""Clients are lazy, injectable and per instance; invalid input never reaches a provider."""

from __future__ import annotations

import pytest

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
