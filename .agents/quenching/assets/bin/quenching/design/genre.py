"""Genre contracts and deterministic multi-medium rendering."""
from __future__ import annotations

import html
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from quenching.common.io import write_text
from quenching.design.build import build_drift, compute_build, write_build
from quenching.design.markdown import parse_document_frontmatter, render_markdown, split_h2
from quenching.design.model import DesignError, font_asset_paths, read_json

SUBPROCESS_TIMEOUT_S = 60
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FIELD_RE = re.compile(r"^- `([A-Za-z][A-Za-z0-9_-]*)` — (required|optional)(?: (list|scalar))?\s*(.*)$")
REPEAT_BLOCK_RE = re.compile(
    r"\{\{\s*#field\.([A-Za-z][A-Za-z0-9_-]*)\s*\}\}(.*?)"
    r"\{\{\s*/field\.\1\s*\}\}", re.DOTALL,
)
REPEAT_MARKER_RE = re.compile(r"\{\{\s*[#/]\s*field\.[A-Za-z][A-Za-z0-9_-]*\s*\}\}")


def new_genre(root: Path, slug: str, name: str, register: str, media: list[str],
              fields: list[str], write: bool = True, engine: str = "builtin") -> dict[str, Any]:
    root = root.resolve()
    if not SLUG_RE.fullmatch(slug):
        raise DesignError("genre slug must be kebab-case")
    normalized_media = _media(media)
    parsed_fields = [_parse_field(field) for field in fields]
    if not parsed_fields:
        raise DesignError("a genre needs at least one declared field")
    engine = _validate_engine(engine)
    targets = {f".design/genres/{slug}.md": _genre_document(
        slug, name, register, normalized_media, parsed_fields, engine,
    )}
    for medium in normalized_media:
        template_medium = "typst" if medium == "pdf" else medium
        if template_medium not in {"html", "typst"}:
            continue
        relative = f".design/media/{template_medium}/{slug}.{'html' if template_medium == 'html' else 'typ'}"
        if relative in targets:
            continue
        targets[relative] = _genre_template(name, template_medium, parsed_fields)
    collisions = sorted(relative for relative in targets if (root / relative).exists())
    if collisions:
        raise DesignError("genre would overwrite existing files: " + ", ".join(collisions))
    if write:
        for relative, content in targets.items():
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_text(str(target), content)
    generated = write_build(compute_build(root)) if write else []
    return {"ok": True, "mode": "write" if write else "check", "files": sorted(targets),
            "generated": generated, "genre": slug, "media": normalized_media}


def render_genre(root: Path, slug: str, medium: str, data_path: Path,
                 output: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    medium = medium.lower()
    if medium not in {"html", "typst", "pdf"}:
        raise DesignError("medium must be html, typst, or pdf")
    result = compute_build(root)
    drift = build_drift(result)
    if drift:
        raise DesignError("generated design projections are stale; run `cq design build` before rendering")
    genre_path = root / ".design" / "genres" / f"{slug}.md"
    try:
        frontmatter, body = parse_document_frontmatter(genre_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DesignError(f"unknown genre: {slug}") from exc
    allowed = _media(frontmatter.get("media", "").split(","))
    if medium not in allowed:
        raise DesignError(f"genre {slug} does not declare medium {medium}")
    fields = _fields_from_body(body)
    data = _read_data(data_path)
    missing = []
    for name, required, _, is_list in fields:
        value = data.get(name)
        if is_list:
            if value is not None and not isinstance(value, list):
                raise DesignError(f"render data field {name} must be a JSON array")
            if required and not value:
                missing.append(name)
        elif required and not str(value or "").strip():
            missing.append(name)
    if missing:
        raise DesignError("render data is missing required fields: " + ", ".join(missing))
    template_medium = "typst" if medium == "pdf" else medium
    suffix = "html" if medium == "html" else ("pdf" if medium == "pdf" else "typ")
    template_suffix = "html" if template_medium == "html" else "typ"
    template_path = root / ".design" / "media" / template_medium / f"{slug}.{template_suffix}"
    try:
        template = template_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise DesignError(f"genre {slug} has no {template_medium} template") from exc
    target = (output if output and output.is_absolute() else root / output) if output else (
        root / ".design" / "build" / f"{slug}.{suffix}")
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    list_fields = {name for name, _, _, is_list in fields if is_list}
    engine = _validate_engine(frontmatter.get("engine", "builtin"))
    if engine == "builtin":
        rendered = _substitute(
            template, data, medium, target, root, frontmatter.get("register", "read"),
            list_fields=list_fields,
        )
        if medium == "pdf":
            _compile_pdf(rendered, target, root)
        else:
            write_text(str(target), rendered)
    else:
        _render_external(engine, data, template, medium, root, target)
    return {"ok": True, "genre": slug, "medium": medium,
            "output": _relative(target, root), "fields": sorted(data)}


def _media(values: list[str]) -> list[str]:
    media = sorted({value.strip().lower() for value in values if value.strip()})
    unknown = sorted(set(media) - {"html", "typst", "pdf"})
    if unknown:
        raise DesignError("unsupported genre media: " + ", ".join(unknown))
    return media


def _validate_engine(value: str) -> str:
    engine = str(value or "").strip()
    if not engine:
        raise DesignError("genre engine must be `builtin` or a non-empty command")
    return engine


def _parse_field(value: str) -> tuple[str, bool, str, bool]:
    parts = value.split(":", 3)
    if len(parts) < 2 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", parts[0]):
        raise DesignError(f"invalid field {value!r}; use name:required|optional[:description]")
    requirement = parts[1].lower()
    if requirement not in {"required", "optional"}:
        raise DesignError(f"invalid field {value!r}; requirement must be required or optional")
    cardinality = "scalar"
    description = ""
    if len(parts) >= 3:
        if parts[2].lower() in {"scalar", "list"}:
            cardinality = parts[2].lower()
            description = parts[3].strip() if len(parts) == 4 else ""
        else:
            description = ":".join(parts[2:]).strip()
    return parts[0], requirement == "required", description, cardinality == "list"


def _genre_document(slug: str, name: str, register: str, media: list[str],
                    fields: list[tuple[str, bool, str, bool]], engine: str) -> str:
    lines = ["---", f"name: {json.dumps(name, ensure_ascii=False)}", f"slug: {json.dumps(slug)}",
             f"register: {json.dumps(register, ensure_ascii=False)}",
             f"media: {json.dumps(', '.join(media))}", f"engine: {json.dumps(engine, ensure_ascii=False)}",
             "---", "", f"# {name}", "", "## Fields", ""]
    for field, required, description, is_list in fields:
        suffix = f" {description}" if description else ""
        cardinality = " list" if is_list else ""
        lines.append(f"- `{field}` — {'required' if required else 'optional'}{cardinality}{suffix}")
    lines.extend(["", "## Composition", "", "Describe the stable composition this genre preserves across media.",
                  "", "## Guardrails", "", "- Cite `/docs/standards/design/production.md`.",
                  "- Import generated medium tokens; never repeat a primitive value."])
    return "\n".join(lines).rstrip() + "\n"


def _genre_template(name: str, medium: str,
                    fields: list[tuple[str, bool, str, bool]]) -> str:
    """Mint a template whose placeholders are exactly the genre's declared fields."""
    if medium == "html":
        title_field = next(
            (field for field, _, _, is_list in fields if field == "title" and not is_list),
            next((field for field, _, _, is_list in fields if not is_list), fields[0][0]),
        )
        blocks = []
        for field, _, description, is_list in fields:
            label = html.escape(description or field.replace("-", " ").replace("_", " ").title())
            if field == title_field:
                blocks.append(f"    <h1>{{{{field.{field}}}}}</h1>")
            elif is_list:
                blocks.append(
                    f'    <section class="field field-{html.escape(field)}">'
                    f"<h2>{label}</h2><ul>{{{{#field.{field}}}}}"
                    f"<li>{{{{item}}}}</li>{{{{/field.{field}}}}}</ul></section>"
                )
            elif field == "body":
                blocks.append(f"    <article aria-label={json.dumps(label)}>{{{{field.{field}}}}}</article>")
            else:
                blocks.append(
                    f'    <section class="field field-{html.escape(field)}">'
                    f"<h2>{label}</h2><p>{{{{field.{field}}}}}</p></section>"
                )
        return "\n".join([
            "<!doctype html>", '<html lang="en">', "<head>", '  <meta charset="utf-8">',
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            f"  <title>{{{{field.{title_field}}}}}</title>", "  <style>{{tokens.css}}</style>",
            "  <style>",
            "    body { margin: 0; color: var(--design-colors-on-surface); background: var(--design-colors-surface); font-family: var(--design-typography-body-font-family); }",
            "    main { max-width: var(--design-layout-content-wide); margin: auto; padding: var(--design-spacing-xl) var(--design-spacing-lg); }",
            "    h1 { font-family: var(--design-typography-display-font-family); font-size: var(--design-typography-display-font-size); line-height: var(--design-typography-display-line-height); }",
            "    article, section { max-width: var(--design-layout-reading); line-height: var(--design-typography-body-line-height); }",
            "  </style>", "</head>", "<body>", f"  <main aria-label={json.dumps(name)}>",
            *blocks, "  </main>", "</body>", "</html>", "",
        ])
    lines = [
        '#import "{{tokens.typ}}": *',
        '#import "{{primitives.typ}}": eyebrow, rule, frame', "",
        "#set text(font: token-typography-body-font-family, size: token-typography-body-font-size, weight: token-typography-body-font-weight)",
        "#show heading.where(level: 1): it => text(size: token-typography-display-font-size, weight: token-typography-display-font-weight, it.body)",
        "", "#frame[",
    ]
    for index, (field, _, description, is_list) in enumerate(fields):
        label = description or field.replace("-", " ").replace("_", " ").title()
        if is_list:
            lines.extend([
                f"  == {label}",
                f"  {{{{#field.{field}}}}}",
                "  - {{item}}",
                f"  {{{{/field.{field}}}}}",
            ])
        elif index == 0 or field == "title":
            lines.append(f"  = {{{{field.{field}}}}}")
        elif field == "body":
            lines.append(f"  {{{{field.{field}}}}}")
        else:
            lines.extend([f"  == {label}", f"  {{{{field.{field}}}}}"])
    lines.extend(["]", ""])
    return "\n".join(lines)


def _fields_from_body(body: str) -> list[tuple[str, bool, str, bool]]:
    section = split_h2(body).get("Fields", "")
    fields = []
    for line in section.splitlines():
        match = FIELD_RE.match(line)
        if match:
            fields.append((match.group(1), match.group(2) == "required", match.group(4).strip(), match.group(3) == "list"))
    if not fields:
        raise DesignError("genre contract declares no parseable fields")
    return fields


def _read_data(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DesignError(f"cannot read render data: {exc}") from exc
    if not isinstance(value, dict):
        raise DesignError("render data must be one JSON object")
    return value


def _substitute(template: str, data: dict[str, Any], medium: str, target: Path, root: Path,
                register: str = "read", list_fields: set[str] | None = None,
                item: Any = None, item_context: bool = False,
                item_field: str | None = None) -> str:
    list_fields = list_fields or set()
    template = _expand_repeat_blocks(
        template, data, medium, target, root, register, list_fields,
    )

    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        if key == "tokens.css":
            return (root / ".design" / "build" / "tokens.css").read_text(encoding="utf-8")
        if key == "tokens.typ":
            return os.path.relpath(root / ".design" / "build" / "tokens.typ", target.parent).replace(os.sep, "/")
        if key == "primitives.typ":
            return os.path.relpath(root / ".design" / "media" / "typst" / "primitives.typ", target.parent).replace(os.sep, "/")
        if key.startswith("asset."):
            return _asset_reference(key[6:], medium, target, root)
        if key == "item" or key.startswith("item."):
            if not item_context:
                raise DesignError("template uses item placeholder outside a repeat block")
            item_key = key[5:] if key.startswith("item.") else None
            if item_key is None:
                value = item
                name = item_field or "item"
            elif isinstance(item, dict):
                value = item.get(item_key, "")
                name = item_key
            else:
                value = ""
                name = item_key
            return _render_field(name, _value_text(value), medium, register)
        if key.startswith("field."):
            name = key[6:]
            if name in list_fields:
                raise DesignError(f"list field {name} must be used in a repeat block")
            value = _value_text(data.get(name, ""))
            return _render_field(name, value, medium, register)
        raise DesignError(f"template uses unknown placeholder {key!r}")
    rendered = re.sub(r"\{\{\s*([^{}]+?)\s*\}\}", replace, template)
    if "{{" in rendered or "}}" in rendered:
        raise DesignError("template retains an unresolved placeholder")
    return rendered.rstrip() + "\n"


def _expand_repeat_blocks(template: str, data: dict[str, Any], medium: str, target: Path,
                          root: Path, register: str, list_fields: set[str]) -> str:
    if not REPEAT_MARKER_RE.search(template):
        return template

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in list_fields:
            raise DesignError(f"repeat block names unknown or scalar field {name}")
        body = match.group(2)
        if REPEAT_MARKER_RE.search(body):
            raise DesignError("nested repeat blocks are not supported")
        values = data.get(name)
        if values is None:
            values = []
        if not isinstance(values, list):
            raise DesignError(f"render data field {name} must be a JSON array")
        return "".join(
            _substitute(
                body, data, medium, target, root, register, list_fields,
                value, True, name,
            )
            for value in values
        )

    expanded = REPEAT_BLOCK_RE.sub(replace, template)
    if REPEAT_MARKER_RE.search(expanded):
        raise DesignError("repeat block is unclosed or has mismatched markers")
    return expanded


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _render_external(engine: str, data: dict[str, Any], template: str, medium: str,
                     root: Path, target: Path) -> None:
    try:
        command = shlex.split(engine)
    except ValueError as exc:
        raise DesignError(f"external engine command is not valid: {exc}") from exc
    if not command:
        raise DesignError("external engine command is empty")
    request = {
        "data": data,
        "template": template,
        "medium": medium,
        "root": str(root),
        "output": str(target),
    }
    try:
        run = subprocess.run(
            command,
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            capture_output=True,
            timeout=SUBPROCESS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise DesignError(f"external engine timed out after {SUBPROCESS_TIMEOUT_S}s") from exc
    except OSError as exc:
        raise DesignError(f"external engine could not start: {exc}") from exc
    if run.returncode:
        detail = run.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise DesignError(f"external engine exited with status {run.returncode}{suffix}")
    if not run.stdout:
        raise DesignError("external engine produced empty output")
    target.write_bytes(run.stdout)


def _render_field(name: str, value: str, medium: str, register: str) -> str:
    """Apply Markdown only to the body of a read-register genre."""
    if name == "body" and register == "read":
        return render_markdown(value, medium)
    return html.escape(value) if medium == "html" else _typst_escape(value)


def _asset_reference(relative: str, medium: str, target: Path, root: Path) -> str:
    if not relative or "\\" in relative:
        raise DesignError("asset placeholder must use a non-empty POSIX relative path")
    requested = Path(relative)
    if requested.is_absolute() or any(part in {"", ".", ".."} for part in requested.parts):
        raise DesignError(f"asset placeholder escapes .design/assets: {relative}")
    asset_root = (root / ".design" / "assets").resolve()
    asset = (asset_root / requested).resolve()
    try:
        asset.relative_to(asset_root)
    except ValueError as exc:
        raise DesignError(f"asset placeholder escapes .design/assets: {relative}") from exc
    if not asset.is_file():
        raise DesignError(f"asset placeholder does not name a file: {relative}")
    reference = os.path.relpath(asset, target.parent).replace(os.sep, "/")
    return quote(reference, safe="/:@-._~") if medium == "html" else reference


def _typst_escape(value: str) -> str:
    return re.sub(r"([\\#\[\]*_])", r"\\\1", value)


def _compile_pdf(source: str, target: Path, root: Path) -> None:
    compiler = shutil.which("typst")
    if not compiler:
        raise DesignError("PDF rendering requires `typst` on PATH; HTML and .typ rendering remain available")
    fd, temporary = tempfile.mkstemp(prefix=f".{target.stem}.", suffix=".typ", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(source)
        font_paths: set[Path] = set()
        try:
            declared = font_asset_paths(read_json(root / ".design" / "tokens.json"),
                                        root / ".design" / "assets")
            for paths in declared.values():
                for path in paths:
                    if not path.exists():
                        raise DesignError(f"declared font asset is missing: {path.relative_to(root)}")
                    font_paths.add(path if path.is_dir() else path.parent)
        except ValueError as exc:
            if isinstance(exc, DesignError):
                raise
            raise DesignError(str(exc)) from exc
        command = [compiler, "compile", temporary, str(target), "--root", str(root)]
        for path in sorted(font_paths):
            command.extend(["--font-path", str(path)])
        try:
            run = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                 timeout=SUBPROCESS_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise DesignError(f"typst compile timed out after {SUBPROCESS_TIMEOUT_S}s") from exc
        except OSError as exc:
            raise DesignError(f"typst could not start: {exc}") from exc
        if run.returncode:
            raise DesignError(f"typst compile failed: {(run.stderr or run.stdout).strip()}")
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
