"""Staged test-environment preflight: configuration, authentication, compute and disposable resources.

Every stage reports its own state. Only the offline stage runs without optional extras; the others
use injectable client providers, so unit tests drive them with in-memory fakes. Nothing here prints
credentials or user identity.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from bricksmith.clients import Clients, import_extra
from bricksmith.env import normalize_host, require_profile_defined
from bricksmith.errors import BricksmithError, ConfigError, MissingExtraError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from bricksmith.clients import Client
    from bricksmith.context import ExecutionContext

STAGES = ("configured", "authenticated", "compute-tested", "resources-tested", "deployment-tested")
FREE_MAX_CONCURRENT_TASKS = 5
OWNER_COMMENT = "bricksmith test-owned; safe to remove"
_PREFIX = re.compile(r"[a-z][a-z0-9_]*")


class State(StrEnum):
    """Outcome of one stage; ``unsupported`` and ``not_run`` are never passing evidence."""

    PASSED = "passed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"
    NOT_RUN = "not_run"


@dataclass(frozen=True)
class StageResult:
    """State of one stage plus a short, secret-free explanation."""

    stage: str
    state: State
    detail: str = ""


@dataclass(frozen=True)
class PreflightReport:
    """All stages of one preflight run for one environment."""

    environment: str
    stages: tuple[StageResult, ...]

    @property
    def failed(self) -> bool:
        """Whether any stage failed."""
        return any(s.state is State.FAILED for s in self.stages)

    def to_dict(self) -> dict[str, object]:
        """Return the stable serializable form."""
        return {
            "environment": self.environment,
            "stages": [{"detail": s.detail, "stage": s.stage, "state": str(s.state)} for s in self.stages],
        }

    def to_text(self) -> str:
        """Return one line per stage."""
        lines = [f"environment: {self.environment}"]
        lines += [f"{s.stage}: {s.state}" + (f" - {s.detail}" if s.detail else "") for s in self.stages]
        return "\n".join(lines)


def unique_schema_name(prefix: str, run_id: str | None = None) -> str:
    """Return ``<prefix>_<run id>``; the run id is random unless supplied."""
    return f"{prefix}_{run_id or secrets.token_hex(4)}"


def create_owned_schema(workspace: Client, catalog: str, name: str) -> None:
    """Create a schema marked as owned by this run."""
    workspace.schemas.create(name=name, catalog_name=catalog, comment=OWNER_COMMENT)


def remove_owned_schema(workspace: Client, catalog: str, name: str) -> list[str]:
    """Remove exactly the named schema (and what it holds) and return leftovers instead of assuming success."""
    leftovers: list[str] = []
    delete_error = ""
    try:
        workspace.schemas.delete(full_name=f"{catalog}.{name}", force=True)
    except Exception as error:  # noqa: BLE001 - an already-absent schema is fine; the listing below decides
        delete_error = f" (delete raised {type(error).__name__})"
    try:
        if name in {s.name for s in workspace.schemas.list(catalog_name=catalog)}:
            leftovers.append(f"schema {catalog}.{name} still exists{delete_error}")
    except Exception as error:  # noqa: BLE001 - an unverifiable cleanup is not a clean one
        leftovers.append(f"could not verify removal of {catalog}.{name}: {type(error).__name__}")
    return leftovers


def _managed_volume_type() -> object:
    """Return the SDK's managed-volume enum, or its plain name when the SDK is replaced by a fake."""
    try:
        return import_extra("databricks.sdk.service.catalog", "sdk").VolumeType.MANAGED
    except MissingExtraError:
        return "MANAGED"


def check_configured(context: ExecutionContext, environ: Mapping[str, str]) -> StageResult:
    """Validate the selected environment offline; never contacts a workspace or falls back to ``DEFAULT``."""
    problems: list[str] = []
    if context.auth_mode == "none":
        problems.append("no profile or workspace host selected")
    if context.host is None:
        problems.append("expected workspace host missing")
    if not context.catalog:
        problems.append("test catalog missing")
    if not context.schema_prefix or not _PREFIX.fullmatch(context.schema_prefix):
        problems.append("schema prefix missing or not [a-z][a-z0-9_]*")
    if context.environment == "free" and context.concurrency > FREE_MAX_CONCURRENT_TASKS:
        problems.append(f"concurrency exceeds the free limit of {FREE_MAX_CONCURRENT_TASKS}")
    if context.profile is not None:
        try:
            require_profile_defined(context.profile, environ)
        except ConfigError as error:
            problems.append(error.message)
    if problems:
        return StageResult("configured", State.FAILED, "; ".join(problems))
    return StageResult("configured", State.PASSED, f"{context.auth_mode} mode, compute {context.compute}")


def check_authenticated(context: ExecutionContext, clients: Clients) -> StageResult:
    """Resolve the workspace, compare it with the expected host and confirm an identity exists."""
    try:
        workspace = clients.workspace()
        resolved = normalize_host(str(workspace.config.host))
        if context.host is not None and resolved != context.host:
            return StageResult("authenticated", State.FAILED, "resolved workspace host differs from the expected host")
        if workspace.current_user.me() is None:
            return StageResult("authenticated", State.FAILED, "no current identity")
    except MissingExtraError as error:
        return StageResult("authenticated", State.UNSUPPORTED, error.message)
    except Exception as error:  # noqa: BLE001 - report the failure class only, never details that may hold secrets
        return StageResult("authenticated", State.FAILED, type(error).__name__)
    return StageResult("authenticated", State.PASSED, "host matches the expected workspace; identity resolved")


def check_compute(clients: Clients) -> StageResult:
    """Run ``select 1`` on the selected compute."""
    try:
        rows = clients.spark().sql("select 1 as one").collect()
        if rows[0][0] != 1:
            return StageResult("compute-tested", State.FAILED, "select 1 returned an unexpected value")
    except MissingExtraError as error:
        return StageResult("compute-tested", State.UNSUPPORTED, error.message)
    except Exception as error:  # noqa: BLE001 - report the failure class only
        return StageResult("compute-tested", State.FAILED, type(error).__name__)
    return StageResult("compute-tested", State.PASSED, "select 1 returned 1")


def check_resources(context: ExecutionContext, clients: Clients, run_id: str | None = None) -> StageResult:
    """Create then remove a unique test schema and volume; report leftovers instead of claiming success."""
    stage = "resources-tested"
    catalog = context.catalog or ""
    name = unique_schema_name(context.schema_prefix or "bricksmith_ci", run_id)
    failure: str | None = None
    created = False
    try:
        workspace = clients.workspace()
        create_owned_schema(workspace, catalog, name)
        created = True
        workspace.volumes.create(
            catalog_name=catalog, schema_name=name, name="probe", volume_type=_managed_volume_type()
        )
    except MissingExtraError as error:
        return StageResult(stage, State.UNSUPPORTED, error.message)
    except Exception as error:  # noqa: BLE001 - recorded, then cleanup still runs
        failure = type(error).__name__
    leftovers = remove_owned_schema(clients.workspace(), catalog, name) if created else []
    if leftovers:
        return StageResult(stage, State.FAILED, "leftovers: " + "; ".join(leftovers))
    if failure:
        return StageResult(stage, State.FAILED, f"{failure}; cleanup verified")
    return StageResult(stage, State.PASSED, "schema and volume created and removed")


def run_preflight(
    context: ExecutionContext,
    *,
    environ: Mapping[str, str],
    clients: Clients | None = None,
    offline_only: bool = False,
    run_id: str | None = None,
) -> PreflightReport:
    """Run the stages in order; a failed or unsupported stage leaves the later ones ``not_run``."""
    results = [check_configured(context, environ)]
    skipped = "offline only" if offline_only else "a previous stage did not pass"
    runners: list[Callable[[Clients], StageResult]] = [
        lambda c: check_authenticated(context, c),
        check_compute,
        lambda c: check_resources(context, c, run_id),
    ]
    live = clients or Clients(context)
    for stage, runner in zip(STAGES[1:4], runners, strict=True):
        if offline_only or results[-1].state is not State.PASSED:
            results.append(StageResult(stage, State.NOT_RUN, skipped))
        else:
            results.append(runner(live))
    results.append(StageResult(STAGES[4], State.NOT_RUN, "proven by the bundle test in tests/remote"))
    return PreflightReport(context.environment, tuple(results))


def failed_configuration(environment: str, error: BricksmithError) -> PreflightReport:
    """Build the report for a context that could not even be resolved."""
    stages = [StageResult(STAGES[0], State.FAILED, error.message)]
    stages += [StageResult(s, State.NOT_RUN, "configuration failed") for s in STAGES[1:]]
    return PreflightReport(environment, tuple(stages))
