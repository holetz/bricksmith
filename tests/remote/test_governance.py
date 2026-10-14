"""Governance on the selected workspace: tags and a grant on a synthetic managed table."""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING, Any

import pytest

from bricksmith.governance import (
    apply_plan,
    audit_governance,
    desired_state,
    governance_report,
    parse_config,
    plan_governance,
    read_live,
    spark_runner,
)

if TYPE_CHECKING:
    from bricksmith.clients import Clients
    from bricksmith.context import ExecutionContext

TODAY = dt.date(2026, 10, 14)


@pytest.fixture
def setup(remote_context: ExecutionContext, remote_clients: Clients, test_schema: str) -> dict[str, Any]:
    spark = remote_clients.spark()
    name = f"{remote_context.catalog}.{test_schema}.gov_orders"
    spark.sql(f"CREATE TABLE {name} (id INT) COMMENT 'synthetic'")
    config = parse_config(
        {
            "version": 1,
            "categories": ["internal"],
            "principals": {"everyone": {remote_context.environment: "account users"}},
            "resource": [
                {
                    "object": name,
                    "managed": True,
                    "classification": "internal",
                    "tags": {"domain": "synthetic"},
                    "grants": [{"principal": "everyone", "privileges": ["SELECT"]}],
                }
            ],
        }
    )
    desired = desired_state(config, environment=remote_context.environment, today=TODAY)
    return {"spark": spark, "name": name, "desired": desired, "run": spark_runner(spark)}


def test_declared_tag_and_grant_applied_and_verified(setup: dict[str, Any]) -> None:
    desired, run = setup["desired"], setup["run"]
    plan = plan_governance(desired, read_live(run, desired.resources))
    assert len(plan.operations) == 3  # two tags and one grant
    result = apply_plan(run, plan)
    live = read_live(run, desired.resources)
    audit = audit_governance(desired, live, applied=frozenset(result.applied), refused=result.refused)
    states = {c.control.id: c.state for c in audit.controls}
    assert result.refused == {}, result.refused
    assert set(states.values()) == {"verified"}, states
    assert plan_governance(desired, live).is_noop  # re-apply is a no-op
    entry = governance_report(audit)["resources"][0]
    assert entry["enforcement"]["effective_masking"] is False


def test_out_of_band_tag_is_reported_not_deleted(setup: dict[str, Any]) -> None:
    desired, run, name, spark = setup["desired"], setup["run"], setup["name"], setup["spark"]
    apply_plan(run, plan_governance(desired, read_live(run, desired.resources)))
    spark.sql(f"ALTER TABLE {name} SET TAGS ('stray' = 'external')")
    live = read_live(run, desired.resources)
    plan = plan_governance(desired, live)
    assert [e.key for e in plan.external if e.kind == "tag"] == ["stray"]
    assert plan.is_noop
    apply_plan(run, plan)
    assert read_live(run, desired.resources)[name].tags["stray"] == "external"
