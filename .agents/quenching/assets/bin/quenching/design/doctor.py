"""Read-only conformance checks for the design front."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from quenching.design.build import GENERATED_PATHS, build_drift, compute_build
from quenching.design.markdown import split_h2
from quenching.design.model import (
    DesignError,
    asset_manifest_entries,
    color_to_css,
    contrast_pairs,
    contrast_policy,
    contrast_ratio,
    font_asset_paths,
    font_metadata,
    non_web_literal_globs,
    quenching_extension,
    read_json,
    relative_luminance,
    resolve_token,
    resolve_value,
    token_map,
    validate_source,
)


def inspect_design(root: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    root = root.resolve()
    design_root = root / ".design"
    token_path = design_root / "tokens.json"
    findings: list[dict[str, str]] = []
    payload: dict[str, Any] = {
        "root": str(root),
        "installed": token_path.is_file(),
        "generated": list(GENERATED_PATHS),
        "webDetector": _web_detector(root),
    }
    if not token_path.is_file():
        findings.append({
            "severity": "error", "code": "design-front-absent", "path": ".design/tokens.json",
            "message": "design front is not installed; run `/quenching-design align`",
        })
        return payload, findings
    try:
        source = read_json(token_path)
    except DesignError as exc:
        findings.append({
            "severity": "error", "code": "design-source-unreadable", "path": ".design/tokens.json",
            "message": str(exc),
        })
        return payload, findings
    try:
        extension = quenching_extension(source)
        payload.update({
            "schema": source.get("$schema"),
            "tokenCount": len(token_map(source)),
            "designMdVersion": extension.get("designMd", {}).get("version"),
            "productSchema": extension.get("productSchema"),
        })
        try:
            payload["contrastPolicy"] = contrast_policy(source)
            payload["contrastPairs"] = _contrast_summary(source)
        except DesignError:
            pass
    except DesignError:
        # `validate_source` below owns the actionable shape finding; status should
        # still return a stable payload when the source is malformed.
        payload.update({"schema": source.get("$schema"), "tokenCount": 0})
    findings.extend(validate_source(source))
    scope, scope_findings = _non_web_literal_scope(root, source)
    payload["nonWebLiteralScope"] = scope
    findings.extend(scope_findings)
    if not findings:
        try:
            result = compute_build(root)
        except DesignError as exc:
            findings.append({
                "severity": "error", "code": "design-build-refusal", "path": ".design/tokens.json",
                "message": str(exc),
            })
        else:
            payload["sources"] = list(result.sources)
            findings.extend(build_drift(result))
    sidecar_path = root / ".impeccable" / "design.json"
    try:
        sidecar = read_json(sidecar_path)
    except DesignError:
        sidecar = None
    if sidecar is not None:
        payload["sidecarSchemaVersion"] = sidecar.get("schemaVersion")
        payload["sidecarComponentCount"] = len(sidecar.get("components", [])) if isinstance(sidecar.get("components"), list) else 0
    findings.extend(_orphan_assets(root))
    findings.extend(_non_web_literals(root, source))
    findings.extend(_font_findings(root, source))
    findings.extend(_contrast_findings(source))
    asset_payload, asset_findings = _asset_findings(root, source)
    payload["assetManifest"] = asset_payload
    findings.extend(asset_findings)
    return payload, sorted(findings, key=lambda item: (item["severity"] != "error", item["path"], item["code"]))


def status_payload(root: Path) -> dict[str, Any]:
    payload, findings = inspect_design(root)
    errors = sum(item["severity"] == "error" for item in findings)
    warnings = len(findings) - errors
    payload.update({
        "ok": errors == 0,
        "errors": errors,
        "warnings": warnings,
        "findings": findings,
    })
    genres = root / ".design" / "genres"
    payload["genres"] = sorted(path.stem for path in genres.glob("*.md")) if genres.is_dir() else []
    return payload


def _orphan_assets(root: Path) -> list[dict[str, str]]:
    assets = root / ".design" / "assets"
    if not assets.is_dir():
        return []
    searchable: list[str] = []
    # A public README shows the interface asset it cites; that citation is a reference too.
    for readme in sorted(root.glob("README*")):
        try:
            searchable.append(readme.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    for base in (root / ".design", root / "docs" / "standards" / "design"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or assets == path.parent or ".design/build" in path.as_posix():
                continue
            try:
                searchable.append(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    haystack = "\n".join(searchable)
    placeholders = {
        match.group(1).replace("\\", "/")
        for match in re.finditer(r"\{\{\s*asset\.([^{}\s]+)\s*\}\}", haystack)
    }
    findings = []
    for asset in sorted(path for path in assets.rglob("*") if path.is_file()):
        if (asset.name in {".gitkeep", "manifest.json"}
                or asset.name.lower().startswith(("readme", "license", "ofl"))):
            continue
        relative = asset.relative_to(root).as_posix()
        design_relative = asset.relative_to(root / ".design").as_posix()
        if (relative not in haystack and design_relative not in haystack and asset.name not in haystack
                and design_relative not in placeholders):
            findings.append({
                "severity": "warning", "code": "design-asset-orphan", "path": relative,
                "message": "asset is not referenced by tokens, design standards, genres, or media primitives",
            })
    return findings


def _asset_findings(root: Path, source: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    manifest_path = root / ".design" / "assets" / "manifest.json"
    if not manifest_path.is_file():
        return [], []
    try:
        entries = asset_manifest_entries(read_json(manifest_path))
    except DesignError as exc:
        return [], [{"severity": "error", "code": "design-asset-manifest",
                     "path": manifest_path.relative_to(root).as_posix(), "message": str(exc)}]
    findings: list[dict[str, str]] = []
    asset_root = manifest_path.parent.resolve()
    for entry in entries:
        path = (asset_root / entry["path"]).resolve()
        try:
            path.relative_to(asset_root)
        except ValueError:
            findings.append({"severity": "error", "code": "design-asset-manifest",
                             "path": manifest_path.relative_to(root).as_posix(),
                             "message": f"asset manifest path escapes .design/assets: {entry['path']}"})
            continue
        if not path.is_file():
            findings.append({"severity": "error", "code": "design-asset-manifest",
                             "path": manifest_path.relative_to(root).as_posix(),
                             "message": f"asset manifest file is missing: {entry['path']}"})
    records = token_map(source)
    dark_surfaces: list[str] = []
    for token in records.values():
        if token.type != "color" or token.path[-1] not in {"surface", "surface-dark"}:
            continue
        try:
            if relative_luminance(resolve_token(token, records)) < 0.5:
                dark_surfaces.append(token.dotted)
        except DesignError:
            continue
    for surface in dark_surfaces:
        for entry in entries:
            if entry["ink"] != "dark" or entry["role"] not in {"lockup", "mark", "wordmark", "icon"}:
                continue
            matching = [candidate for candidate in entries
                        if candidate["role"] == entry["role"]
                        and candidate["orientation"] == entry["orientation"]
                        and candidate["ink"] in {"light", "any"}]
            if not matching:
                findings.append({
                    "severity": "error", "code": "design-asset-variant-missing",
                    "path": ".design/assets/manifest.json",
                    "message": (f"surface {surface} needs a light {entry['role']} "
                                f"variant for {entry['orientation']} orientation"),
                })
    return entries, findings


def _font_findings(root: Path, source: dict[str, Any]) -> list[dict[str, str]]:
    """Check declared font assets without making unannotated legacy fonts invalid."""
    records = token_map(source)
    declarations: dict[str, tuple[Any, dict[str, Any]]] = {}
    try:
        paths_by_token = font_asset_paths(source, root / ".design" / "assets")
    except DesignError as exc:
        return [{"severity": "error", "code": "design-font-metadata",
                 "path": ".design/tokens.json", "message": str(exc)}]
    for token in records.values():
        if token.type != "fontFamily":
            continue
        metadata = font_metadata(token)
        if metadata:
            declarations[token.dotted] = (token, metadata)
    if not declarations:
        return []
    by_family: dict[str, tuple[Any, dict[str, Any]]] = {}
    for token, metadata in declarations.values():
        try:
            value = resolve_value(token.value, records, (token.dotted,))
        except DesignError:
            continue
        names = value if isinstance(value, list) else [value]
        for name in names:
            if isinstance(name, str):
                by_family[name.casefold()] = (token, metadata)
    findings: list[dict[str, str]] = []
    for token in records.values():
        if token.type != "typography":
            continue
        try:
            value = resolve_value(token.value, records, (token.dotted,))
        except DesignError:
            continue
        family = value.get("fontFamily") if isinstance(value, dict) else None
        names = family if isinstance(family, list) else [family]
        for name in names:
            declaration = by_family.get(name.casefold()) if isinstance(name, str) else None
            if not declaration:
                continue
            font_token, metadata = declaration
            if metadata.get("source") == "webfont":
                continue
            paths = paths_by_token.get(font_token.dotted, [])
            missing = [path for path in paths if not path.exists()]
            if missing or not paths:
                detail = ", ".join(str(path.relative_to(root)) for path in missing) or "no files or directories"
                findings.append({
                    "severity": "error", "code": "design-font-unresolved",
                    "path": f".design/tokens.json#{token.dotted}",
                    "message": f"font family {name!r} has no resolvable declared asset: {detail}",
                })
    return findings


def _contrast_findings(source: dict[str, Any]) -> list[dict[str, str]]:
    try:
        policy = contrast_policy(source)
    except DesignError as exc:
        return [{"severity": "error", "code": "design-contrast-policy",
                 "path": ".design/tokens.json", "message": str(exc)}]
    records = token_map(source)
    findings: list[dict[str, str]] = []
    for foreground, background in contrast_pairs(source):
        try:
            ratio = contrast_ratio(resolve_token(foreground, records), resolve_token(background, records))
        except DesignError as exc:
            findings.append({
                "severity": "warning", "code": "design-contrast-unmeasurable",
                "path": f".design/tokens.json#{foreground.dotted}",
                "message": f"contrast pair {foreground.dotted}/{background.dotted} is not measurable: {exc}",
                "pair": f"{foreground.dotted}/{background.dotted}",
            })
            continue
        if ratio < policy["threshold"]:
            findings.append({
                "severity": "error", "code": "design-contrast-failure",
                "path": f".design/tokens.json#{foreground.dotted}",
                "message": (f"contrast pair {foreground.dotted}/{background.dotted} is "
                            f"{ratio:.2f}:1, below {policy['threshold']:.2f}:1"),
                "pair": f"{foreground.dotted}/{background.dotted}",
                "ratio": f"{ratio:.2f}",
                "threshold": f"{policy['threshold']:.2f}",
                "level": policy["level"],
                "textSize": policy["textSize"],
            })
    return findings


def _contrast_summary(source: dict[str, Any]) -> list[dict[str, str]]:
    records = token_map(source)
    summary: list[dict[str, str]] = []
    for foreground, background in contrast_pairs(source):
        pair = f"{foreground.dotted}/{background.dotted}"
        try:
            ratio = contrast_ratio(resolve_token(foreground, records), resolve_token(background, records))
        except DesignError as exc:
            summary.append({"pair": pair, "status": "unmeasurable", "reason": str(exc)})
        else:
            summary.append({"pair": pair, "status": "measured", "ratio": f"{ratio:.2f}"})
    return summary


def _web_detector(root: Path) -> dict[str, Any]:
    """Report optional Impeccable availability without installing or executing it."""
    candidates = []
    resolved = shutil.which("impeccable")
    if resolved:
        candidates.append(Path(resolved))
    candidates.extend([
        root / "node_modules" / ".bin" / "impeccable",
        root / ".impeccable" / "node_modules" / ".bin" / "impeccable",
    ])
    for candidate in candidates:
        if candidate.is_file() and (candidate.is_absolute() or candidate.exists()):
            return {"name": "impeccable", "state": "available", "path": str(candidate)}
    return {
        "name": "impeccable",
        "state": "skipped",
        "reason": "optional detector is not installed; run it only when it already resolves locally",
    }


def _non_web_literal_scope(root: Path, source: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    try:
        source_globs = non_web_literal_globs(source)
    except DesignError:
        source_globs = []
    production = root / "docs" / "standards" / "design" / "production.md"
    production_globs: list[str] = []
    try:
        sections = split_h2(production.read_text(encoding="utf-8"))
    except OSError:
        sections = {}
    section = sections.get("Non-web literal scope", "")
    if section:
        for line in section.splitlines():
            if not line.strip().startswith("-"):
                continue
            value = line.strip()[1:].strip().strip("`").strip()
            if not value:
                findings.append({"severity": "error", "code": "design-nonweb-scope",
                                 "path": production.relative_to(root).as_posix(),
                                 "message": "Non-web literal scope entries must be non-empty paths"})
                continue
            relative = Path(value)
            if relative.is_absolute() or ".." in relative.parts:
                findings.append({"severity": "error", "code": "design-nonweb-scope",
                                 "path": production.relative_to(root).as_posix(),
                                 "message": f"non-web literal glob must stay inside the repository: {value}"})
                continue
            production_globs.append(value.replace("\\", "/"))
    effective = sorted(set(source_globs + production_globs)) or [".design/media/**"]
    return effective, findings


def _non_web_literals(root: Path, source: dict[str, Any]) -> list[dict[str, str]]:
    """Detect manual color literals in non-web sources; Impeccable owns web drift."""
    literals: dict[str, str] = {}
    try:
        records = token_map(source)
        for token in records.values():
            if token.type != "color":
                continue
            value = color_to_css(resolve_token(token, records))
            if value.startswith("#"):
                literals[value.lower()] = token.dotted
    except DesignError:
        return []
    findings: list[dict[str, str]] = []
    scope, _ = _non_web_literal_scope(root, source)
    candidates: set[Path] = set()
    for pattern in scope:
        try:
            if pattern.endswith("/**"):
                base = root / pattern[:-3].rstrip("/")
                paths = base.rglob("*") if base.is_dir() else []
            else:
                paths = root.glob(pattern)
        except (OSError, ValueError):
            continue
        for path in paths:
            try:
                resolved = path.resolve()
                resolved.relative_to(root.resolve())
            except ValueError:
                continue
            if path.is_file():
                candidates.add(path)
    for path in sorted(candidates):
        relative_path = path.relative_to(root).as_posix()
        if (path.suffix.lower() in {".css", ".html", ".htm", ".json"}
                or relative_path.startswith(".design/build/")):
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeDecodeError):
            continue
        for literal, token_name in literals.items():
            if re.search(rf"(?<![0-9a-f]){re.escape(literal)}(?![0-9a-f])", text):
                findings.append({
                    "severity": "error", "code": "design-nonweb-literal",
                    "path": path.relative_to(root).as_posix(),
                    "message": f"non-web primitive repeats {literal} from {token_name}; import the generated adapter instead",
                })
    return findings
