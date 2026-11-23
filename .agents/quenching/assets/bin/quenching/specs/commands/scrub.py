"""`cq specs scrub` — the specs already in the store, judged by the leak gate.

Without `--apply` nothing is written: it lists every spec carrying an absolute path that has a
known relative form, and every spec matching a generic pattern or the denylist. `--apply` writes
only the path normalization, in one write where the backend batches, and never touches a
denylist or generic hit: those have no relative form, and removing them is a human's edit."""
from __future__ import annotations

from quenching.common.leak import denylist_path, normalize_paths, patterns, scan_text
from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter


def scrub_report(backend, root: str) -> tuple[list[dict], list[tuple[dict, str]]]:
    """One row per spec that needs attention, and the `(info, normalized text)` writes the
    path rows would land."""
    pats = patterns(require_denylist=False)
    ids = [row["id"] for row in backend.list_specs()]
    rows, writes = [], []
    for spec_id, info in backend.read_specs(ids).items():
        if info is None:
            continue
        clean = normalize_paths(info["text"], root)
        findings = scan_text(f"spec {spec_id}", clean, pats)
        if clean == info["text"] and not findings:
            continue
        rows.append({"id": info["id"], "paths": clean != info["text"],
                     "findings": [f.as_dict() for f in findings]})
        if clean != info["text"]:
            writes.append((info, clean))
    return rows, writes


def cmd_scrub(args, root: str, out: Emitter) -> int:
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    rows, writes = scrub_report(backend, root)
    if args.apply and writes:
        if len(writes) > 1 and hasattr(backend, "write_specs"):
            backend.write_specs(writes)
        else:
            for info, text in writes:
                backend.write_spec(info, text)
    flagged = [row for row in rows if row["findings"]]
    denylist = denylist_path()
    payload = {"ok": True, "applied": bool(args.apply), "normalized": len(writes) if args.apply else 0,
               "denylist": "present" if denylist.is_file() else "absent", "specs": rows}
    human = "".join(
        f"{row['id']}: {'absolute paths' if row['paths'] else ''}"
        f"{'; ' if row['paths'] and row['findings'] else ''}"
        + "; ".join(f"[{f['label']}] line {f['line']}" for f in row["findings"]) + "\n"
        for row in rows)
    human += (f"{len(writes)} spec(s) normalized; " if args.apply else
              f"{len(writes)} spec(s) with paths to normalize (--apply writes them); ")
    human += f"{len(flagged)} spec(s) with a leak to remove by hand; denylist {payload['denylist']}"
    out.emit(args.json, payload, human)
    return 1 if flagged or (writes and not args.apply) else 0
