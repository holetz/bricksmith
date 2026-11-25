"""Install the design front's starter pack without overwriting project truth."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from quenching.design.build import PRODUCT_SECTIONS, compute_build, write_build
from quenching.design.markdown import split_h2
from quenching.design.model import DesignError


def payload_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "assets" / "design" / "brand-pack"
        if candidate.is_dir():
            return candidate
    raise DesignError("installed plugin does not carry assets/design/brand-pack")


def align_plan(root: Path, product: dict[str, Any] | None = None, *,
               design_winner: str | None = None,
               product_reviewed: bool = False) -> dict[str, Any]:
    root = root.resolve()
    pack = payload_root()
    missing: list[str] = []
    preserved: list[str] = []
    for source in _pack_files(pack):
        relative = source.relative_to(pack)
        target = root / relative
        (preserved if target.exists() else missing).append(relative.as_posix())
    product_target = root / "docs" / "vision" / "product.md"
    has_product = _has_product_source(root)
    if product and not product_target.exists() and not has_product:
        missing.append("docs/vision/product.md")
    design_text = _read_text(root / "DESIGN.md")
    product_text = _read_text(root / "PRODUCT.md")
    external_design = bool(design_text) and not _matches_generated(root, "DESIGN.md", design_text)
    external_product = bool(product_text) and not _matches_generated(root, "PRODUCT.md", product_text)
    blockers = []
    if not (root / "docs" / "index.md").is_file():
        blockers.append("an installed /docs/ bundle is required before the design front")
    if not has_product and not product:
        blockers.append("confirmed product facts are required to project PRODUCT.md")
    if external_design and design_winner not in {"import", "build"}:
        blockers.append(
            "an external DESIGN.md requires an explicit source winner: import or build"
        )
    if external_product and not (product_reviewed or product):
        blockers.append(
            "an external PRODUCT.md must be reviewed into OKF before it can be replaced"
        )
    return {
        "root": str(root),
        "missing": sorted(missing),
        "preserved": sorted(preserved),
        "blockers": blockers,
        "ready": not blockers,
        "externalDesign": external_design,
        "externalProduct": external_product,
        "designWinner": design_winner,
        "productReviewed": product_reviewed or bool(product),
    }


def align_write(root: Path, product: dict[str, Any] | None = None, *,
                design_winner: str | None = None,
                product_reviewed: bool = False) -> dict[str, Any]:
    root = root.resolve()
    before = align_plan(
        root, product, design_winner=design_winner, product_reviewed=product_reviewed
    )
    if before["blockers"]:
        raise DesignError("; ".join(before["blockers"]))
    pack = payload_root()
    written: list[str] = []
    for source in _pack_files(pack):
        relative = source.relative_to(pack)
        target = root / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Assets may include licensed binary fonts as well as text (SVG, Markdown,
        # JSON). Copy bytes by default so alignment never corrupts a font or other
        # opaque brand file; the token timestamp is the one textual exception.
        content = source.read_bytes()
        if relative.as_posix() == ".design/tokens.json":
            timestamp = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            content = content.decode("utf-8").replace("__GENERATED_AT__", timestamp).encode("utf-8")
        target.write_bytes(content)
        written.append(relative.as_posix())
    product_target = root / "docs" / "vision" / "product.md"
    if product and not product_target.exists() and not _has_product_source(root):
        product_target.parent.mkdir(parents=True, exist_ok=True)
        product_target.write_text(_product_document(product), encoding="utf-8", newline="\n")
        written.append("docs/vision/product.md")
    imported: list[str] = []
    if before["externalDesign"] and design_winner == "import":
        # Preserve the external proposal until the DTCG source exists, then fold its portable
        # subset before the first build is allowed to overwrite DESIGN.md.
        from quenching.design.importer import import_design

        imported = import_design(root, write=True)["changed"]
    generated = write_build(compute_build(root))
    return {
        "ok": True,
        "root": str(root),
        "written": sorted(written),
        "generated": generated,
        "imported": imported,
        "designWinner": design_winner,
        "productReviewed": before["productReviewed"],
        "preserved": before["preserved"],
    }


def read_product(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DesignError(f"cannot read product facts from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DesignError("product facts must be one JSON object keyed by PRODUCT.md headings")
    return value


def _has_product_source(root: Path) -> bool:
    bundle = root / "docs"
    for home in (bundle / "vision", bundle / "standards" / "platform"):
        if not home.is_dir():
            continue
        for path in home.rglob("*.md"):
            # OKF indexes describe a home; they are not product records.  Do
            # not let an incidental `## Platform` in an index make align skip
            # the confirmed-product gate while the projection later ignores
            # that same index.
            if path.name == "index.md":
                continue
            try:
                platform = split_h2(path.read_text(encoding="utf-8")).get("Platform", "").strip()
            except OSError:
                continue
            if platform in {"web", "ios", "android", "adaptive"}:
                return True
    return False


def _pack_files(pack: Path) -> list[Path]:
    return sorted(path for path in pack.rglob("*") if path.is_file())


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _matches_generated(root: Path, relative: str, actual: str) -> bool:
    """Provenance is a byte claim, not a comment that a manual edit can leave behind."""
    if "GENERATED by `cq design build`" not in actual and "GERADO por `cq design build`" not in actual:
        return False
    try:
        expected = compute_build(root).outputs[relative]
    except (DesignError, KeyError):
        return False
    return actual == expected


def _product_document(product: dict[str, Any]) -> str:
    platform = str(product.get("Platform") or "").strip()
    if platform not in {"web", "ios", "android", "adaptive"}:
        raise DesignError("product facts need Platform = web, ios, android, or adaptive")
    required = {"Users", "Product Purpose", "Positioning"}
    missing = sorted(key for key in required if not str(product.get(key) or "").strip())
    if missing:
        raise DesignError(f"product facts need confirmed values for {', '.join(missing)}")
    today = dt.date.today().isoformat()
    lines = [
        "---", "type: vision", "title: Product record",
        "description: Confirmed product truth projected into PRODUCT.md for design consumers",
        "resource: PRODUCT.md", "tags: [product, design]", f"timestamp: {today}",
        "audience: both", "authority: background",
        "source: confirmed product interview conducted by /quenching-design align",
        "maintainer: project", "---", "", "# Product record",
    ]
    for heading in PRODUCT_SECTIONS:
        value = product.get(heading)
        if value is None or not str(value).strip():
            continue
        lines.extend(["", f"## {heading}", "", str(value).strip()])
    return "\n".join(lines).rstrip() + "\n"
