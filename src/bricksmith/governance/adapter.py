"""Reads live governance state and applies statements through an injectable SQL runner."""
# ruff: noqa: S608 - SQL is built from quoted identifiers and escaped literals only

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from bricksmith.governance.model import LiveResource, ResolvedResource
from bricksmith.storage.sql import literal

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from bricksmith.governance.planner import GovernancePlan

_URL = re.compile(r"https?://\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_MAX = 300


def redact(text: str) -> str:
    """Strip hosts, URLs and identities from a platform message and bound its length."""
    cleaned = _EMAIL.sub("<identity>", _URL.sub("<url>", " ".join(text.split())))
    return cleaned[:_MAX]


def spark_runner(spark: Any) -> Callable[[str], Sequence[Mapping[str, Any]]]:  # noqa: ANN401 - Spark session
    """Wrap a Spark session as a ``SqlRunner`` returning dict rows."""
    return lambda sql: [row.asDict() for row in spark.sql(sql).collect()]


def _parts(name: str, kind: str) -> list[str]:
    parts = name.split(".")
    expected = {"table": 3, "view": 3, "schema": 2, "catalog": 1}[kind]
    if len(parts) != expected:
        msg = f"{name!r} must have {expected} dot-separated parts for a {kind}"
        raise ValueError(msg)
    return parts


def _field(row: Mapping[str, Any], name: str) -> Any:  # noqa: ANN401
    for key, value in row.items():
        if key.lower() == name:
            return value
    return None


def _queries(resource: ResolvedResource) -> dict[str, str]:
    parts = _parts(resource.name, resource.kind)
    catalog = f"`{parts[0]}`.information_schema"
    if resource.kind in ("table", "view"):
        where = f"WHERE {{c}}_schema = {literal(parts[1])} AND table_name = {literal(parts[2])}"
        tag_where = f"WHERE schema_name = {literal(parts[1])} AND table_name = {literal(parts[2])}"
        masks_where = f"WHERE table_schema = {literal(parts[1])} AND table_name = {literal(parts[2])}"
        return {
            "exists": f"SELECT comment FROM {catalog}.tables " + where.format(c="table"),
            "tags": f"SELECT tag_name, tag_value FROM {catalog}.table_tags {tag_where}",
            "masks": f"SELECT column_name AS name FROM {catalog}.column_masks {masks_where}",
            "row_filters": f"SELECT table_name AS name FROM {catalog}.row_filters {masks_where}",
        }
    if resource.kind == "schema":
        w = f"WHERE schema_name = {literal(parts[1])}"
        return {
            "exists": f"SELECT comment FROM {catalog}.schemata {w}",
            "tags": f"SELECT tag_name, tag_value FROM {catalog}.schema_tags {w}",
        }
    w = f"WHERE catalog_name = {literal(parts[0])}"
    return {
        "exists": f"SELECT comment FROM {catalog}.catalogs {w}",
        "tags": f"SELECT tag_name, tag_value FROM {catalog}.catalog_tags {w}",
    }


def _read_grants(
    run: Callable[[str], Sequence[Mapping[str, Any]]], resource: ResolvedResource
) -> frozenset[tuple[str, str]]:
    keyword = resource.kind.upper()
    rows = run(f"SHOW GRANTS ON {keyword} " + ".".join(f"`{p}`" for p in resource.name.split(".")))
    return frozenset(
        (str(_field(r, "principal")), str(_field(r, "actiontype")))
        for r in rows
        if str(_field(r, "objecttype") or keyword).upper().replace("_", " ")
        in (keyword, "VIEW" if keyword == "TABLE" else keyword)
    )


def read_resource(run: Callable[[str], Sequence[Mapping[str, Any]]], resource: ResolvedResource) -> LiveResource:
    """Read one resource; a section that cannot be read is recorded as unreadable, never guessed."""
    queries = _queries(resource)
    try:
        found = run(queries["exists"])
    except Exception as exc:  # noqa: BLE001 - any platform refusal becomes an explicit unreadable state
        return LiveResource(exists=False, unreadable={"exists": redact(str(exc))})
    if not found:
        return LiveResource(exists=False)
    unreadable: dict[str, str] = {}
    tags: dict[str, str] = {}
    properties: dict[str, str] = {}
    masks: tuple[str, ...] = ()
    row_filters: tuple[str, ...] = ()
    grants: frozenset[tuple[str, str]] = frozenset()

    def attempt(section: str, action: Callable[[], Any]) -> Any:  # noqa: ANN401
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            unreadable[section] = redact(str(exc))
            return None

    got = attempt(
        "tags", lambda: {str(_field(r, "tag_name")): str(_field(r, "tag_value")) for r in run(queries["tags"])}
    )
    tags = got or tags
    got = attempt("grants", lambda: _read_grants(run, resource))
    grants = got if got is not None else grants
    if resource.kind in ("table", "view"):
        name = ".".join(f"`{p}`" for p in resource.name.split("."))
        got = attempt(
            "properties",
            lambda: {str(_field(r, "key")): str(_field(r, "value")) for r in run(f"SHOW TBLPROPERTIES {name}")},
        )
        properties = got or properties
        got = attempt("masks", lambda: tuple(sorted(str(_field(r, "name")) for r in run(queries["masks"]))))
        masks = got or masks
        got = attempt("row_filters", lambda: tuple(sorted(str(_field(r, "name")) for r in run(queries["row_filters"]))))
        row_filters = got or row_filters
    comment = _field(found[0], "comment")
    return LiveResource(
        exists=True,
        tags=tags,
        grants=grants,
        comment=None if comment is None else str(comment),
        properties=properties,
        masks=masks,
        row_filters=row_filters,
        unreadable=unreadable,
    )


def read_live(
    run: Callable[[str], Sequence[Mapping[str, Any]]], resources: Iterable[ResolvedResource]
) -> dict[str, LiveResource]:
    """Read every managed resource's live state."""
    return {r.name: read_resource(run, r) for r in resources if r.managed}


@dataclass(frozen=True)
class ApplyResult:
    """Which operations ran and which the platform refused (redacted reasons)."""

    applied: tuple[str, ...] = ()
    refused: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON form."""
        return {"applied": list(self.applied), "refused": dict(sorted(self.refused.items()))}


def apply_plan(run: Callable[[str], Sequence[Mapping[str, Any]]], plan: GovernancePlan) -> ApplyResult:
    """Run each planned statement once, in order; a refusal is recorded and does not stop the others."""
    applied: list[str] = []
    refused: dict[str, str] = {}
    for op in plan.operations:
        try:
            run(op.sql)
        except Exception as exc:  # noqa: BLE001 - recorded as an explicit refusal
            refused[op.control_id] = redact(str(exc))
        else:
            applied.append(op.control_id)
    return ApplyResult(tuple(applied), refused)
