"""Deterministic projections from the design source and the OKF product/design prose."""
from __future__ import annotations

import json
import pprint
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quenching.common.io import write_text
from quenching.design.markdown import parse_document_frontmatter, read_sections, split_h2, yaml_map
from quenching.design.model import (
    DESIGN_MD_VERSION,
    PRODUCT_SCHEMA_VERSION,
    QUENCHING_EXTENSION,
    SIDECAR_SCHEMA_VERSION,
    DesignError,
    Token,
    color_to_css,
    dimension_to_css,
    dump_json,
    quenching_extension,
    read_json,
    reference_target,
    resolve_token,
    resolve_value,
    token_map,
    typography_to_design,
    validate_source,
    value_to_css,
)

PRODUCT_SECTIONS = (
    "Platform", "Stack", "Users", "Product Purpose", "Positioning", "Operating Context",
    "Capabilities and Constraints", "Brand Commitments", "Evidence on Hand",
    "Product Principles", "Accessibility & Inclusion",
)
PRODUCT_HOMES = {
    "Platform": ("standards/platform", "vision"),
    "Stack": ("standards/platform", "standards/architecture"),
    "Users": ("vision",),
    "Product Purpose": ("vision",),
    "Positioning": ("vision",),
    "Operating Context": ("vision", "standards/workflows"),
    "Capabilities and Constraints": ("standards", "catalog"),
    "Brand Commitments": ("standards/design",),
    "Evidence on Hand": ("catalog", "external"),
    "Product Principles": ("vision",),
    "Accessibility & Inclusion": ("standards/quality",),
}
DESIGN_SECTIONS = (
    "Overview", "Colors", "Typography", "Layout", "Elevation & Depth", "Shapes",
    "Components", "Do's and Don'ts",
)
GENERATED_PATHS = (
    "PRODUCT.md",
    "DESIGN.md",
    "MEDIUM.md",
    ".impeccable/design.json",
    ".design/build/tokens.css",
    ".design/build/tokens.typ",
    ".design/build/tokens.py",
    ".design/build/brand-api.md",
)


@dataclass(frozen=True)
class BuildResult:
    root: Path
    outputs: dict[str, str]
    sources: tuple[str, ...]


def compute_build(root: Path) -> BuildResult:
    root = root.resolve()
    token_path = root / ".design" / "tokens.json"
    source = read_json(token_path)
    findings = validate_source(source)
    if findings:
        raise DesignError("; ".join(finding["message"] for finding in findings))
    extension = quenching_extension(source)
    records = token_map(source)
    knowledge_sources = _knowledge_sources(root)
    brand_path = root / "docs" / "standards" / "design" / "brand.md"
    production_path = root / "docs" / "standards" / "design" / "production.md"
    primitives_path = root / ".design" / "media" / "html" / "primitives.json"
    genres = sorted((root / ".design" / "genres").glob("*.md"))

    design_sections = _design_sections(brand_path, production_path, source, records)
    outputs = {
        "PRODUCT.md": _render_product(root, knowledge_sources),
        "DESIGN.md": _render_design(source, records, design_sections, root),
        "MEDIUM.md": _render_medium(genres, root),
        ".impeccable/design.json": _render_sidecar(
            source, records, design_sections, primitives_path, root),
        ".design/build/tokens.css": _render_css(records, root),
        ".design/build/tokens.typ": _render_typst(records, root),
        ".design/build/tokens.py": _render_python(records, root),
        ".design/build/brand-api.md": _render_brand_api(records, root),
    }
    sources = [token_path, brand_path, production_path, primitives_path, *knowledge_sources, *genres]
    return BuildResult(
        root=root,
        outputs=outputs,
        sources=tuple(sorted({_relative(path, root) for path in sources if path.exists()})),
    )


def write_build(result: BuildResult) -> list[str]:
    written: list[str] = []
    for relative, content in result.outputs.items():
        path = result.root / relative
        try:
            current = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            current = None
        if current == content:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        write_text(str(path), content)
        written.append(relative)
    return written


def build_drift(result: BuildResult) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for relative, expected in result.outputs.items():
        path = result.root / relative
        try:
            actual = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            findings.append({
                "severity": "error", "code": "design-generated-missing", "path": relative,
                "message": "generated projection is missing; run `cq design build`",
            })
            continue
        if actual != expected:
            findings.append({
                "severity": "error", "code": "design-generated-drift", "path": relative,
                "message": "generated projection differs byte-for-byte; choose build (source wins) or import (DESIGN.md wins)",
            })
    return findings


def _knowledge_sources(root: Path) -> list[Path]:
    bundle = root / "docs"
    if not bundle.is_dir():
        raise DesignError("PRODUCT.md projection requires an installed /docs/ bundle")
    paths: set[Path] = set()
    for homes in PRODUCT_HOMES.values():
        for home in homes:
            base = bundle / home
            if base.is_file():
                paths.add(base)
            elif base.is_dir():
                paths.update(path for path in base.rglob("*.md") if path.name != "index.md")
    return sorted(paths)


def _render_product(root: Path, sources: list[Path]) -> str:
    collected: dict[str, list[str]] = {section: [] for section in PRODUCT_SECTIONS}
    bundle = root / "docs"
    confirmed_record = (bundle / "vision" / "product.md").resolve()
    for section in PRODUCT_SECTIONS:
        allowed = tuple((bundle / home).resolve() for home in PRODUCT_HOMES[section])
        for path in sources:
            resolved = path.resolve()
            # The aligner may persist a complete, confirmed interview in one
            # vision record.  That record is authoritative for every product
            # heading even when a heading's usual OKF home is elsewhere.
            if resolved != confirmed_record and not any(
                resolved == base or base in resolved.parents for base in allowed
            ):
                continue
            try:
                body = split_h2(path.read_text(encoding="utf-8")).get(section, "").strip()
            except OSError:
                continue
            if body:
                collected[section].append(body)
    platform = "\n\n".join(collected["Platform"]).strip()
    if platform not in {"web", "ios", "android", "adaptive"}:
        raise DesignError(
            "PRODUCT.md needs one pure `## Platform` value (web, ios, android, or adaptive) "
            "under /docs/standards/platform/ or /docs/vision/"
        )
    notice = _generated_notice(root, "knowledge")
    lines = ["# Product", "", f"<!-- impeccable:product-schema {PRODUCT_SCHEMA_VERSION} -->", "",
             f"<!-- {notice} -->"]
    for section in PRODUCT_SECTIONS:
        bodies = collected[section]
        if not bodies:
            continue
        lines.extend(["", f"## {section}", "", "\n\n".join(bodies).strip()])
    return "\n".join(lines).rstrip() + "\n"


def _design_sections(brand_path: Path, production_path: Path, source: dict[str, Any],
                     records: dict[str, Token]) -> dict[str, str]:
    sections = read_sections([brand_path, production_path])
    rendered: dict[str, str] = {}
    aliases = {"Elevation & Depth": ("Elevation & Depth", "Elevation")}
    for heading in DESIGN_SECTIONS:
        candidates = aliases.get(heading, (heading,))
        bodies = [body for candidate in candidates for body in sections.get(candidate, [])]
        if bodies:
            rendered[heading] = "\n\n".join(bodies).strip()
    for heading, group in (
        ("Colors", "colors"), ("Typography", "typography"), ("Layout", "spacing"),
        ("Shapes", "rounded"), ("Elevation & Depth", "shadows"),
    ):
        if heading in rendered:
            continue
        descriptions = [f"- **{'-'.join(token.path[1:])}:** {token.description}"
                        for token in records.values() if token.path[0] == group and token.description]
        if descriptions:
            rendered[heading] = "\n".join(descriptions)
    extension = quenching_extension(source)
    components = extension.get("designMd", {}).get("components", {})
    if isinstance(components, dict) and components:
        component_details = []
        for name, properties in components.items():
            component_details.append(f"### {name}")
            if isinstance(properties, dict):
                for prop, value in properties.items():
                    component_details.append(f"- **{prop}:** {value}")
            component_details.append("")
        generated_components = "\n".join(component_details).rstrip()
        rendered["Components"] = (
            f"{rendered['Components']}\n\n{generated_components}"
            if rendered.get("Components") else generated_components
        )
    if "Overview" not in rendered:
        rendered["Overview"] = str(extension.get("description") or extension["name"])
    return rendered


def _render_design(source: dict[str, Any], records: dict[str, Token], sections: dict[str, str],
                   root: Path) -> str:
    extension = quenching_extension(source)
    frontmatter: dict[str, Any] = {
        "version": DESIGN_MD_VERSION,
        "name": extension["name"],
    }
    if extension.get("description"):
        frontmatter["description"] = extension["description"]
    for group in ("colors", "typography", "rounded", "spacing"):
        values = _design_group(group, records)
        if values:
            frontmatter[group] = values
    components = extension.get("designMd", {}).get("components")
    if isinstance(components, dict) and components:
        frontmatter["components"] = components

    lines = ["---", f"# {_generated_notice(root, 'tokens')}",
             "# Run `/impeccable document` to propose changes, then `cq design import` to fold them into the source."]
    lines.extend(yaml_map(frontmatter))
    lines.append("---")
    # Keep the human-facing title from Impeccable's canonical DESIGN.md shape.  The
    # frontmatter is the machine contract; this H1 makes the generated document
    # immediately legible when opened outside the parser.
    lines.extend(["", f"# Design System: {extension['name']}"])
    for heading in DESIGN_SECTIONS:
        body = sections.get(heading)
        if body:
            lines.extend(["", f"## {heading}", "", body])
    return "\n".join(lines).rstrip() + "\n"


def _design_group(group: str, records: dict[str, Token]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    for token in records.values():
        if not token.path or token.path[0] != group:
            continue
        name = _token_name(token)
        value = resolve_value(token.value, records, (token.dotted,))
        if group == "colors":
            projected[name] = color_to_css(value)
        elif group == "typography":
            projected[name] = typography_to_design(value)
            impeccable = token.extensions.get(QUENCHING_EXTENSION, {}).get("impeccable", {})
            if isinstance(impeccable, dict):
                for prop in ("fontFeature", "fontVariation"):
                    if prop in impeccable:
                        projected[name][prop] = impeccable[prop]
        else:
            projected[name] = dimension_to_css(value)
    return projected


def _render_css(records: dict[str, Token], root: Path) -> str:
    lines = [f"/* {_generated_notice(root, 'tokens')} */", ":root {"]
    for token in records.values():
        name = "-".join(_slug(part) for part in token.path)
        if token.type == "typography":
            value = resolve_value(token.value, records, (token.dotted,))
            for prop, item in typography_to_design(value).items():
                lines.append(f"  --design-{name}-{_slug(prop)}: {item};")
            continue
        try:
            value = value_to_css(token, records, preserve_reference=True)
        except DesignError:
            continue
        match = re.fullmatch(r"\{([^{}]+)\}", value)
        if match:
            value = f"var(--design-{'-'.join(_slug(part) for part in match.group(1).split('.'))})"
        lines.append(f"  --design-{name}: {value};")
    lines.extend(["}", ""])
    return "\n".join(lines)


def _render_typst(records: dict[str, Token], root: Path) -> str:
    lines = [f"// {_generated_notice(root, 'tokens')}"]
    for token in records.values():
        name = "-".join(_slug(part) for part in token.path)
        if token.type == "typography":
            value = resolve_value(token.value, records, (token.dotted,))
            if not isinstance(value, dict):
                raise DesignError(f"{token.dotted} cannot be projected as Typst typography")
            for prop in ("fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing"):
                item = value.get(prop)
                if prop in {"fontSize", "letterSpacing"}:
                    item = _dimension_to_typst(item)
                elif prop == "fontFamily" and isinstance(item, list):
                    generic_families = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui"}
                    families = [str(font) for font in item if str(font).lower() not in generic_families]
                    if len(families) == 1:
                        item = json.dumps(families[0], ensure_ascii=False)
                    else:
                        item = "(" + ", ".join(json.dumps(font, ensure_ascii=False) for font in families) + ")"
                else:
                    item = json.dumps(item, ensure_ascii=False)
                lines.append(f"#let token-{name}-{_slug(prop)} = {item}")
            continue
        reference = reference_target(token.value)
        if reference:
            value = "token-" + "-".join(_slug(part) for part in reference.split("."))
        else:
            resolved = resolve_token(token, records)
            if token.type == "color":
                value = _color_to_typst(resolved)
            elif token.type == "dimension":
                value = _dimension_to_typst(resolved)
            elif token.type == "number" and isinstance(resolved, (int, float)):
                value = str(resolved)
            else:
                try:
                    value = json.dumps(value_to_css(token, records), ensure_ascii=False)
                except DesignError:
                    value = json.dumps(resolved, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"#let token-{name} = {value}")
    return "\n".join(lines).rstrip() + "\n"


def _render_python(records: dict[str, Token], root: Path) -> str:
    values = {token.dotted: resolve_token(token, records) for token in records.values()}
    return (f'"""{_generated_notice(root, "tokens")}"""\n\n'
             "TOKENS = " + pprint.pformat(values, sort_dicts=True, width=100) + "\n")


def _render_brand_api(records: dict[str, Token], root: Path) -> str:
    lines = ["# Brand API", "", f"<!-- {_generated_notice(root, 'tokens')} -->", "",
             "| Token | Type | Portable value | Description |", "| --- | --- | --- | --- |"]
    for token in records.values():
        try:
            value = value_to_css(token, records)
        except DesignError:
            value = json.dumps(resolve_token(token, records), ensure_ascii=False, separators=(",", ":"))
        safe_value = str(value).replace("|", "\\|").replace("\n", " ")
        description = token.description.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{token.dotted}` | `{token.type}` | `{safe_value}` | {description} |")
    return "\n".join(lines).rstrip() + "\n"


def _render_sidecar(source: dict[str, Any], records: dict[str, Token], sections: dict[str, str],
                    primitives_path: Path, root: Path) -> str:
    extension = quenching_extension(source)
    color_meta: dict[str, Any] = {}
    typography_meta: dict[str, Any] = {}
    for token in records.values():
        meta = token.extensions.get(QUENCHING_EXTENSION, {}).get("impeccable", {})
        name = _token_name(token)
        if token.path[0] == "colors":
            color_meta[name] = dict(meta) if isinstance(meta, dict) else {}
            try:
                canonical = color_to_css(resolve_token(token, records))
            except DesignError:
                canonical = None
            if canonical:
                color_meta[name].setdefault("canonical", canonical)
                color_meta[name].setdefault("displayName", name.replace("-", " ").title())
                color_meta[name].setdefault("tonalRamp", _tonal_ramp(canonical))
        elif token.path[0] == "typography":
            typography_meta[name] = dict(meta) if isinstance(meta, dict) else {}
            typography_meta[name].setdefault("displayName", name.replace("-", " ").title())
            if token.description:
                typography_meta[name].setdefault("purpose", token.description)
    payload = {
        "schemaVersion": SIDECAR_SCHEMA_VERSION,
        "generatedAt": extension["generatedAt"],
        "title": f"Design System: {extension['name']}",
        "extensions": {
            "colorMeta": color_meta,
            "typographyMeta": typography_meta,
            "shadows": _sidecar_token_list("shadows", records),
            "motion": _sidecar_token_list("motion", records),
            "breakpoints": _sidecar_token_list("breakpoints", records),
        },
        "components": _read_primitives(primitives_path, records),
        "narrative": _narrative(sections),
    }
    return dump_json(payload)


def _sidecar_token_list(group: str, records: dict[str, Token]) -> list[dict[str, Any]]:
    result = []
    for token in records.values():
        if token.path[0] != group:
            continue
        try:
            value: Any = value_to_css(token, records)
        except DesignError:
            value = resolve_token(token, records)
        entry: dict[str, Any] = {"name": _token_name(token), "value": value}
        if token.description:
            entry["purpose"] = token.description
        result.append(entry)
    return result


def _token_name(token: Token) -> str:
    """Name a token for sidecar metadata, including a group's DTCG `$root` token."""
    return "-".join(token.path[1:]) or token.path[0]


def _read_primitives(path: Path, records: dict[str, Token]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = read_json(path)
    components = payload.get("components")
    if not isinstance(components, list):
        raise DesignError(f"{path} must declare a components array")
    required = {"name", "kind", "refersTo", "description", "html", "css"}
    for index, component in enumerate(components):
        if not isinstance(component, dict) or not required.issubset(component):
            raise DesignError(f"{path} component {index} must declare {', '.join(sorted(required))}")
        component["css"] = _materialize_css_variables(str(component["css"]), records, path)
    return components


def _tonal_ramp(canonical: str) -> list[str]:
    """Create a deterministic eight-stop fallback for a color without authored metadata."""
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", canonical)
    if not match:
        return [canonical] * 8
    base = tuple(int(match.group(1)[index:index + 2], 16) for index in (0, 2, 4))
    stops = (0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.95)
    ramp: list[str] = []
    for stop in stops:
        if stop <= 0.5:
            factor = stop / 0.5
            channels = tuple(round(channel * factor) for channel in base)
        else:
            factor = (stop - 0.5) / 0.5
            channels = tuple(round(channel + (255 - channel) * factor) for channel in base)
        ramp.append("#" + "".join(f"{channel:02X}" for channel in channels))
    return ramp


def _materialize_css_variables(css: str, records: dict[str, Token], path: Path) -> str:
    """Make sidecar snippets self-contained inside Impeccable's shadow DOM."""
    by_variable: dict[str, str] = {}
    for token in records.values():
        base = "--design-" + "-".join(_slug(part) for part in token.path)
        if token.type == "typography":
            for prop, value in typography_to_design(resolve_value(token.value, records, (token.dotted,))).items():
                by_variable[f"{base}-{_slug(prop)}"] = str(value)
            continue
        try:
            by_variable[base] = value_to_css(token, records)
        except DesignError:
            continue

    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        value = by_variable.get(variable)
        if value is None:
            raise DesignError(f"{path} references unknown generated variable {variable}")
        return value

    return re.sub(r"var\((--design-[A-Za-z0-9_-]+)\)", replace, css)


def _narrative(sections: dict[str, str]) -> dict[str, Any]:
    overview = sections.get("Overview", "")
    north_star = ""
    match = re.search(r"\*\*Creative North Star:?\*\*\s*[\"“]?([^\n\"”]+)", overview, re.IGNORECASE)
    if match:
        north_star = match.group(1).strip()
    elif overview:
        north_star = overview.splitlines()[0].strip(' #*-"')
    characteristics = [re.sub(r"^\*\*(.+?)\*\*:?\s*", r"\1: ", line[2:]).strip()
                       for line in overview.splitlines() if line.startswith("- ")]
    rules: list[dict[str, str]] = []
    for section, body in sections.items():
        for item in re.finditer(r"\*\*(The [^*]+? Rule)\.\*\*\s*([^\n]+)", body):
            rules.append({"name": item.group(1), "body": item.group(2).strip(),
                          "section": _slug(section.split(" & ")[0])})
    guardrails = sections.get("Do's and Don'ts", "")
    dos = [line[2:].strip() for line in guardrails.splitlines()
           if line.startswith("- ") and re.match(r"\*\*Do\b", line[2:], re.IGNORECASE)]
    donts = [line[2:].strip() for line in guardrails.splitlines()
             if line.startswith("- ") and re.match(r"\*\*Don['’]t\b", line[2:], re.IGNORECASE)]
    return {
        "northStar": north_star,
        "overview": overview,
        "keyCharacteristics": characteristics,
        "rules": rules,
        "dos": dos,
        "donts": donts,
    }


def _render_medium(genres: list[Path], root: Path) -> str:
    lines = ["# Media and genres", "", f"<!-- {_generated_notice(root, 'genres')} -->", "",
             "| Genre | Register | Media | Contract | Engine |", "| --- | --- | --- | --- | --- |"]
    for path in genres:
        try:
            frontmatter, _ = parse_document_frontmatter(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        engine = frontmatter.get("engine", "builtin").replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {frontmatter.get('name', path.stem)} | {frontmatter.get('register', '-')} | "
            f"{frontmatter.get('media', '-')} | `/.design/genres/{path.name}` | {engine} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def _generated_notice(root: Path, source: str) -> str:
    portuguese = _repo_language(root).lower().startswith("pt")
    if source == "tokens":
        return ("GERADO por `cq design build` a partir de /.design/tokens.json. Não edite aqui."
                if portuguese else
                "GENERATED by `cq design build` from /.design/tokens.json. Do not edit here.")
    if source == "knowledge":
        return ("GERADO por `cq design build` a partir de /docs/. Não edite aqui."
                if portuguese else
                "GENERATED by `cq design build` from /docs/. Do not edit here.")
    return ("GERADO por `cq design build` a partir de /.design/genres/. Não edite aqui."
            if portuguese else
            "GENERATED by `cq design build` from /.design/genres/. Do not edit here.")


def _repo_language(root: Path) -> str:
    for name in ("AGENTS.md", "CLAUDE.md"):
        try:
            text = (root / name).read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"^Language:\s*([A-Za-z0-9-]+)", text, re.MULTILINE)
        if match:
            return match.group(1)
    return "en"


def _slug(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "token"


def _color_to_typst(value: Any) -> str:
    """Project one DTCG color as a Typst color; an unsupported space keeps its CSS string."""
    if not isinstance(value, dict) or not isinstance(value.get("colorSpace"), str):
        raise DesignError(f"cannot project {value!r} as a Typst color")
    components = value.get("components")
    if (not isinstance(components, list) or len(components) != 3
            or not all(isinstance(part, (int, float)) for part in components)):
        raise DesignError(f"cannot project {value!r} as a Typst color")
    alpha = value.get("alpha", 1)
    if not isinstance(alpha, (int, float)):
        raise DesignError(f"cannot project {value!r} as a Typst color")
    alpha = max(0.0, min(1.0, float(alpha)))
    space = value["colorSpace"]
    channels = [float(part) for part in components]
    if space == "srgb":
        if alpha == 1:
            # Installed repositories already carry this exact line; keep its bytes stable.
            return f"rgb({json.dumps(color_to_css(value))})"
        rgb = [max(0, min(255, round(channel * 255))) for channel in channels]
        return f"rgb({rgb[0]}, {rgb[1]}, {rgb[2]}, {_typst_number(alpha * 100)}%)"
    suffix = "" if alpha == 1 else f", {_typst_number(alpha * 100)}%"
    if space == "srgb-linear":
        parts = ", ".join(f"{_typst_number(channel * 100)}%" for channel in channels)
        return f"color.linear-rgb({parts}{suffix})"
    if space == "hsl":
        hue, saturation, lightness = channels
        return (f"color.hsl({_typst_number(hue)}deg, {_typst_number(saturation)}%, "
                f"{_typst_number(lightness)}%{suffix})")
    if space in {"oklab", "oklch"}:
        lightness, second, third = channels
        angle = f"{_typst_number(third)}deg" if space == "oklch" else _typst_number(third)
        return (f"color.{space}({_typst_number(lightness * 100)}%, "
                f"{_typst_number(second)}, {angle}{suffix})")
    # Typst has no constructor for this space, so the portable CSS string stands.
    return json.dumps(color_to_css(value), ensure_ascii=False)


def _typst_number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _dimension_to_typst(value: Any) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("value"), (int, float)):
        raise DesignError(f"cannot project {value!r} as a Typst dimension")
    amount = float(value["value"])
    if value.get("unit") == "px":
        # CSS fixes 96 px per inch; Typst fixes 72 pt per inch.
        return f"{_typst_number(amount * 0.75)}pt"
    if value.get("unit") == "rem":
        return f"{_typst_number(amount)}em"
    raise DesignError(f"cannot project {value!r} as a Typst dimension")


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)
