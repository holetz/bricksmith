"""Governance project configuration (TOML) and the resolution of desired controls."""
# ruff: noqa: EM102, TRY003 - mapping errors carry their own specific messages

from __future__ import annotations

import datetime as dt
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

from bricksmith.governance.errors import GovernanceConfigError, PrincipalMappingError
from bricksmith.governance.model import (
    COMMENT,
    GRANT,
    MASK,
    PROPERTY,
    RESOURCE_KINDS,
    ROW_FILTER,
    TAG,
    Control,
    DesiredState,
    ResolvedResource,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

FORMAT_VERSION = 1
UNCLASSIFIED = "unclassified"
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_PRIVILEGE = re.compile(r"[A-Z][A-Z ]*[A-Z]")
_DEFAULT_TAG_KEYS = {"classification": "sensitivity", "owner": "owner", "retention": "retention"}
_TOP = frozenset({"version", "categories", "tag_keys", "retention", "principals", "resource", "policy", "severity"})
_RESOURCE = frozenset({
    "object", "kind", "managed", "classification", "owner", "retention", "comment", "tags", "properties",
    "grants", "masks", "row_filter",
})  # fmt: skip


def _fail(message: str) -> NoReturn:
    raise GovernanceConfigError(message) from None


@dataclass(frozen=True)
class GrantDecl:
    """Privileges granted to a logical principal."""

    principal: str
    privileges: tuple[str, ...]


@dataclass(frozen=True)
class PrincipalRef:
    """A principal name in one environment, optionally valid only until ``expires`` (inclusive)."""

    name: str
    expires: dt.date | None = None


@dataclass(frozen=True)
class ResourceDecl:
    """A declared resource; only ``managed`` ones receive controls."""

    object: str
    kind: str = "table"
    managed: bool = False
    classification: str = UNCLASSIFIED
    owner: str = ""
    retention: str = ""
    comment: str | None = None
    tags: Mapping[str, str] = field(default_factory=dict)
    properties: Mapping[str, str] = field(default_factory=dict)
    grants: tuple[GrantDecl, ...] = ()
    masks: Mapping[str, str] = field(default_factory=dict)
    row_filter: str = ""


@dataclass(frozen=True)
class PolicyOutput:
    """What a policy rule or hook asks for: tags and grants (logical principals)."""

    tags: Mapping[str, str] = field(default_factory=dict)
    grants: tuple[GrantDecl, ...] = ()


@dataclass(frozen=True)
class PolicyRule:
    """A declared rule: every managed resource of ``classification`` gets ``output``."""

    name: str
    classification: str
    output: PolicyOutput


PolicyHook = Callable[[ResourceDecl], PolicyOutput | None]
"""A project-supplied callable mapping a declared resource to desired tags and grants."""


@dataclass(frozen=True)
class GovernanceConfig:
    """Parsed ``governance.toml``."""

    categories: tuple[str, ...] = ()
    tag_keys: Mapping[str, str] = field(default_factory=lambda: dict(_DEFAULT_TAG_KEYS))
    retention: Mapping[str, int] = field(default_factory=dict)
    principals: Mapping[str, Mapping[str, PrincipalRef]] = field(default_factory=dict)
    resources: tuple[ResourceDecl, ...] = ()
    rules: tuple[PolicyRule, ...] = ()
    severity: Mapping[str, str] = field(default_factory=dict)


def _str_map(value: object, where: str) -> dict[str, str]:
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        _fail(f"{where}: expected a table of strings")
    return dict(value)


def _tags(value: object, where: str) -> dict[str, str]:
    tags = _str_map(value, where)
    bad = [k for k in tags if not _KEY.fullmatch(k)]
    if bad:
        _fail(f"{where}: invalid tag keys {sorted(bad)}")
    return tags


def _grants(value: object, where: str) -> tuple[GrantDecl, ...]:
    if not isinstance(value, list):
        _fail(f"{where}: expected a list of grants")
    out: list[GrantDecl] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"principal", "privileges"}:
            _fail(f"{where}: a grant needs exactly 'principal' and 'privileges'")
        privileges = item["privileges"]
        if (
            not isinstance(item["principal"], str)
            or not isinstance(privileges, list)
            or not privileges
            or not all(isinstance(p, str) and _PRIVILEGE.fullmatch(p) for p in privileges)
        ):
            _fail(f"{where}: invalid grant {item!r}")
        out.append(GrantDecl(item["principal"], tuple(sorted(set(privileges)))))
    return tuple(out)


def _resource(data: dict[str, Any], index: int, categories: tuple[str, ...]) -> ResourceDecl:
    where = f"resource[{index}]"
    unknown = sorted(set(data) - _RESOURCE)
    if unknown or not isinstance(data.get("object"), str) or not data["object"]:
        _fail(f"{where}: needs 'object' and rejects unknown keys {unknown}")
    kind = data.get("kind", "table")
    classification = data.get("classification", UNCLASSIFIED)
    if kind not in RESOURCE_KINDS:
        _fail(f"{where}: kind must be one of {list(RESOURCE_KINDS)}")
    if classification != UNCLASSIFIED and classification not in categories:
        _fail(f"{where}: classification {classification!r} is not a declared category")
    properties = _str_map(data.get("properties", {}), f"{where}.properties")
    if properties and kind not in ("table", "view"):
        _fail(f"{where}: properties are supported on tables and views only")
    texts = {k: data.get(k, "") for k in ("owner", "retention", "row_filter")}
    if not all(isinstance(v, str) for v in texts.values()) or not isinstance(data.get("managed", False), bool):
        _fail(f"{where}: owner, retention, row_filter must be strings and managed a boolean")
    return ResourceDecl(
        object=data["object"],
        kind=kind,
        managed=data.get("managed", False),
        classification=classification,
        owner=texts["owner"],
        retention=texts["retention"],
        comment=data.get("comment"),
        tags=_tags(data.get("tags", {}), f"{where}.tags"),
        properties=properties,
        grants=_grants(data.get("grants", []), f"{where}.grants"),
        masks=_str_map(data.get("masks", {}), f"{where}.masks"),
        row_filter=texts["row_filter"],
    )


def _principals(data: object) -> dict[str, dict[str, PrincipalRef]]:
    if not isinstance(data, dict):
        _fail("principals: expected a table")
    out: dict[str, dict[str, PrincipalRef]] = {}
    for logical, envs in data.items():
        if not isinstance(envs, dict):
            _fail(f"principals.{logical}: expected a table of environments")
        out[logical] = {}
        for env_name, ref in envs.items():
            if isinstance(ref, str):
                out[logical][env_name] = PrincipalRef(ref)
            elif isinstance(ref, dict) and isinstance(ref.get("name"), str) and set(ref) <= {"name", "expires"}:
                try:
                    expires = dt.date.fromisoformat(str(ref["expires"])) if "expires" in ref else None
                except ValueError:
                    _fail(f"principals.{logical}.{env_name}: expires must be an ISO date")
                out[logical][env_name] = PrincipalRef(ref["name"], expires)
            else:
                _fail(f"principals.{logical}.{env_name}: expected a name or {{name, expires}}")
    return out


def _rules(data: object, categories: tuple[str, ...]) -> tuple[PolicyRule, ...]:
    if not isinstance(data, list):
        _fail("policy: expected an array of tables")
    rules: list[PolicyRule] = []
    for i, item in enumerate(data):
        where = f"policy[{i}]"
        if not isinstance(item, dict) or set(item) - {"name", "classification", "tags", "grants"}:
            _fail(f"{where}: invalid keys")
        if not isinstance(item.get("name"), str) or item.get("classification") not in categories:
            _fail(f"{where}: needs a name and a declared classification")
        out = PolicyOutput(_tags(item.get("tags", {}), f"{where}.tags"), _grants(item.get("grants", []), where))
        rules.append(PolicyRule(item["name"], item["classification"], out))
    return tuple(rules)


def parse_config(data: dict[str, Any], source: str = "governance") -> GovernanceConfig:
    """Validate parsed TOML into a ``GovernanceConfig``."""
    if data.get("version") != FORMAT_VERSION:
        _fail(f"{source}: version must be {FORMAT_VERSION}")
    unknown = sorted(set(data) - _TOP)
    if unknown:
        _fail(f"{source}: unknown keys {unknown}")
    categories = data.get("categories", [])
    if (
        not isinstance(categories, list)
        or not all(isinstance(c, str) and _KEY.fullmatch(c) for c in categories)
        or UNCLASSIFIED in categories
    ):
        _fail(f"{source}: categories must be identifiers; '{UNCLASSIFIED}' is implicit")
    cats = tuple(categories)
    tag_keys = {**_DEFAULT_TAG_KEYS, **_tags(data.get("tag_keys", {}), "tag_keys")}
    retention = data.get("retention", {})
    if not isinstance(retention, dict) or not all(isinstance(v, int) and v > 0 for v in retention.values()):
        _fail("retention: expected names mapped to a positive number of days")
    severity = _str_map(data.get("severity", {}), "severity")
    if not set(severity.values()) <= {"error", "warning", "off"}:
        _fail("severity: values are error, warning or off")
    resources = tuple(_resource(r, i, cats) for i, r in enumerate(data.get("resource", [])))
    names = [r.object for r in resources]
    if len(set(names)) != len(names):
        _fail(f"{source}: duplicate resource declarations")
    for r in resources:
        if r.retention and r.retention not in retention:
            _fail(f"{r.object}: retention {r.retention!r} is not declared")
    return GovernanceConfig(
        categories=cats,
        tag_keys=tag_keys,
        retention=dict(retention),
        principals=_principals(data.get("principals", {})),
        resources=resources,
        rules=_rules(data.get("policy", []), cats),
        severity=severity,
    )


def load_config(path: Path | str) -> GovernanceConfig:
    """Read and validate a governance TOML file."""
    try:
        with Path(path).open("rb") as handle:
            return parse_config(tomllib.load(handle), str(path))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _fail(f"{path}: cannot read governance configuration ({type(exc).__name__})")


def resolve_principal(config: GovernanceConfig, logical: str, environment: str, today: dt.date) -> str:
    """Map a logical principal to its name in ``environment``; unknown, unmapped or expired fails."""
    refs = config.principals.get(logical)
    if refs is None:
        raise PrincipalMappingError(f"principal {logical!r} is not declared")
    ref = refs.get(environment)
    if ref is None:
        raise PrincipalMappingError(f"principal {logical!r} has no mapping for environment {environment!r}")
    if ref.expires is not None and today > ref.expires:
        raise PrincipalMappingError(f"principal {logical!r} mapping for {environment!r} expired on {ref.expires}")
    return ref.name


def _grant_controls(
    resource: str, grants: Iterable[GrantDecl], source: str, resolve: Callable[[str], str]
) -> list[Control]:
    return [
        Control(resource, GRANT, f"{resolve(g.principal)}:{privilege}", "", source)
        for g in grants
        for privilege in g.privileges
    ]


def _merge(controls: list[Control]) -> tuple[Control, ...]:
    seen: dict[str, Control] = {}
    for control in controls:
        prior = seen.get(control.id)
        if prior is not None and prior.value != control.value:
            _fail(f"{control.id}: conflicting values from {prior.source} and {control.source}")
        seen.setdefault(control.id, control)
    return tuple(sorted(seen.values(), key=lambda c: c.id))


def desired_state(
    config: GovernanceConfig,
    *,
    environment: str,
    today: dt.date,
    variables: Mapping[str, str] | None = None,
    hooks: Iterable[Callable[[ResourceDecl], PolicyOutput | None]] = (),
) -> DesiredState:
    """Resolve declarations for ``environment`` into resources and controls (managed resources only)."""
    names = dict(variables or {})
    resources: list[ResolvedResource] = []
    controls: list[Control] = []
    hook_list = tuple(hooks)

    def resolve(logical: str) -> str:
        return resolve_principal(config, logical, environment, today)

    for decl in config.resources:
        try:
            name = decl.object.format(**names)
        except (KeyError, IndexError):
            _fail(f"{decl.object}: unresolved placeholder; pass the variables it needs")
        resources.append(
            ResolvedResource(name, decl.kind, decl.managed, decl.classification, decl.owner, decl.retention)
        )
        if not decl.managed:
            continue
        controls += _resource_controls(config, decl, name, resolve, hook_list)
    return DesiredState(tuple(resources), _merge(controls))


def _resource_controls(
    config: GovernanceConfig,
    decl: ResourceDecl,
    name: str,
    resolve: Callable[[str], str],
    hooks: tuple[Callable[[ResourceDecl], PolicyOutput | None], ...],
) -> list[Control]:
    keys = config.tag_keys
    out = [Control(name, TAG, k, v, "declared") for k, v in decl.tags.items()]
    if decl.classification != UNCLASSIFIED:
        out.append(Control(name, TAG, keys["classification"], decl.classification, "classification"))
    if decl.owner:
        resolve(decl.owner)  # fail clearly when unmapped; the tag stores the logical name
        out.append(Control(name, TAG, keys["owner"], decl.owner, "ownership"))
    if decl.retention:
        out.append(Control(name, TAG, keys["retention"], decl.retention, "retention"))
    if decl.comment is not None:
        out.append(Control(name, COMMENT, "comment", decl.comment, "declared"))
    out += [Control(name, PROPERTY, k, v, "declared") for k, v in decl.properties.items()]
    out += _grant_controls(name, decl.grants, "declared", resolve)
    out += [Control(name, MASK, column, mask, "declared") for column, mask in decl.masks.items()]
    if decl.row_filter:
        out.append(Control(name, ROW_FILTER, "row_filter", decl.row_filter, "declared"))
    outputs = [(f"policy:{r.name}", r.output) for r in config.rules if r.classification == decl.classification]
    for index, hook in enumerate(hooks):
        produced = hook(decl)
        if produced is not None:
            outputs.append((f"hook:{index}", produced))
    for source, output in outputs:
        bad = [k for k in output.tags if not _KEY.fullmatch(k)]
        if bad:
            _fail(f"{source}: invalid tag keys {sorted(bad)}")
        out += [Control(name, TAG, k, v, source) for k, v in output.tags.items()]
        out += _grant_controls(name, output.grants, source, resolve)
    return out
