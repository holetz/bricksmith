"""Write one community artifact from its pinned template, never over an existing file.

Upstream templates are byte-identical to their source; the only change is the substitution of the
placeholders ``sources.json`` declares for that template.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from quenching.opensource.aggregate import TEMPLATES, load_sources


class Refusal(Exception):
    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.payload = {"code": code, "message": message, **extra}


def _entry(artifact: str, values: dict[str, str], sources: dict[str, Any]) -> dict[str, Any]:
    entries = [e for e in sources["templates"] if e["artifact"] == artifact]
    if not entries:
        known = sorted({e["artifact"] for e in sources["templates"]})
        raise Refusal("os-artifact-unknown", f"no template for artifact {artifact!r}", known=known)
    if artifact != "license":
        return entries[0]
    spdx = values.get("spdx")
    for entry in entries:
        if entry.get("spdx") == spdx:
            return entry
    raise Refusal("os-license-unvendored", f"no vendored license text for SPDX id {spdx!r}",
                  vendored=sorted(e["spdx"] for e in entries))


def apply(root: str, artifact: str, values: dict[str, str], dry_run: bool = False,
          directory: Path = TEMPLATES) -> dict[str, Any]:
    entry = _entry(artifact, values, load_sources(directory))
    placeholders = entry.get("placeholders", {})
    selector = {"spdx"} if artifact == "license" else set()
    unknown = sorted(set(values) - set(placeholders) - selector)
    if unknown:
        raise Refusal("os-var-unknown", f"{artifact} declares no placeholder {unknown}",
                      declared=sorted(placeholders))
    missing = sorted(set(placeholders) - set(values))
    if missing:
        raise Refusal("os-var-missing", f"{artifact} needs --var for {missing}", missing=missing)
    content = (directory / entry["path"]).read_bytes()
    if entry.get("sha256") and hashlib.sha256(content).hexdigest() != entry["sha256"]:
        raise Refusal("os-template-drift", f"pinned template differs from {entry.get('url')}",
                      path=entry["path"])
    for key, token in placeholders.items():
        content = content.replace(token.encode("utf-8"), values[key].encode("utf-8"))
    target = os.path.join(root, entry["target"])
    if os.path.lexists(target):
        raise Refusal("os-target-exists", f"{entry['target']} already exists; apply never overwrites",
                      target=entry["target"])
    if not dry_run:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "xb") as handle:
            handle.write(content)
    supplied = sorted(set(values) & set(placeholders))
    warnings = [{"code": "os-var-language-unverified", "severity": "info", "placeholders": supplied,
                 "message": "Owner-supplied placeholder prose must be English; values are not language-verified."}
                ] if supplied else []
    return {"ok": True, "artifact": artifact, "target": entry["target"], "source": entry["path"],
            "url": entry.get("url"), "language": entry["language"], "contentVerified": False,
            "substituted": sorted(placeholders), "warnings": warnings, "dryRun": dry_run,
            "written": not dry_run}
