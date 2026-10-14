"""Offline governance behaviour against fake live state."""
# ruff: noqa: EM101, TRY003

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from typing import Any

import pytest

from bricksmith.governance import (
    DesiredState,
    GovernanceConfigError,
    GovernancePlan,
    GrantDecl,
    LiveResource,
    PolicyOutput,
    PrincipalMappingError,
    apply_plan,
    audit_governance,
    desired_state,
    governance_findings,
    governance_report,
    parse_config,
    plan_governance,
    read_live,
)

TODAY = dt.date(2026, 10, 13)
A = "c.s.orders"
B = "c.s.scratch"
RAW: dict[str, Any] = {
    "version": 1,
    "categories": ["internal", "restricted"],
    "retention": {"short": 30},
    "principals": {
        "readers": {"test": "group-a", "old": {"name": "group-old", "expires": "2026-01-01"}},
        "stewards": {"test": "group-b", "old": "group-b"},
    },
    "resource": [
        {
            "object": A,
            "managed": True,
            "classification": "restricted",
            "owner": "stewards",
            "retention": "short",
            "comment": "orders",
            "tags": {"domain": "sales"},
            "grants": [{"principal": "readers", "privileges": ["SELECT"]}],
            "masks": {"email": "mask_email"},
        },
        {"object": B, "managed": False},
        {"object": "c.s.loose", "managed": True},
    ],
    "policy": [{"name": "base", "classification": "restricted", "tags": {"review": "required"}}],
}


def desired(raw: dict[str, Any] | None = None, env: str = "test", **kw: Any) -> DesiredState:  # noqa: ANN401
    return desired_state(parse_config(raw or RAW), environment=env, today=TODAY, **kw)


def converged() -> dict[str, LiveResource]:
    d = desired()
    tags = {c.key: c.value for c in d.controls if c.kind == "tag" and c.resource == A}
    grants = frozenset(("group-a", "SELECT") for c in d.controls if c.kind == "grant")
    return {
        A: LiveResource(tags=tags, grants=grants, comment="orders"),
        "c.s.loose": LiveResource(),
    }


def tweak(live: dict[str, LiveResource], name: str = A, **changes: Any) -> None:  # noqa: ANN401
    """Replace fields of a frozen live resource in the mapping."""
    live[name] = dataclasses.replace(live[name], **changes)


class FakeSql:
    """Records statements; optionally refuses some."""

    def __init__(self, refuse: str = "") -> None:
        self.sql: list[str] = []
        self.refuse = refuse

    def __call__(self, sql: str) -> list[dict[str, Any]]:
        self.sql.append(sql)
        if self.refuse and self.refuse in sql:
            raise RuntimeError("PERMISSION_DENIED for someone@example.com at https://host.example/x")
        return []


def test_four_states_are_distinct() -> None:
    live = converged()
    tweak(live, tags={k: v for k, v in live[A].tags.items() if k != "domain"})  # desired
    plan = plan_governance(desired(), live)
    assert [o.control_id for o in plan.operations] == [f"tag:{A}:domain"]
    audit = audit_governance(desired(), live, applied=frozenset({f"tag:{A}:domain"}))
    states = {c.control.id: c.state for c in audit.controls}
    assert states[f"tag:{A}:domain"] == "applied"  # applied, not visible live
    assert states[f"tag:{A}:sensitivity"] == "verified"
    assert states[f"mask:{A}:email"] == "unavailable"
    assert {c.state for c in audit_governance(desired(), live).controls} == {"desired", "verified", "unavailable"}
    refused = audit_governance(desired(), live, refused={f"tag:{A}:domain": "denied"})
    assert next(c for c in refused.controls if c.control.key == "domain").state == "unavailable"


def test_unmanaged_resources_are_untouched() -> None:
    live = converged()
    live[B] = LiveResource(tags={"x": "y"}, grants=frozenset({("p", "SELECT")}))
    plan = plan_governance(desired(), live)
    assert all(B not in o.resource for o in plan.operations)
    assert all(e.resource != B for e in plan.external)
    assert B not in {c.control.resource for c in audit_governance(desired(), live).controls}


def test_external_controls_reported_never_deleted() -> None:
    live = converged()
    live[A].tags["stray"] = "1"
    live[A].tags["system.internal"] = "x"
    tweak(live, grants=live[A].grants | {("someone-else", "MODIFY")}, row_filters=("flt",), masks=("email",))
    plan = plan_governance(desired(), live)
    kinds = {e.id for e in plan.external}
    assert {f"tag:{A}:stray", f"grant:{A}:someone-else:MODIFY", f"row_filter:{A}:flt"} <= kinds
    assert not any("system." in k for k in kinds)
    assert not plan.operations  # nothing to run; nothing deleted
    assert not any("REVOKE" in o.sql or "UNSET" in o.sql for o in plan.operations)
    ids = {f.id for f in governance_findings(audit_governance(desired(), live)).findings}
    assert f"governance.external.policy:row_filter:{A}:flt" in ids


def test_classification_never_reported_as_masking() -> None:
    audit = audit_governance(desired(), converged())
    report = governance_report(audit)
    entry = next(r for r in report["resources"] if r["name"] == A)
    assert entry["enforcement"]["classification"] == "metadata-only"
    assert entry["enforcement"]["effective_masking"] is False
    assert entry["enforcement"]["masking"] == "none"
    assert "not column masking" in report["note"]
    mask = next(c for c in entry["controls"] if c["kind"] == "mask")
    assert mask["state"] == "unavailable"
    assert "no mutation adapter" in mask["reason"]


def test_detected_mask_is_read_only_and_not_effective() -> None:
    live = converged()
    tweak(live, masks=("email",))
    resources = governance_report(audit_governance(desired(), live))["resources"]
    entry = next(r for r in resources if r["name"] == A)
    assert "read-only" in entry["enforcement"]["masking"]
    assert entry["enforcement"]["effective_masking"] is False
    mask = next(c for c in entry["controls"] if c["kind"] == "mask")
    assert mask["state"] == "unavailable"


def test_unclassified_visible_in_coverage_and_findings() -> None:
    audit = audit_governance(desired(), converged())
    report = governance_report(audit)
    assert report["unclassified"] == ["c.s.loose"]
    assert report["classification"] == {"restricted": 1, "unclassified": 1}
    assert report["unmanaged"] == 1
    checks = governance_findings(audit)
    assert "governance.unclassified:c.s.loose" in {f.id for f in checks.findings}
    strict = governance_findings(audit, {"governance.unclassified": "error"})
    assert not strict.ok
    quiet = governance_findings(audit, {"governance.*": "off"})
    assert quiet.findings == ()


def test_drift_findings_use_configurable_severity() -> None:
    live = converged()
    live[A].tags["domain"] = "other"
    audit = audit_governance(desired(), live)
    default = {f.id: f.severity for f in governance_findings(audit).findings}
    assert default[f"governance.drift.different:tag:{A}:domain"] == "error"
    relaxed = governance_findings(audit, {"governance.drift": "warning"})
    assert relaxed.ok


@pytest.mark.parametrize(
    ("env", "message"),
    [("old", "expired"), ("other", "no mapping")],
)
def test_expired_or_unmapped_principal_fails(env: str, message: str) -> None:
    with pytest.raises(PrincipalMappingError, match=message):
        desired(env=env)


def test_unknown_principal_fails() -> None:
    raw = json.loads(json.dumps(RAW))
    raw["resource"][0]["grants"][0]["principal"] = "ghosts"
    with pytest.raises(PrincipalMappingError, match="not declared"):
        desired(raw)


def test_unmanaged_resource_does_not_resolve_principals() -> None:
    raw = json.loads(json.dumps(RAW))
    raw["resource"][1]["grants"] = [{"principal": "ghosts", "privileges": ["SELECT"]}]
    assert desired(raw)


def test_idempotent_replan_after_apply() -> None:
    d = desired()
    run = FakeSql()
    live: dict[str, LiveResource] = {A: LiveResource(), "c.s.loose": LiveResource()}
    plan = plan_governance(d, live)
    assert plan.operations
    assert apply_plan(run, plan).refused == {}
    assert any(s.startswith("GRANT SELECT ON TABLE `c`.`s`.`orders` TO `group-a`") for s in run.sql)
    assert any("SET TAGS ('sensitivity' = 'restricted')" in s for s in run.sql)
    assert plan_governance(d, converged()).is_noop
    assert plan_governance(d, converged()).to_dict() == plan_governance(d, converged()).to_dict()


def test_apply_records_redacted_refusals_and_continues() -> None:
    run = FakeSql(refuse="GRANT")
    plan = plan_governance(desired(), {A: LiveResource(), "c.s.loose": LiveResource()})
    result = apply_plan(run, plan)
    assert list(result.refused) == [f"grant:{A}:group-a:SELECT"]
    reason = next(iter(result.refused.values()))
    assert "example.com" not in reason
    assert "<identity>" in reason
    assert len(result.applied) == len(plan.operations) - 1


def test_missing_resource_and_unreadable_sections_are_unavailable() -> None:
    live = converged()
    live["c.s.loose"] = LiveResource(exists=False)
    tweak(live, unreadable={"grants": "denied"})
    states = {c.control.id: c for c in audit_governance(desired(), live).controls}
    assert states[f"grant:{A}:group-a:SELECT"].state == "unavailable"
    assert "unreadable" in states[f"grant:{A}:group-a:SELECT"].reason


def test_hooks_and_rule_conflicts() -> None:
    def hook(decl: Any) -> PolicyOutput | None:  # noqa: ANN401
        if decl.classification == "restricted":
            return PolicyOutput({"hooked": "yes"}, (GrantDecl("readers", ("SELECT", "USE SCHEMA")),))
        return None

    d = desired(hooks=[hook])
    ids = {c.id for c in d.controls}
    assert f"tag:{A}:hooked" in ids
    assert f"grant:{A}:group-a:USE SCHEMA" in ids
    raw = json.loads(json.dumps(RAW))
    raw["policy"][0]["tags"] = {"domain": "conflict"}
    with pytest.raises(GovernanceConfigError, match="conflicting"):
        desired(raw)


@pytest.mark.parametrize(
    "patch",
    [
        {"version": 2},
        {"categories": ["unclassified"]},
        {"surprise": 1},
        {"severity": {"x": "loud"}},
        {"resource": [{"object": A, "classification": "secretive"}]},
        {"resource": [{"object": A, "retention": "nope"}]},
        {"resource": [{"object": A, "grants": [{"principal": "p", "privileges": ["select;"]}]}]},
        {"resource": [{"object": A}, {"object": A}]},
    ],
)
def test_invalid_config_is_rejected(patch: dict[str, Any]) -> None:
    with pytest.raises(GovernanceConfigError):
        parse_config({**RAW, **patch})


def test_read_live_marks_failed_sections_unreadable() -> None:
    def run(sql: str) -> list[dict[str, Any]]:
        if "table_tags" in sql:
            raise RuntimeError("no access")
        if "information_schema.tables" in sql:
            return [{"comment": "orders"}]
        if sql.startswith("SHOW GRANTS"):
            return [{"Principal": "group-a", "ActionType": "SELECT", "ObjectType": "TABLE", "ObjectKey": A}]
        return []

    live = read_live(run, desired().resources)
    assert set(live) == {A, "c.s.loose"}  # managed only
    assert live[A].unreadable == {"tags": "no access"}
    assert live[A].grants == {("group-a", "SELECT")}
    assert live[A].comment == "orders"


def test_plan_round_trips() -> None:
    plan = plan_governance(desired(), {A: LiveResource(), "c.s.loose": LiveResource()})
    assert GovernancePlan.from_dict(json.loads(plan.to_json())) == plan
