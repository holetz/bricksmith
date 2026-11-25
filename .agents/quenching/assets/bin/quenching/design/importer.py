"""Fold the portable DESIGN.md subset back into the DTCG source."""
from __future__ import annotations

import copy
import datetime as dt
from pathlib import Path
from typing import Any

from quenching.common.io import write_text
from quenching.design.markdown import parse_frontmatter
from quenching.design.model import (
    DESIGN_COMPONENT_PROPERTIES,
    QUENCHING_EXTENSION,
    DesignError,
    css_color_to_dtcg,
    css_dimension_to_dtcg,
    dump_json,
    quenching_extension,
    read_json,
    token_map,
    validate_source,
)


def import_design(root: Path, write: bool = True) -> dict[str, Any]:
    root = root.resolve()
    source_path = root / ".design" / "tokens.json"
    design_path = root / "DESIGN.md"
    source = read_json(source_path)
    try:
        frontmatter, _ = parse_frontmatter(design_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DesignError("DESIGN.md is missing; there is nothing to import") from exc
    except (OSError, ValueError) as exc:
        raise DesignError(f"cannot parse DESIGN.md: {exc}") from exc
    candidate = copy.deepcopy(source)
    changed: list[str] = []
    retained: list[str] = []
    skipped: list[str] = []
    extension = quenching_extension(candidate)
    for field in ("name", "description"):
        value = frontmatter.get(field)
        if isinstance(value, str) and value and extension.get(field) != value:
            extension[field] = value
            changed.append(field)

    existing = token_map(candidate)
    for group in ("colors", "typography", "rounded", "spacing"):
        values = frontmatter.get(group)
        if values is None:
            continue
        if not isinstance(values, dict):
            raise DesignError(f"DESIGN.md {group} must be an object")
        external_names = set(values)
        group_existing = {"-".join(token.path[1:]): token for token in existing.values()
                          if token.path[0] == group}
        retained.extend(f"{group}.{name}" for name in sorted(set(group_existing) - external_names))
        for name, value in values.items():
            token = group_existing.get(name)
            try:
                dtcg_value, token_extensions = _import_value(group, value, token)
            except DesignError:
                # DESIGN.md is intentionally lossy.  A valid Impeccable role
                # such as a clamp() display size or its non-token type scale
                # must not make an otherwise useful import fail; retain the
                # richer DTCG value and report the omitted portable field.
                skipped.append(f"{group}.{name}")
                continue
            if token:
                target = _node_at(candidate, token.path)
                if target.get("$value") != dtcg_value:
                    target["$value"] = dtcg_value
                    changed.append(f"{group}.{name}")
                if token_extensions:
                    extensions = target.setdefault("$extensions", {})
                    qext = extensions.setdefault(QUENCHING_EXTENSION, {})
                    qext["impeccable"] = token_extensions
            else:
                group_node = candidate.setdefault(group, {"$type": _group_type(group)})
                if not isinstance(group_node, dict):
                    raise DesignError(f"tokens.json {group} is not a DTCG group")
                group_node[name] = {"$value": dtcg_value}
                if token_extensions:
                    group_node[name]["$extensions"] = {
                        QUENCHING_EXTENSION: {"impeccable": token_extensions}}
                changed.append(f"{group}.{name}")

    components = frontmatter.get("components")
    if components is not None:
        if not isinstance(components, dict):
            raise DesignError("DESIGN.md components must be an object")
        for name, properties in components.items():
            if not isinstance(properties, dict):
                raise DesignError(f"DESIGN.md component {name!r} must be an object")
        design_md = extension.setdefault("designMd", {})
        portable_components = {}
        existing_components = design_md.get("components") if isinstance(design_md.get("components"), dict) else {}
        for name, properties in components.items():
            portable = {key: value for key, value in properties.items()
                        if key in DESIGN_COMPONENT_PROPERTIES}
            omitted = sorted(set(properties) - set(portable))
            skipped.extend(f"components.{name}.{key}" for key in omitted)
            if portable:
                portable_components[name] = portable
            elif name in existing_components:
                portable_components[name] = existing_components[name]
        if design_md.get("components") != portable_components:
            design_md["components"] = portable_components
            changed.append("components")

    if changed:
        extension["generatedAt"] = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    findings = validate_source(candidate)
    if findings:
        raise DesignError("import would make tokens.json invalid: "
                          + "; ".join(item["message"] for item in findings))
    if write and changed:
        write_text(str(source_path), dump_json(candidate))
    return {
        "ok": True,
        "mode": "write" if write else "check",
        "changed": sorted(set(changed)),
        "retained": sorted(set(retained)),
        "skipped": sorted(set(skipped)),
        "lossy": True,
        "message": ("portable groups were folded into tokens.json; unmentioned or unrepresentable values were retained"
                    if changed or skipped else "DESIGN.md already agrees with the portable source subset"),
    }


def _import_value(group: str, value: Any, token: Any) -> tuple[Any, dict[str, Any]]:
    if group == "colors":
        if not isinstance(value, str):
            raise DesignError("DESIGN.md color values must be strings")
        if value.startswith("{") and value.endswith("}"):
            return value, {}
        return css_color_to_dtcg(value), {}
    if group in {"rounded", "spacing"}:
        if not isinstance(value, str):
            raise DesignError(f"DESIGN.md {group} values must carry px or rem units")
        if value.startswith("{") and value.endswith("}"):
            return value, {}
        return css_dimension_to_dtcg(value), {}
    if not isinstance(value, dict):
        raise DesignError("DESIGN.md typography values must be objects")
    current = copy.deepcopy(token.value) if token and isinstance(token.value, dict) else {}
    merged = {**current, **value}
    required = {"fontFamily", "fontSize", "fontWeight", "letterSpacing", "lineHeight"}
    missing = sorted(required - set(merged))
    if missing:
        raise DesignError(f"DESIGN.md typography is missing DTCG-required properties: {', '.join(missing)}")
    line_height = merged["lineHeight"]
    if not isinstance(line_height, (int, float)):
        raise DesignError("DTCG 2025.10 requires unitless typography lineHeight")
    typography = {
        "fontFamily": merged["fontFamily"],
        "fontSize": (merged["fontSize"] if isinstance(merged["fontSize"], dict)
                     else css_dimension_to_dtcg(str(merged["fontSize"]))),
        "fontWeight": merged["fontWeight"],
        "letterSpacing": (merged["letterSpacing"] if isinstance(merged["letterSpacing"], dict)
                           else css_dimension_to_dtcg(str(merged["letterSpacing"]))),
        "lineHeight": line_height,
    }
    metadata = {key: value[key] for key in ("fontFeature", "fontVariation") if key in value}
    return typography, metadata


def _node_at(source: dict[str, Any], path: tuple[str, ...]) -> dict[str, Any]:
    node: Any = source
    for part in path:
        node = node[part]
    if not isinstance(node, dict):
        raise DesignError(f"{'.'.join(path)} is not a token object")
    return node


def _group_type(group: str) -> str:
    return {"colors": "color", "typography": "typography",
            "rounded": "dimension", "spacing": "dimension"}[group]
