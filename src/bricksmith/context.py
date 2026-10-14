"""Immutable execution context and the precedence-ordered resolution that builds it."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from bricksmith import env
from bricksmith.errors import ConfigError, ValidationError
from bricksmith.params import Scalar, validate_parameters

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_ENVIRONMENT = "free"
DEFAULT_COMPUTE = "serverless"
DEFAULT_CONCURRENCY = 2
SOURCE_ORDER = ("argument", "environment", "dotenv", "default")
type AuthMode = Literal["profile", "credentials", "none"]


def _frozen[T](mapping: Mapping[str, T]) -> Mapping[str, T]:
    return MappingProxyType(dict(sorted(mapping.items())))


@dataclass(frozen=True)
class ExecutionContext:
    """Everything a remote operation needs; passed explicitly, never read from globals."""

    environment: str
    profile: str | None = None
    host: str | None = None
    bundle_target: str | None = None
    catalog: str | None = None
    schema_prefix: str | None = None
    compute: str = DEFAULT_COMPUTE
    concurrency: int = DEFAULT_CONCURRENCY
    catalogs: Mapping[str, str] = field(default_factory=dict)
    schemas: Mapping[str, str] = field(default_factory=dict)
    parameters: Mapping[str, Scalar] = field(default_factory=dict)
    sources: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Copy and freeze the mappings so no two contexts share mutable state."""
        for name in ("catalogs", "schemas", "parameters", "sources"):
            object.__setattr__(self, name, _frozen(getattr(self, name)))

    @property
    def auth_mode(self) -> AuthMode:
        """``profile`` when a named profile is set, ``credentials`` when only a host is, else ``none``."""
        if self.profile is not None:
            return "profile"
        return "credentials" if self.host is not None else "none"

    def physical_catalog(self, logical: str) -> str:
        """Map a logical catalog to its physical name (unchanged when unmapped)."""
        return self.catalogs.get(logical, logical)

    def physical_schema(self, logical: str) -> str:
        """Map a logical schema to its physical name (unchanged when unmapped)."""
        return self.schemas.get(logical, logical)

    def to_dict(self) -> dict[str, object]:
        """Return a sorted, secret-free serializable form."""
        return {
            "auth_mode": self.auth_mode,
            "bundle_target": self.bundle_target,
            "catalog": self.catalog,
            "catalogs": dict(self.catalogs),
            "compute": self.compute,
            "concurrency": self.concurrency,
            "environment": self.environment,
            "host": self.host,
            "parameters": dict(self.parameters),
            "profile": self.profile,
            "schema_prefix": self.schema_prefix,
            "schemas": dict(self.schemas),
            "sources": dict(self.sources),
        }


class _Layers:
    """Ordered settings layers; blank values count as unset so an empty ``.env`` slot never shadows."""

    def __init__(self, layers: Mapping[str, Mapping[str, str]]) -> None:
        self._layers = [(name, layers[name]) for name in SOURCE_ORDER]

    def get(self, key: str) -> tuple[str, str] | None:
        for name, values in self._layers:
            value = values.get(key, "").strip()
            if value:
                return value, name
        return None


def resolve_context(  # noqa: PLR0913 - one keyword per documented explicit setting
    *,
    environment: str | None = None,
    profile: str | None = None,
    host: str | None = None,
    target: str | None = None,
    compute: str | None = None,
    concurrency: int | None = None,
    catalogs: Mapping[str, str] | None = None,
    schemas: Mapping[str, str] | None = None,
    parameters: Mapping[str, Scalar] | None = None,
    environ: Mapping[str, str] | None = None,
    dotenv: Mapping[str, str] | None = None,
    defaults: Mapping[str, str] | None = None,
) -> ExecutionContext:
    """Resolve one context: arguments > process environment > loaded dotenv > project defaults.

    ``environ`` defaults to the process environment, read now and never modified. Only the
    selected environment's fields are read, so a half-configured other environment is ignored.
    """
    process = os.environ if environ is None else environ
    explicit = {
        env.KEY_ENVIRONMENT: environment or "",
        env.KEY_TARGET: target or "",
        env.KEY_CONCURRENCY: "" if concurrency is None else str(concurrency),
    }
    layers = _Layers({"argument": explicit, "environment": process, "dotenv": dotenv or {}, "default": defaults or {}})
    found = layers.get(env.KEY_ENVIRONMENT)
    name = found[0] if found else DEFAULT_ENVIRONMENT
    if not env.ENV_NAME.fullmatch(name):
        msg = f"environment name {name!r} must match [a-z][a-z0-9_]*"
        raise ConfigError(msg)
    sources = {"environment": found[1] if found else "default"}
    for field_name, value in (("PROFILE", profile), ("WORKSPACE_HOST", host), ("COMPUTE", compute)):
        explicit[env.env_key(name, field_name)] = value or ""
    values: dict[str, str | None] = {}
    keys = {"bundle_target": env.KEY_TARGET, "concurrency": env.KEY_CONCURRENCY}
    keys |= {f.lower(): env.env_key(name, f) for f in env.ENV_FIELDS}
    for attr, key in keys.items():
        hit = layers.get(key)
        values[attr] = hit[0] if hit else None
        sources[attr] = hit[1] if hit else "unset"
    resolved_host = env.normalize_host(values["workspace_host"]) if values["workspace_host"] else None
    resolved_profile = values["profile"]
    env.check_auth_conflicts({**(dotenv or {}), **process}, profile=resolved_profile, host=resolved_host)
    resolved_compute = values["compute"] or DEFAULT_COMPUTE
    if resolved_compute != "serverless" and not resolved_compute.startswith("cluster:"):
        msg = f"compute {resolved_compute!r} must be 'serverless' or 'cluster:<id>'"
        raise ConfigError(msg)
    try:
        limit = int(values["concurrency"] or DEFAULT_CONCURRENCY)
    except ValueError:
        msg = f"concurrency {values['concurrency']!r} is not an integer"
        raise ConfigError(msg) from None
    if limit < 1:
        msg = "concurrency must be at least 1"
        raise ConfigError(msg)
    return ExecutionContext(
        environment=name,
        profile=resolved_profile,
        host=resolved_host,
        bundle_target=values["bundle_target"],
        catalog=values["catalog"],
        schema_prefix=values["schema_prefix"],
        compute=resolved_compute,
        concurrency=limit,
        catalogs=_checked_names(catalogs, "catalog"),
        schemas=_checked_names(schemas, "schema"),
        parameters=validate_parameters(parameters or {}),
        sources=sources,
    )


def _checked_names(mapping: Mapping[str, str] | None, what: str) -> dict[str, str]:
    checked: dict[str, str] = {}
    for logical, physical in (mapping or {}).items():
        if not logical or not physical:
            msg = f"{what} mapping {logical!r} -> {physical!r} has an empty name"
            raise ValidationError(msg)
        checked[logical] = physical
    return checked
