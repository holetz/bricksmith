"""Opt-in dotenv loading and the free/corporate field contract mapped to unified authentication.

Nothing here runs on import, reads ``.env`` implicitly or mutates ``os.environ``.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from bricksmith.errors import AuthConflictError, ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from bricksmith.context import ExecutionContext

KEY_ENVIRONMENT = "BRICKSMITH_TEST_ENV"
KEY_TARGET = "BRICKSMITH_TARGET"
KEY_CONCURRENCY = "BRICKSMITH_TEST_MAX_CONCURRENT_TASKS"
ENV_FIELDS = ("PROFILE", "WORKSPACE_HOST", "CATALOG", "SCHEMA_PREFIX", "COMPUTE")
# Exported unified-auth settings that would silently override or mix with a selected profile.
CONFLICTING_AUTH = ("DATABRICKS_HOST", "DATABRICKS_TOKEN", "DATABRICKS_CLIENT_ID", "DATABRICKS_CLIENT_SECRET")
ENV_NAME = re.compile(r"[a-z][a-z0-9_]*")
_LINE = re.compile(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)")


def env_key(environment: str, field: str) -> str:
    """Return the dotenv key of ``field`` for ``environment`` (for example ``BRICKSMITH_FREE_PROFILE``)."""
    return f"BRICKSMITH_{environment.upper()}_{field}"


def _unquote(raw: str, line_no: int) -> str:
    raw = raw.strip()
    if raw[:1] in {'"', "'"}:
        end = raw.find(raw[0], 1)
        rest = raw[end + 1 :].strip() if end > 0 else "?"
        if end < 0 or (rest and not rest.startswith("#")):
            msg = f"dotenv line {line_no}: unbalanced quote"
            raise ConfigError(msg)
        return raw[1:end]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse ``KEY=VALUE`` lines (comments, ``export`` and quotes allowed); no interpolation."""
    values: dict[str, str] = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE.fullmatch(stripped)
        if match is None:
            msg = f"dotenv line {line_no} is not KEY=VALUE"
            raise ConfigError(msg)
        values[match.group(1)] = _unquote(match.group(2), line_no)
    return values


def load_dotenv(path: str | Path) -> dict[str, str]:
    """Read an explicitly named dotenv file and return its values without touching ``os.environ``."""
    file = Path(path)
    try:
        text = file.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read dotenv file {file}: {exc.strerror}"
        raise ConfigError(msg) from None
    return parse_dotenv(text)


def normalize_host(host: str) -> str:
    """Return a canonical ``https://host`` URL or raise when the value is not a workspace host."""
    parts = urlsplit(host.strip())
    if parts.scheme != "https" or not parts.netloc or parts.path.strip("/") or parts.query:
        msg = f"workspace host {host!r} must look like https://<host>"
        raise ConfigError(msg)
    return f"https://{parts.netloc.lower()}"


def check_auth_conflicts(exported: Mapping[str, str], *, profile: str | None, host: str | None) -> None:
    """Reject exported auth settings that conflict with the selected profile or expected host."""
    present = {k: v.strip() for k, v in exported.items() if k.startswith("DATABRICKS_") and v.strip()}
    if profile is not None:
        names = [k for k in CONFLICTING_AUTH if k in present]
        if present.get("DATABRICKS_CONFIG_PROFILE", profile) != profile:
            names.append("DATABRICKS_CONFIG_PROFILE")
        if names:
            msg = f"profile mode ({profile!r}) conflicts with exported {', '.join(sorted(names))}; unset them"
            raise AuthConflictError(msg)
    elif host is not None:
        if "DATABRICKS_CONFIG_PROFILE" in present:
            msg = "credential mode conflicts with exported DATABRICKS_CONFIG_PROFILE; unset it"
            raise AuthConflictError(msg)
        exported_host = present.get("DATABRICKS_HOST")
        if exported_host is not None and normalize_host(exported_host) != host:
            msg = "exported DATABRICKS_HOST differs from the expected workspace host"
            raise AuthConflictError(msg)


def auth_settings(context: ExecutionContext) -> dict[str, str]:
    """Return unified-auth variables for a child process or explicit config; never exported by bricksmith."""
    settings: dict[str, str] = {}
    if context.profile is not None:
        settings["DATABRICKS_CONFIG_PROFILE"] = context.profile
    elif context.host is not None:
        settings["DATABRICKS_HOST"] = context.host
    if context.compute == "serverless":
        settings["DATABRICKS_SERVERLESS_COMPUTE_ID"] = "auto"
    elif context.compute.startswith("cluster:"):
        settings["DATABRICKS_CLUSTER_ID"] = context.compute.removeprefix("cluster:")
    return settings


def require_profile_defined(profile: str, environ: Mapping[str, str]) -> None:
    """Fail when ``profile`` is absent from the external config file; never fall back to ``DEFAULT``."""
    path = Path(environ.get("DATABRICKS_CONFIG_FILE") or Path.home() / ".databrickscfg")
    parser = configparser.ConfigParser(default_section="__none__", interpolation=None)
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        msg = f"cannot read Databricks config file {path}"
        raise ConfigError(msg) from None
    if not parser.has_section(profile):
        msg = f"profile {profile!r} is not defined in {path}; bricksmith does not fall back to DEFAULT"
        raise ConfigError(msg)
