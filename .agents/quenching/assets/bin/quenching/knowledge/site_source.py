"""Materialize the bounded source tree that Zensical is allowed to read.

The OKF bundle is a knowledge store, not automatically a public documentation site. Zensical
0.0.57 builds every Markdown file below ``docs_dir`` even when it is absent from ``nav`` and does
not support ``exclude_docs``. The site therefore reads this generated sibling tree instead of the
bundle itself. The tree contains hard links when the filesystem permits them, so staging does not
duplicate large source files.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from quenching.knowledge.schema import EXEMPT

# Reader-facing and durable project knowledge homes that may enter the site source. Raw data
# catalogues and external research are intentionally not in this allow-list. New homes stay out
# until the publication contract and this allow-list are updated deliberately.
PUBLISHED_HOMES = (
    "tutorials",
    "how-to",
    "documentation",
    "project",
    "standards",
    "concepts",
    "vision",
    "decisions",
)
EXCLUDED_HOMES = ("catalog", "external")
ROOT_FILES = ("index.md", "glossary.md")
ROOT_ASSETS = "assets"
MANIFEST_NAME = ".quenching-site-source.json"
_MARKDOWN_LINK = re.compile(r"(?P<image>!?)[\[](?P<label>[^\]\n]*)\]\((?P<target>[^)\n]+)\)")


def _link_target(target: str, source_file: Path, source_root: Path,
                 selected: set[str]) -> str | None:
    """Return a staged relative target, or ``None`` for external/fragment links."""
    destination = target.strip().split(None, 1)[0].strip("<>")
    if not destination or "://" in destination or destination.startswith(("#", "mailto:", "//")):
        return None
    destination = destination.split("#", 1)[0].split("?", 1)[0]
    if not destination:
        return None
    if destination.startswith("/"):
        parts = list(PurePosixPath(posixpath.normpath(destination)).parts)
        if parts and parts[0] == "/":
            parts.pop(0)
        if parts[:1] == ["docs"]:
            parts = parts[1:]
        else:
            return ""
        relative = PurePosixPath(*parts).as_posix()
    else:
        candidate = Path(os.path.normpath(str(source_file.parent / destination)))
        try:
            relative = candidate.relative_to(source_root).as_posix()
        except ValueError:
            return ""
    if relative == ".":
        relative = "index.md"
    if relative in selected:
        return relative
    if f"{relative}.md" in selected:
        return f"{relative}.md"
    if f"{relative}/index.md" in selected:
        return f"{relative}/index.md"
    return ""


def _sanitize_markdown(text: str, source_file: Path, source_root: Path,
                       selected: set[str]) -> str:
    """Remove links whose local destinations are absent from the published tree."""
    output = []
    fenced: str | None = None
    changed = False
    for line in text.splitlines(keepends=True):
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)[0]
            if fenced is None:
                fenced = marker
            elif fenced == marker:
                fenced = None
            output.append(line)
            continue
        if fenced is None:
            def replace(match: re.Match[str]) -> str:
                nonlocal changed
                destination = _link_target(match.group("target"), source_file, source_root, selected)
                if destination is None or destination:
                    return match.group(0)
                changed = True
                return match.group("label")

            line = _MARKDOWN_LINK.sub(replace, line)
        output.append(line)
    return "".join(output) if changed else text


def _copy_file(source: Path, destination: Path, source_root: Path,
               selected: set[str]) -> None:
    """Copy one regular file without duplicating bytes when hard links are available."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == ".md":
        try:
            text = source.read_text(encoding="utf-8")
            sanitized = _sanitize_markdown(text, source, source_root, selected)
        except (OSError, UnicodeDecodeError):
            sanitized = None
        if sanitized is not None and sanitized != text:
            destination.write_text(sanitized, encoding="utf-8")
            stat = source.stat()
            os.utime(destination, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def _copy_tree(source: Path, destination: Path, source_root: Path,
               selected: set[str]) -> None:
    """Copy a selected subtree while pruning private/dot sidecars and symlinks."""
    for dirpath, dirnames, filenames in os.walk(source, followlinks=False):
        current = Path(dirpath)
        relative = current.relative_to(source)
        target_dir = destination / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        dirnames[:] = [
            name for name in dirnames
            if not name.startswith((".", "_"))
            and not (current / name).is_symlink()
        ]
        for name in filenames:
            source_file = current / name
            if name in EXEMPT or name.startswith((".", "_")) or source_file.is_symlink():
                continue
            _copy_file(source_file, target_dir / name, source_root, selected)


def _selected_files(source: Path) -> list[Path]:
    """List the files represented by the staged tree, without reading their bodies."""
    selected: list[Path] = []
    for relative in ROOT_FILES:
        path = source / relative
        if path.is_file() and not path.is_symlink():
            selected.append(path)
    assets = source / ROOT_ASSETS
    if assets.is_dir() and not assets.is_symlink():
        selected.extend(
            path for path in assets.rglob("*")
            if path.is_file() and not path.is_symlink()
            and path.name not in EXEMPT
            and not any(part.startswith((".", "_")) for part in path.relative_to(source).parts)
        )
    for home in PUBLISHED_HOMES:
        path = source / home
        if path.is_dir() and not path.is_symlink():
            selected.extend(
                candidate for candidate in path.rglob("*")
                if candidate.is_file() and not candidate.is_symlink()
                and candidate.name not in EXEMPT
                and not any(part.startswith((".", "_"))
                            for part in candidate.relative_to(source).parts)
            )
    return sorted(selected)


def _manifest(source: Path) -> dict:
    """Return a cheap metadata manifest; no selected file body is loaded."""
    files = []
    for path in _selected_files(source):
        stat = path.stat()
        files.append({
            "path": path.relative_to(source).as_posix(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        })
    return {"version": 1, "files": files}


def _destination_manifest(destination: Path) -> tuple[dict, list[str]]:
    """Describe what Zensical can actually read, including unexpected symlinks."""
    files = []
    symlinks = []
    for dirpath, dirnames, filenames in os.walk(destination, followlinks=False):
        current = Path(dirpath)
        kept_dirs = []
        for name in dirnames:
            path = current / name
            if path.is_symlink():
                symlinks.append(path.relative_to(destination).as_posix())
            else:
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in filenames:
            path = current / name
            relative = path.relative_to(destination).as_posix()
            if relative == MANIFEST_NAME:
                continue
            if path.is_symlink():
                symlinks.append(relative)
                continue
            if path.is_file():
                stat = path.stat()
                files.append({
                    "path": relative,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                })
    return {"version": 1, "files": sorted(files, key=lambda item: item["path"])}, sorted(symlinks)


def _owned(destination: Path) -> bool:
    """Only replace a directory that this command created."""
    return (destination / MANIFEST_NAME).is_file()


def stage_site_source(source: str | Path, destination: str | Path) -> dict:
    """Atomically stage the allowed subset of ``source`` into ``destination``."""
    raw_destination = Path(destination)
    if raw_destination.is_symlink():
        raise ValueError(f"refusing symlink site source: {destination}")
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if not source_path.is_dir():
        raise ValueError(f"site source is not a directory: {source}")
    if destination_path == source_path or destination_path.is_relative_to(source_path):
        raise ValueError("site source destination must be outside the bundle")
    if destination_path.exists() and not _owned(destination_path):
        raise ValueError(
            f"refusing to replace unowned site source: {destination}; "
            f"remove it or add {MANIFEST_NAME} only after review"
        )

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination_path.name}-", dir=destination_path.parent))
    try:
        selected = {path.relative_to(source_path).as_posix() for path in _selected_files(source_path)}
        for relative in ROOT_FILES:
            path = source_path / relative
            if path.is_file() and not path.is_symlink():
                _copy_file(path, temporary / relative, source_path, selected)
        assets = source_path / ROOT_ASSETS
        if assets.is_dir() and not assets.is_symlink():
            _copy_tree(assets, temporary / ROOT_ASSETS, source_path, selected)
        for home in PUBLISHED_HOMES:
            path = source_path / home
            if path.is_dir() and not path.is_symlink():
                _copy_tree(path, temporary / home, source_path, selected)

        source_manifest = _manifest(source_path)
        staged_manifest, _ = _destination_manifest(temporary)
        manifest = {
            "version": 2,
            "source_files": source_manifest["files"],
            "files": staged_manifest["files"],
        }
        (temporary / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if destination_path.exists():
            shutil.rmtree(destination_path)
        os.replace(temporary, destination_path)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    return {
        "source": str(source_path),
        "destination": str(destination_path),
        "files": len(manifest["files"]),
        "bytes": sum(item["size"] for item in manifest["files"]),
        "excluded_homes": list(EXCLUDED_HOMES),
        "changed": True,
    }


def site_source_findings(source: str | Path, destination: str | Path) -> tuple[dict, list[dict]]:
    """Check the staged tree's cheap metadata contract without rebuilding it."""
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    payload = {"source": str(source_path), "destination": str(destination_path)}
    if not destination_path.is_dir():
        return payload, [{
            "path": str(destination),
            "code": "site-source-missing",
            "message": "generated site source is missing — stage it before building",
        }]
    marker = destination_path / MANIFEST_NAME
    if not marker.is_file():
        return payload, [{
            "path": str(destination),
            "code": "site-source-unowned",
            "message": f"site source has no {MANIFEST_NAME} marker",
        }]
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return payload, [{
            "path": str(marker),
            "code": "site-source-manifest-unreadable",
            "message": str(exc),
        }]
    desired = _manifest(source_path)
    actual, symlinks = _destination_manifest(destination_path)
    payload.update({
        "files": len(recorded.get("files", [])),
        "bytes": sum(item.get("size", 0) for item in recorded.get("files", [])),
        "excluded_homes": list(EXCLUDED_HOMES),
    })
    if (recorded.get("version") != 2
            or recorded.get("source_files") != desired.get("files")
            or recorded.get("files") != actual.get("files")
            or symlinks):
        return payload, [{
            "path": str(destination),
            "code": "site-source-stale",
            "message": "generated site source differs from the allowed bundle files",
        }]
    return payload, []
