"""The design front's DTCG source model and portable value conversions.

The source is a DTCG 2025.10 document.  Quenching-specific data lives below
``$extensions.org.quenching``; token values never acquire an invented type just to make an
adapter convenient.  This matters most for DESIGN.md component properties: the external schema
accepts arbitrary strings, while DTCG deliberately has no string token type, so those properties
belong to the extension instead of masquerading as DTCG tokens.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DTCG_SCHEMA = "https://www.designtokens.org/schemas/2025.10/format.json"
QUENCHING_EXTENSION = "org.quenching"
DESIGN_MD_VERSION = "alpha"
PRODUCT_SCHEMA_VERSION = 1
SIDECAR_SCHEMA_VERSION = 2

TOKEN_TYPES = {
    "color", "dimension", "fontFamily", "fontWeight", "duration", "cubicBezier",
    "number", "strokeStyle", "border", "transition", "shadow", "gradient", "typography",
}
FONT_SOURCES = {"local", "webfont", "licensed"}
ASSET_ROLES = {"lockup", "mark", "wordmark", "icon", "illustration"}
ASSET_INKS = {"dark", "light", "any"}
ASSET_ORIENTATIONS = {"horizontal", "vertical", "square", "any"}
CONTRAST_LEVELS = {
    ("AA", "normal"): 4.5,
    ("AA", "large"): 3.0,
    ("AAA", "normal"): 7.0,
    ("AAA", "large"): 4.5,
}
DESIGN_COMPONENT_PROPERTIES = {
    "backgroundColor", "textColor", "typography", "rounded", "padding", "size", "height",
    "width",
}
TOKEN_NAME_RE = re.compile(r"^[^${}.][^{}.]*$")
REFERENCE_RE = re.compile(r"^\{([^{}]+)\}$")
JSON_POINTER_RE = re.compile(r"^#/(.+)$")
DIMENSION_RE = re.compile(r"^(-?(?:\d+(?:\.\d+)?|\.\d+))(px|rem)$")


class DesignError(ValueError):
    """A source or projection cannot be represented without guessing."""


@dataclass(frozen=True)
class Token:
    path: tuple[str, ...]
    type: str
    value: Any
    description: str
    extensions: dict[str, Any]

    @property
    def dotted(self) -> str:
        return ".".join(self.path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DesignError(f"missing required source: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DesignError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DesignError(f"{path} must contain one JSON object")
    return value


def dump_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def quenching_extension(source: dict[str, Any]) -> dict[str, Any]:
    extensions = source.get("$extensions")
    if not isinstance(extensions, dict):
        raise DesignError("tokens.json must declare $extensions.org.quenching")
    extension = extensions.get(QUENCHING_EXTENSION)
    if not isinstance(extension, dict):
        raise DesignError("tokens.json must declare $extensions.org.quenching")
    return extension


def validate_source(source: dict[str, Any]) -> list[dict[str, str]]:
    """Validate the DTCG subset the adapters consume, returning stable findings."""
    findings: list[dict[str, str]] = []

    def add(code: str, message: str, path: str = ".design/tokens.json") -> None:
        findings.append({"severity": "error", "code": code, "message": message, "path": path})

    if source.get("$schema") != DTCG_SCHEMA:
        add("design-dtcg-schema", f"$schema must be {DTCG_SCHEMA}")
    try:
        extension = quenching_extension(source)
    except DesignError as exc:
        add("design-extension-missing", str(exc))
        extension = {}
    for key in ("name", "generatedAt"):
        if not isinstance(extension.get(key), str) or not extension[key].strip():
            add("design-extension-field", f"$extensions.{QUENCHING_EXTENSION}.{key} must be a non-empty string")
    design_md = extension.get("designMd")
    if not isinstance(design_md, dict) or design_md.get("version") != DESIGN_MD_VERSION:
        add("design-external-version", f"designMd.version must be {DESIGN_MD_VERSION!r}")
    if extension.get("productSchema") != PRODUCT_SCHEMA_VERSION:
        add("design-product-version", f"productSchema must be {PRODUCT_SCHEMA_VERSION}")
    try:
        contrast_policy(source)
    except DesignError as exc:
        add("design-contrast-policy", str(exc))
    try:
        non_web_literal_globs(source)
    except DesignError as exc:
        add("design-nonweb-scope", str(exc))

    try:
        records = list(iter_tokens(source))
    except DesignError as exc:
        add("design-dtcg-shape", str(exc))
        records = []
    if not records:
        add("design-token-empty", "tokens.json must declare at least one DTCG token")

    known = {record.dotted for record in records}
    for record in records:
        try:
            validate_token_value(record)
        except DesignError as exc:
            add("design-token-value", str(exc), f".design/tokens.json#{record.dotted}")
        for reference in references_in(record.value):
            if reference not in known:
                add("design-token-reference", f"{record.dotted} references missing token {reference}",
                    f".design/tokens.json#{record.dotted}")
        if record.type == "fontFamily":
            try:
                validate_font_metadata(record)
            except DesignError as exc:
                add("design-font-metadata", str(exc), f".design/tokens.json#{record.dotted}")

    components = design_md.get("components", {}) if isinstance(design_md, dict) else {}
    if components and not isinstance(components, dict):
        add("design-components-shape", "designMd.components must be an object")
    elif isinstance(components, dict):
        for name, properties in components.items():
            if not isinstance(properties, dict):
                add("design-component-shape", f"component {name!r} must be an object")
                continue
            unknown = sorted(set(properties) - DESIGN_COMPONENT_PROPERTIES)
            if unknown:
                add("design-component-property",
                    f"component {name!r} uses unsupported properties: {', '.join(unknown)}")
            for property_name, property_value in properties.items():
                for reference in references_in(property_value):
                    if reference not in known:
                        add(
                            "design-component-reference",
                            f"component {name!r} property {property_name!r} references missing token {reference}",
                        )
    return findings


def iter_tokens(source: dict[str, Any]) -> Iterator[Token]:
    """Yield every DTCG token with its inherited type."""
    def walk(node: dict[str, Any], path: tuple[str, ...], inherited_type: str | None) -> Iterator[Token]:
        node_type = node.get("$type", inherited_type)
        # DTCG groups may carry their default token under the reserved `$root`
        # member.  It is addressed by the group's path (for example
        # `{colors}`), while ordinary children retain their dotted paths.
        root_token = node.get("$root")
        if isinstance(root_token, dict) and ("$value" in root_token or "$ref" in root_token):
            token_type = root_token.get("$type", node_type)
            if token_type not in TOKEN_TYPES:
                raise DesignError(f"{'.'.join(path)} has no supported $type")
            token_value: Any = root_token.get("$value")
            if "$ref" in root_token:
                if "$value" in root_token:
                    raise DesignError(f"{'.'.join(path)} declares both $value and $ref")
                token_value = {"$ref": root_token["$ref"]}
            if path:
                yield Token(
                    path=path,
                    type=token_type,
                    value=token_value,
                    description=str(root_token.get("$description") or ""),
                    extensions=root_token.get("$extensions") if isinstance(root_token.get("$extensions"), dict) else {},
                )
        for key, value in node.items():
            if key.startswith("$"):
                continue
            if not TOKEN_NAME_RE.match(key):
                raise DesignError(f"invalid DTCG token/group name at {'.'.join(path + (key,))}")
            if not isinstance(value, dict):
                raise DesignError(f"{'.'.join(path + (key,))} must be a token or group object")
            child_path = path + (key,)
            is_token = "$value" in value or "$ref" in value
            if is_token:
                if "$value" in value and "$ref" in value:
                    raise DesignError(f"{'.'.join(child_path)} declares both $value and $ref")
                token_type = value.get("$type", node_type)
                if token_type not in TOKEN_TYPES:
                    raise DesignError(f"{'.'.join(child_path)} has no supported $type")
                token_value: Any = value.get("$value")
                if "$ref" in value:
                    token_value = {"$ref": value["$ref"]}
                yield Token(
                    path=child_path,
                    type=token_type,
                    value=token_value,
                    description=str(value.get("$description") or ""),
                    extensions=value.get("$extensions") if isinstance(value.get("$extensions"), dict) else {},
                )
            else:
                yield from walk(value, child_path, value.get("$type", node_type))

    yield from walk(source, (), source.get("$type"))


def validate_token_value(token: Token) -> None:
    value = token.value
    if reference_target(value) is not None:
        return
    if token.type == "color":
        if not isinstance(value, dict) or not isinstance(value.get("colorSpace"), str):
            raise DesignError(f"{token.dotted} must use the DTCG color object")
        components = value.get("components")
        if not isinstance(components, list) or len(components) != 3:
            raise DesignError(f"{token.dotted} color components must contain three values")
    elif token.type == "dimension":
        if (not isinstance(value, dict) or not isinstance(value.get("value"), (int, float))
                or value.get("unit") not in {"px", "rem"}):
            raise DesignError(f"{token.dotted} must use a DTCG dimension object with px or rem")
    elif token.type == "typography":
        required = {"fontFamily", "fontSize", "fontWeight", "letterSpacing", "lineHeight"}
        if not isinstance(value, dict) or not required.issubset(value):
            raise DesignError(f"{token.dotted} typography must declare {', '.join(sorted(required))}")
        family = value.get("fontFamily")
        if (not isinstance(family, (str, list))
                or (isinstance(family, str) and not family.strip())
                or (isinstance(family, list) and not family)):
            raise DesignError(f"{token.dotted}.fontFamily must be a non-empty string or list")
        if isinstance(family, list) and not all(isinstance(item, str) and item.strip() for item in family):
            raise DesignError(f"{token.dotted}.fontFamily list must contain non-empty strings")
    elif token.type == "transition":
        required = {"duration", "delay", "timingFunction"}
        if not isinstance(value, dict) or not required.issubset(value):
            raise DesignError(f"{token.dotted} transition must declare duration, delay, timingFunction")


def references_in(value: Any) -> Iterator[str]:
    target = reference_target(value)
    if target is not None:
        yield target
        return
    if isinstance(value, str):
        return
    elif isinstance(value, list):
        for item in value:
            yield from references_in(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from references_in(item)


def validate_font_metadata(token: Token) -> None:
    """Validate optional metadata that makes a font asset addressable."""
    extension = token.extensions.get(QUENCHING_EXTENSION, {})
    metadata = extension.get("font") if isinstance(extension, dict) else None
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        raise DesignError(f"{token.dotted} font metadata must be an object")
    source = metadata.get("source")
    if source not in FONT_SOURCES:
        raise DesignError(f"{token.dotted} font source must be one of {', '.join(sorted(FONT_SOURCES))}")
    license_name = metadata.get("license")
    if not isinstance(license_name, str) or not license_name.strip():
        raise DesignError(f"{token.dotted} font metadata must declare a non-empty license")
    for key in ("files", "directories"):
        values = metadata.get(key, [])
        if not isinstance(values, list) or not all(isinstance(item, str) and item.strip() for item in values):
            raise DesignError(f"{token.dotted} font metadata {key} must be a list of paths")
        for item in values:
            relative = Path(item)
            if relative.is_absolute() or ".." in relative.parts:
                raise DesignError(f"{token.dotted} font metadata path must stay inside .design/assets: {item}")
    if source == "webfont":
        url = metadata.get("url")
        if not isinstance(url, str) or not re.match(r"^https?://", url):
            raise DesignError(f"{token.dotted} webfont metadata must declare an http(s) url")


def font_metadata(token: Token) -> dict[str, Any] | None:
    extension = token.extensions.get(QUENCHING_EXTENSION, {})
    metadata = extension.get("font") if isinstance(extension, dict) else None
    return metadata if isinstance(metadata, dict) else None


def font_asset_paths(source: dict[str, Any], assets_root: Path) -> dict[str, list[Path]]:
    """Resolve declared font files and directories without leaving the asset root."""
    assets_root = assets_root.resolve()
    result: dict[str, list[Path]] = {}
    for token in token_map(source).values():
        if token.type != "fontFamily":
            continue
        metadata = font_metadata(token)
        if not metadata:
            continue
        paths: list[Path] = []
        for key in ("files", "directories"):
            for item in metadata.get(key, []):
                path = (assets_root / item).resolve()
                try:
                    path.relative_to(assets_root)
                except ValueError as exc:
                    raise DesignError(f"font path escapes .design/assets: {item}") from exc
                paths.append(path)
        result[token.dotted] = sorted(set(paths))
    return result


def contrast_policy(source: dict[str, Any]) -> dict[str, Any]:
    """Return the declared WCAG policy, applying only the documented defaults."""
    extension = quenching_extension(source)
    accessibility = extension.get("accessibility", {})
    raw = accessibility.get("contrast", {}) if isinstance(accessibility, dict) else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise DesignError("$extensions.org.quenching.accessibility.contrast must be an object")
    level = raw.get("level", "AA")
    text_size = raw.get("textSize", "normal")
    if (level, text_size) not in CONTRAST_LEVELS:
        raise DesignError("contrast level must be AA or AAA and textSize must be normal or large")
    threshold = raw.get("threshold", CONTRAST_LEVELS[level, text_size])
    if not isinstance(threshold, (int, float)) or threshold < 1:
        raise DesignError("contrast threshold must be a number greater than or equal to 1")
    return {"level": level, "textSize": text_size, "threshold": float(threshold)}


def contrast_pairs(source: dict[str, Any]) -> list[tuple[Token, Token]]:
    """Enumerate same-group X/on-X color pairs in stable token order."""
    records = token_map(source)
    pairs: list[tuple[Token, Token]] = []
    for token in records.values():
        if token.type != "color" or not token.path or not token.path[-1].startswith("on-"):
            continue
        base_path = token.path[:-1] + (token.path[-1][3:],)
        background = records.get(".".join(base_path))
        if background and background.type == "color":
            pairs.append((token, background))
    return pairs


def color_to_srgb(value: Any) -> tuple[float, float, float]:
    """Return opaque sRGB channels; wide-gamut conversion is intentionally explicit."""
    if not isinstance(value, dict) or value.get("colorSpace") != "srgb":
        raise DesignError("contrast measurement supports only sRGB colors")
    components = value.get("components")
    if not isinstance(components, list) or len(components) != 3:
        raise DesignError("contrast color needs three sRGB components")
    if not all(isinstance(component, (int, float)) for component in components):
        raise DesignError("contrast color components must be numeric")
    return tuple(max(0.0, min(1.0, float(component))) for component in components)


def relative_luminance(value: Any) -> float:
    channels = color_to_srgb(value)
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
              for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: Any, background: Any) -> float:
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def non_web_literal_globs(source: dict[str, Any]) -> list[str]:
    extension = quenching_extension(source)
    doctor = extension.get("doctor", {})
    values = doctor.get("nonWebLiteralGlobs", []) if isinstance(doctor, dict) else []
    if values is None:
        values = []
    if not isinstance(values, list):
        raise DesignError("$extensions.org.quenching.doctor.nonWebLiteralGlobs must be a list")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise DesignError("nonWebLiteralGlobs must contain non-empty strings")
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise DesignError(f"non-web literal glob must stay inside the repository: {value}")
        result.append(value.replace("\\", "/"))
    return sorted(set(result))


def asset_manifest_entries(manifest: dict[str, Any]) -> list[dict[str, str]]:
    """Validate and normalize the optional semantic asset manifest."""
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise DesignError(".design/assets/manifest.json must declare an assets list")
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for entry in assets:
        if not isinstance(entry, dict):
            raise DesignError("asset manifest entries must be objects")
        values = {key: entry.get(key) for key in ("path", "role", "ink", "orientation")}
        if not all(isinstance(value, str) and value.strip() for value in values.values()):
            raise DesignError("asset manifest entries need path, role, ink, and orientation")
        path = Path(values["path"])
        if path.is_absolute() or ".." in path.parts or "" in path.parts:
            raise DesignError(f"asset manifest path must stay inside .design/assets: {values['path']}")
        if values["role"] not in ASSET_ROLES:
            raise DesignError(f"asset role must be one of {', '.join(sorted(ASSET_ROLES))}")
        if values["ink"] not in ASSET_INKS:
            raise DesignError(f"asset ink must be one of {', '.join(sorted(ASSET_INKS))}")
        if values["orientation"] not in ASSET_ORIENTATIONS:
            raise DesignError(f"asset orientation must be one of {', '.join(sorted(ASSET_ORIENTATIONS))}")
        normalized = (path.as_posix(), values["role"], values["ink"], values["orientation"])
        if normalized in seen:
            raise DesignError(f"asset manifest duplicates {values['path']}")
        seen.add(normalized)
        entries.append({"path": normalized[0], "role": normalized[1],
                        "ink": normalized[2], "orientation": normalized[3]})
    return entries


def token_map(source: dict[str, Any]) -> dict[str, Token]:
    return {token.dotted: token for token in iter_tokens(source)}


def resolve_token(token: Token, records: dict[str, Token], trail: tuple[str, ...] = ()) -> Any:
    target = reference_target(token.value)
    if target is None:
        return token.value
    if target in trail or target == token.dotted:
        raise DesignError(f"circular token reference: {' -> '.join(trail + (token.dotted, target))}")
    if target not in records:
        raise DesignError(f"{token.dotted} references missing token {target}")
    return resolve_token(records[target], records, trail + (token.dotted,))


def resolve_value(value: Any, records: dict[str, Token], trail: tuple[str, ...] = ()) -> Any:
    """Resolve token references nested inside composite DTCG values."""
    target = reference_target(value)
    if target is not None:
        if target in trail or target not in records:
            if target not in records:
                raise DesignError(f"references missing token {target}")
            raise DesignError(f"circular token reference: {' -> '.join(trail + (target,))}")
        return resolve_value(records[target].value, records, trail + (target,))
    if isinstance(value, dict):
        return {key: resolve_value(item, records, trail) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_value(item, records, trail) for item in value]
    return value


def reference_target(value: Any) -> str | None:
    """Return a dotted token path for either DTCG 2025.10 reference form."""
    if isinstance(value, str):
        match = REFERENCE_RE.match(value)
        return match.group(1) if match else None
    if not isinstance(value, dict) or set(value) != {"$ref"}:
        return None
    pointer = value["$ref"]
    if not isinstance(pointer, str):
        raise DesignError("a DTCG $ref must be a JSON Pointer string")
    match = JSON_POINTER_RE.match(pointer)
    if not match:
        raise DesignError(f"invalid DTCG JSON Pointer reference: {pointer!r}")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in match.group(1).split("/")]
    if parts and parts[-1] in {"$value", "$root"}:
        parts.pop()
    if not parts or any(not part or "." in part for part in parts):
        raise DesignError(f"JSON Pointer does not identify one token: {pointer!r}")
    return ".".join(parts)


def _number(value: float) -> str:
    if math.isfinite(value) and value == int(value):
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def dimension_to_css(value: Any) -> str:
    if isinstance(value, str) and REFERENCE_RE.match(value):
        return value
    if not isinstance(value, dict) or value.get("unit") not in {"px", "rem"}:
        raise DesignError(f"cannot project {value!r} as a CSS dimension")
    amount = value.get("value")
    if not isinstance(amount, (int, float)):
        raise DesignError(f"cannot project {value!r} as a CSS dimension")
    return f"{_number(float(amount))}{value['unit']}"


def color_to_css(value: Any) -> str:
    if isinstance(value, str) and REFERENCE_RE.match(value):
        return value
    if not isinstance(value, dict):
        raise DesignError(f"cannot project {value!r} as a CSS color")
    space = value.get("colorSpace")
    components = value.get("components")
    alpha = value.get("alpha", 1)
    if not isinstance(components, list) or len(components) != 3:
        raise DesignError(f"cannot project {value!r} as a CSS color")
    if value.get("hex") and alpha == 1:
        return str(value["hex"]).upper()
    if not all(isinstance(component, (int, float)) for component in components):
        raise DesignError("property-level color references cannot be represented in DESIGN.md")
    c = [float(component) for component in components]
    alpha_suffix = "" if alpha == 1 else f" / {_number(float(alpha))}"
    if space == "srgb":
        rgb = [max(0, min(255, round(component * 255))) for component in c]
        if alpha == 1:
            return "#" + "".join(f"{component:02X}" for component in rgb)
        return f"rgb({rgb[0]} {rgb[1]} {rgb[2]}{alpha_suffix})"
    if space in {"hsl", "hwb"}:
        return f"{space}({_number(c[0])} {_number(c[1])}% {_number(c[2])}%{alpha_suffix})"
    if space in {"lab", "lch"}:
        return f"{space}({_number(c[0])}% {_number(c[1])} {_number(c[2])}{alpha_suffix})"
    if space in {"oklab", "oklch"}:
        first = _number(c[0] * 100) + "%"
        return f"{space}({first} {_number(c[1])} {_number(c[2])}{alpha_suffix})"
    if space in {"display-p3", "a98-rgb", "prophoto-rgb", "rec2020", "srgb-linear"}:
        return f"color({space} {' '.join(_number(part) for part in c)}{alpha_suffix})"
    if space in {"xyz-d50", "xyz-d65"}:
        return f"color({space} {' '.join(_number(part) for part in c)}{alpha_suffix})"
    raise DesignError(f"unsupported DTCG color space: {space!r}")


def typography_to_design(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DesignError(f"cannot project {value!r} as typography")
    result: dict[str, Any] = {}
    for key in ("fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing"):
        item = value.get(key)
        if key == "fontFamily" and isinstance(item, list):
            result[key] = ", ".join(str(font) for font in item)
        elif key in {"fontSize", "letterSpacing"} and isinstance(item, dict):
            result[key] = dimension_to_css(item)
        else:
            result[key] = item
    return result


def value_to_css(token: Token, records: dict[str, Token], preserve_reference: bool = False) -> str:
    target = reference_target(token.value)
    if preserve_reference and target is not None:
        return "{" + target + "}"
    value = resolve_token(token, records)
    if token.type == "color":
        return color_to_css(value)
    if token.type == "dimension":
        return dimension_to_css(value)
    if token.type == "duration":
        if isinstance(value, dict) and isinstance(value.get("value"), (int, float)) and value.get("unit") in {"ms", "s"}:
            return f"{_number(float(value['value']))}{value['unit']}"
    if token.type == "cubicBezier" and isinstance(value, list) and len(value) == 4:
        return f"cubic-bezier({', '.join(_number(float(part)) for part in value)})"
    if token.type == "number" and isinstance(value, (int, float)):
        return _number(float(value))
    if token.type == "fontFamily":
        return ", ".join(value) if isinstance(value, list) else str(value)
    if token.type == "fontWeight":
        return str(value)
    if token.type == "transition" and isinstance(value, dict):
        duration = _duration_to_css(value.get("duration"))
        delay = _duration_to_css(value.get("delay"))
        curve = value.get("timingFunction")
        curve_css = (f"cubic-bezier({', '.join(_number(float(part)) for part in curve)})"
                     if isinstance(curve, list) and len(curve) == 4 else str(curve))
        return f"{duration} {curve_css} {delay}"
    if token.type == "shadow":
        return shadow_to_css(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _duration_to_css(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("value"), (int, float)):
        return f"{_number(float(value['value']))}{value.get('unit', 'ms')}"
    return str(value)


def shadow_to_css(value: Any) -> str:
    shadows = value if isinstance(value, list) else [value]
    rendered = []
    for shadow in shadows:
        if not isinstance(shadow, dict):
            raise DesignError(f"cannot project {value!r} as a shadow")
        parts = [dimension_to_css(shadow.get(key)) for key in ("offsetX", "offsetY", "blur", "spread")]
        color = color_to_css(shadow.get("color"))
        rendered.append(("inset " if shadow.get("inset") else "") + " ".join(parts + [color]))
    return ", ".join(rendered)


def css_color_to_dtcg(value: str) -> dict[str, Any]:
    text = value.strip()
    match = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", text)
    if match:
        raw = match.group(1)
        if len(raw) == 3:
            raw = "".join(ch * 2 for ch in raw)
        alpha = int(raw[6:8], 16) / 255 if len(raw) == 8 else 1
        rgb = [int(raw[index:index + 2], 16) / 255 for index in (0, 2, 4)]
        result: dict[str, Any] = {"colorSpace": "srgb", "components": rgb}
        if alpha != 1:
            result["alpha"] = round(alpha, 6)
        else:
            result["hex"] = "#" + raw[:6].upper()
        return result
    match = re.fullmatch(r"oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)(?:\s*/\s*([\d.]+))?\s*\)", text, re.IGNORECASE)
    if match:
        result = {"colorSpace": "oklch",
                  "components": [float(match.group(1)) / 100, float(match.group(2)), float(match.group(3))]}
        if match.group(4) is not None:
            result["alpha"] = float(match.group(4))
        return result
    raise DesignError(f"color {value!r} cannot be imported losslessly into DTCG 2025.10")


def css_dimension_to_dtcg(value: str) -> dict[str, Any]:
    if value.strip() in {"0", "+0", "-0"}:
        return {"value": 0, "unit": "px"}
    match = DIMENSION_RE.fullmatch(value.strip())
    if not match:
        raise DesignError(f"dimension {value!r} cannot be imported losslessly into DTCG 2025.10")
    return {"value": float(match.group(1)), "unit": match.group(2)}
