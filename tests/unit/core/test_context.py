"""Context resolution: precedence, isolation, environment selection and auth rules."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from bricksmith.context import resolve_context
from bricksmith.env import auth_settings, load_dotenv, parse_dotenv, require_profile_defined
from bricksmith.errors import AuthConflictError, ConfigError, ValidationError

if TYPE_CHECKING:
    from pathlib import Path

BOTH = {
    "BRICKSMITH_TEST_ENV": "free",
    "BRICKSMITH_TARGET": "ci",
    "BRICKSMITH_FREE_PROFILE": "p-free",
    "BRICKSMITH_FREE_WORKSPACE_HOST": "https://free.example.com/",
    "BRICKSMITH_CORPORATE_PROFILE": "p-corp",
    "BRICKSMITH_CORPORATE_WORKSPACE_HOST": "not a url",
}


def test_precedence_argument_over_environ_over_dotenv_over_default() -> None:
    layers = {
        "environ": {"BRICKSMITH_FREE_PROFILE": "from-environ", "BRICKSMITH_TARGET": "env-t"},
        "dotenv": {
            "BRICKSMITH_FREE_PROFILE": "from-dotenv",
            "BRICKSMITH_TARGET": "dot-t",
            "BRICKSMITH_FREE_CATALOG": "c",
        },
        "defaults": {"BRICKSMITH_FREE_CATALOG": "d", "BRICKSMITH_FREE_SCHEMA_PREFIX": "pre"},
    }
    ctx = resolve_context(
        profile="arg",
        environ=layers["environ"],
        dotenv=layers["dotenv"],
        defaults=layers["defaults"],
    )
    assert (ctx.profile, ctx.bundle_target, ctx.catalog, ctx.schema_prefix) == ("arg", "env-t", "c", "pre")
    assert ctx.sources["profile"] == "argument"
    assert ctx.sources["bundle_target"] == "environment"
    assert ctx.sources["catalog"] == "dotenv"
    assert ctx.sources["schema_prefix"] == "default"


def test_only_selected_environment_is_validated_and_active() -> None:
    ctx = resolve_context(environ=BOTH)
    assert (ctx.environment, ctx.profile, ctx.host) == ("free", "p-free", "https://free.example.com")
    with pytest.raises(ConfigError):
        resolve_context(environ=BOTH, environment="corporate")


def test_target_ci_does_not_decide_workspace() -> None:
    ctx = resolve_context(environ={"BRICKSMITH_TARGET": "ci"})
    assert ctx.bundle_target == "ci"
    assert (ctx.profile, ctx.host, ctx.auth_mode) == (None, None, "none")
    other = resolve_context(environ={**BOTH, "BRICKSMITH_TARGET": "ci"}, target="prod")
    assert (other.bundle_target, other.profile) == ("prod", "p-free")


def test_blank_dotenv_slot_does_not_shadow_lower_layer() -> None:
    ctx = resolve_context(dotenv={"BRICKSMITH_FREE_PROFILE": ""}, defaults={"BRICKSMITH_FREE_PROFILE": "base"})
    assert ctx.profile == "base"


def test_two_contexts_share_no_state() -> None:
    catalogs = {"raw": "raw_x"}
    one = resolve_context(environ={}, catalogs=catalogs, parameters={"a": 1})
    two = resolve_context(environ={}, catalogs={"raw": "raw_y"}, parameters={"a": 2}, target="t")
    catalogs["raw"] = "mutated"
    assert one.physical_catalog("raw") == "raw_x"
    assert two.physical_catalog("raw") == "raw_y"
    assert one.parameters["a"] == 1
    assert one.bundle_target is None
    with pytest.raises(TypeError):
        one.parameters["b"] = 3  # type: ignore[index]
    with pytest.raises(AttributeError):
        one.profile = "x"  # type: ignore[misc]


def test_unmapped_logical_names_pass_through() -> None:
    ctx = resolve_context(environ={}, schemas={"silver": "silver_ci"})
    assert (ctx.physical_schema("silver"), ctx.physical_schema("gold"), ctx.physical_catalog("any")) == (
        "silver_ci",
        "gold",
        "any",
    )


@pytest.mark.parametrize(
    "name", ["DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET"]
)
def test_profile_mode_rejects_exported_credentials(name: str) -> None:
    with pytest.raises(AuthConflictError) as info:
        resolve_context(environ={**BOTH, name: "secret-value"})
    assert "secret-value" not in str(info.value)


def test_profile_mode_rejects_other_exported_profile_but_accepts_same() -> None:
    with pytest.raises(AuthConflictError):
        resolve_context(environ={**BOTH, "DATABRICKS_CONFIG_PROFILE": "other"})
    assert resolve_context(environ={**BOTH, "DATABRICKS_CONFIG_PROFILE": "p-free"}).profile == "p-free"


def test_credential_mode_validates_expected_host() -> None:
    environ = {"BRICKSMITH_FREE_WORKSPACE_HOST": "https://ci.example.com", "DATABRICKS_HOST": "https://ci.example.com/"}
    assert resolve_context(environ=environ).auth_mode == "credentials"
    with pytest.raises(AuthConflictError):
        resolve_context(environ={**environ, "DATABRICKS_HOST": "https://other.example.com"})


def test_auth_settings_map_profile_and_serverless_without_default() -> None:
    ctx = resolve_context(environ=BOTH)
    assert auth_settings(ctx) == {"DATABRICKS_CONFIG_PROFILE": "p-free", "DATABRICKS_SERVERLESS_COMPUTE_ID": "auto"}
    assert "DATABRICKS_CONFIG_PROFILE" not in auth_settings(resolve_context(environ={}))


def test_missing_profile_never_becomes_default(tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.write_text("[DEFAULT]\nhost = https://d.example.com\n[other]\nhost = https://o.example.com\n")
    environ = {"DATABRICKS_CONFIG_FILE": str(cfg)}
    require_profile_defined("other", environ)
    with pytest.raises(ConfigError, match="DEFAULT"):
        require_profile_defined("absent", environ)


def test_invalid_settings_rejected() -> None:
    with pytest.raises(ConfigError):
        resolve_context(environ={}, environment="Bad Name")
    with pytest.raises(ConfigError):
        resolve_context(environ={}, compute="gpu")
    with pytest.raises(ConfigError):
        resolve_context(environ={}, concurrency=0)
    with pytest.raises(ConfigError):
        resolve_context(environ={}, host="http://x")
    with pytest.raises(ValidationError):
        resolve_context(environ={}, parameters={"bad key": 1})


def test_dotenv_parser_and_explicit_loader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = "# c\nexport A=1\nB = 'two words' # note\nC=x # trailing\n\nD=\n"
    assert parse_dotenv(text) == {"A": "1", "B": "two words", "C": "x", "D": ""}
    with pytest.raises(ConfigError):
        parse_dotenv("not a pair")
    with pytest.raises(ConfigError):
        parse_dotenv("A='open")
    file = tmp_path / ".env"
    file.write_text("BRICKSMITH_FREE_PROFILE=loaded\n")
    monkeypatch.delenv("BRICKSMITH_FREE_PROFILE", raising=False)
    values = load_dotenv(file)
    assert "BRICKSMITH_FREE_PROFILE" not in os.environ
    assert resolve_context(environ={}, dotenv=values).profile == "loaded"
    with pytest.raises(ConfigError):
        load_dotenv(tmp_path / "missing")
