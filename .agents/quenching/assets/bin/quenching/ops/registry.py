"""Render the generated operations registry without touching authored prose."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from quenching.ops.model import EntryPoint, Inventory


REGISTRY_START = "<!-- quenching-ops-registry-start -->"
REGISTRY_END = "<!-- quenching-ops-registry-end -->"
REGISTRY_HASH = "quenching-ops-registry-sha256"
_REGISTRY_ZONE = re.compile(
    rf"{re.escape(REGISTRY_START)}.*?{re.escape(REGISTRY_END)}", re.DOTALL
)


def canonical_inventory(inventory: Inventory) -> bytes:
    """Serialize the inventory fields that make a registry row true."""
    payload = {
        "router": inventory.router.as_dict(),
        "entryPoints": [entry.as_dict() for entry in inventory.entry_points],
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def inventory_digest(inventory: Inventory) -> str:
    """Return the stable digest written into the generated registry."""
    return hashlib.sha256(canonical_inventory(inventory)).hexdigest()


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _entry_row(entry: EntryPoint) -> str:
    lifecycle = entry.lifecycle or "—"
    writes = "yes" if entry.writes_outside_repo else "no"
    flags = ", ".join(f"`{_cell(flag)}`" for flag in entry.flags) or "—"
    return (
        f"| `{_cell(entry.path)}` | {_cell(entry.purpose) or '—'} | "
        f"`{_cell(entry.invocation)}` | {lifecycle} | {writes} | {flags} |"
    )


def render_registry_block(inventory: Inventory) -> str:
    """Render the complete generated zone for an inventory."""
    lines = [
        REGISTRY_START,
        f"<!-- {REGISTRY_HASH} {inventory_digest(inventory)} -->",
        "| Entry point | Purpose | Invocation | Lifecycle | Writes outside repository | Flags |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    lines.extend(_entry_row(entry) for entry in inventory.entry_points)
    lines.append(REGISTRY_END)
    return "\n".join(lines)


class RegistryError(ValueError):
    """A registry document the generator cannot read or safely rewrite."""


def read_registry(path: Path) -> str:
    """Read a registry document; an absent file is empty, an unreadable one or invalid UTF-8 is a RegistryError."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except UnicodeDecodeError as exc:
        raise RegistryError(f"registry document is not valid UTF-8: {path}") from exc
    except OSError as exc:
        raise RegistryError(f"registry document cannot be read: {path} ({exc.strerror})") from exc


def render_registry_document(document: str, inventory: Inventory) -> str:
    """Replace only the generated zone, preserving every byte outside it."""
    block = render_registry_block(inventory)
    matches = list(_REGISTRY_ZONE.finditer(document))
    if len(matches) > 1:
        raise RegistryError("registry document has more than one generated zone")
    if matches:
        match = matches[0]
        return document[:match.start()] + block + document[match.end():]
    if not document:
        return block + "\n"
    return document.rstrip("\n") + "\n\n" + block + "\n"


def write_registry(path: Path, inventory: Inventory) -> bool:
    """Write a registry document and report whether its content changed."""
    document = read_registry(path)
    updated = render_registry_document(document, inventory)
    if updated == document:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return True
