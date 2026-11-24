"""Declared build reading by task section, explicitly a heuristic rather than telemetry."""
from __future__ import annotations

import fnmatch
import json
import math
import re
import shlex
import sys
from pathlib import Path

from quenching.session.commands.context import BUDGETS
from quenching.specs.backends import open_backend
from quenching.specs.commands.output import Emitter, read_one
from quenching.specs.parse.tasks import _files_bad_annotation
from quenching.specs.parse.text import STANDARD_PATH_RE, strip_comments
from quenching.specs.schema import headings_for_moment


WORKER_BUDGET_TOKENS = BUDGETS["worker"]
BYTES_PER_TOKEN = 4
EXCLUDED = [
    "host/system/harness overhead", "conditional execute references", "tool/test output",
    "iteration count", "growing conversation history", "generated code",
]
_EXECUTE_REL = "skills/quenching-specs/references/execute.md"


def _shipped_execute_body(module: Path) -> Path:
    """The execute body beside this runtime: `quenching/skills/` in the source checkout, or
    `.agents/skills/` where `cq deploy` installed the runtime under `.agents/quenching/`."""
    candidates = [module.parents[n] / _EXECUTE_REL for n in (5, 6)]
    return next((c for c in candidates if c.is_file()), candidates[0])


EXECUTE_BODY = _shipped_execute_body(Path(__file__).resolve())
# Small-tier projection (ADR 0014). Fixed prompt of a Task sub-agent in this repository,
# measured 2026-10-17 on the first turn of general-purpose sub-agents (32065-33329 tokens).
EXECUTOR_FIXED_TOKENS = 33000
SMALL_TIER_TOKENIZER_FACTOR = 1.3  # Haiku 5.5 counts ~30% more tokens than Haiku 4.5
SMALL_TIER_CEILING = 80000  # ADR 0014: a Haiku ceiling, independent of the worker budget
IRREVERSIBLE_ROOTS = ("docs/", "knowledge/")


def _file_size(root: Path, entry: str, standard: bool = False) -> dict:
    annotation = _files_bad_annotation(entry)
    raw = re.sub(r"\s*\(new\)\s*$", "", entry).strip()
    # Standards use the bundle's canonical /docs/ spelling, not an OS absolute path.
    if standard:
        raw = raw.lstrip("/")
    path = Path(raw)
    if annotation or path.is_absolute() or not raw:
        return {"path": entry, "bytes": None, "reason": "not a repository-relative file"}
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        return {"path": entry, "bytes": None, "reason": "path escapes repository"}
    label = resolved.relative_to(root).as_posix()
    try:
        size = resolved.stat().st_size if resolved.is_file() else None
    except OSError:
        size = None
    return {"path": label, "bytes": size,
            **({"reason": "missing, new or unreadable file"} if size is None else {})}


def _is_generated_mirror(root: Path, entry: str) -> bool:
    """Find the nearest generated directory, honoring its module's authored files."""
    raw = re.sub(r"\s*\(new\)\s*$", "", entry).strip()
    if _files_bad_annotation(entry) or not raw or Path(raw).is_absolute():
        return False
    resolved = (root / raw).resolve()
    if not resolved.is_relative_to(root):
        return False
    if resolved.name == ".quenching-deploy.json":
        return True     # the deploy's own manifest: written whole by `cq deploy`, never read to build
    for directory in resolved.parents:
        if directory == root:
            break
        if not any((directory / marker).is_file()
                   for marker in (".generated-from.json", ".generated-files.json")):
            continue
        try:
            manifest = json.loads((root / "quenching/assets/modules.json")
                                  .read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            manifest = {}
        modules = manifest.get("modules", []) if isinstance(manifest, dict) else []
        if isinstance(modules, list):
            for module in modules:
                if (isinstance(module, dict)
                        and module.get("path") == directory.relative_to(root).as_posix()):
                    authored = module.get("authored", [])
                    return not (isinstance(authored, list)
                                and resolved.relative_to(directory).as_posix() in authored)
        return True
    return False


def _is_declared_write_only(label: str, write_only: list[str]) -> bool:
    """A repository-declared path: the relative label equal to, or `fnmatch`-ing, one entry."""
    return any(label == pattern or fnmatch.fnmatch(label, pattern) for pattern in write_only)


def _valid_paths(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def estimate_build(info: dict, root: str | Path, budget: int = WORKER_BUDGET_TOKENS,
                   preamble: Path = EXECUTE_BODY, write_only: list[str] | None = None) -> dict:
    """Load no task file contents: sizes and the backend's parsed document are enough."""
    root = Path(root).resolve()
    impact = strip_comments(info["sections"].get("Impact", {}).get("body", ""))
    standards = sorted(set(STANDARD_PATH_RE.findall(impact)))
    build_bytes = sum(len(info["sections"].get(h, {}).get("body", "").encode("utf-8"))
                      for h in headings_for_moment("build"))
    try:
        preamble_bytes = preamble.stat().st_size
        preamble_missing = False
    except OSError:
        preamble_bytes, preamble_missing = 0, True
    common_bytes = preamble_bytes + build_bytes
    groups: dict[int, list] = {}
    for task in info["tasks"]:
        groups.setdefault(task["section"], []).append(task)
    rows = []
    for section, tasks in groups.items():
        files: dict[str, dict] = {}
        for entry in standards:
            row = _file_size(root, entry, standard=True)
            files[row["path"]] = row
        standard_paths = set(files)
        for task in tasks:
            for entry in task["files"]:
                row = _file_size(root, entry)
                if row["path"] not in standard_paths:
                    if _is_declared_write_only(row["path"], write_only or []):
                        row["writeOnly"] = "declared by repo"
                    elif _is_generated_mirror(root, entry):
                        row["writeOnly"] = "generated mirror"
                files[row["path"]] = row
        unresolved = [{"path": row["path"], "reason": row["reason"]}
                      for row in files.values() if row["bytes"] is None and not row.get("writeOnly")]
        unresolved.extend({"task": task["id"], "reason": "no files declared"}
                          for task in tasks
                          if not task["files"] and not task["operational"])
        if preamble_missing:
            unresolved.append({"path": str(preamble), "reason": "execute preamble unavailable"})
        byte_count = common_bytes + sum(row["bytes"] or 0 for row in files.values()
                                        if not row.get("writeOnly"))
        tokens = (byte_count + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN
        sources = [(str(preamble), preamble_bytes), ("spec build sections", build_bytes)]
        sources += [(row["path"], row["bytes"] or 0) for row in files.values()
                    if not row.get("writeOnly")]
        top = sorted((s for s in sources if s[1] > 0), key=lambda s: (-s[1], s[0]))[:3]
        rows.append({"section": section, "tasks": [task["id"] for task in tasks],
                     "taskCount": len(tasks), "files": sorted(files.values(), key=lambda r: r["path"]),
                     "topContributors": [{"source": name, "bytes": size,
                                          "tokens": (size + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN}
                                         for name, size in top],
                     "bytes": byte_count, "estimatedTokens": tokens, "unresolved": unresolved,
                     "complete": not unresolved, "overBudget": tokens > budget})
    peak = max((row["estimatedTokens"] for row in rows), default=0)
    return {"kind": "estimated-declared-reading", "method": "ceil(UTF-8 bytes / 4)",
            "workerBudgetTokens": budget, "budgetSource": "S2 worker policy (spec 1629)",
            "preamble": {"source": str(preamble), "bytes": preamble_bytes,
                         "available": not preamble_missing}, "specBuildBytes": build_bytes,
            "standards": standards, "sections": rows, "maxSectionTokens": peak,
            "totalReadTokens": sum(row["estimatedTokens"] for row in rows),
            "complete": bool(rows) and all(row["complete"] for row in rows),
            "overBudget": peak > budget, "excluded": EXCLUDED,
            "uncertainty": "Incomplete build forecast: below budget never proves the build fits; "
                           "unknown files and excluded overhead can dominate. "
                           "totalReadTokens is reading volume, not a context peak."}


def _oracle_tokens(verify: str) -> list[str]:
    try:
        words = shlex.split(verify)
    except ValueError:
        words = verify.split()
    return [w.rstrip("/") for w in words if "/" in w or re.search(r"\.\w+$", w)]


def _is_oracle(path: str, tokens: list[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    if re.match(r"test_.*|.*_test\.\w+$|.*\.(test|spec)\.\w+$", name) or "/tests/" in f"/{path}":
        return True
    return any(path == t or path.startswith(t + "/") or fnmatch.fnmatch(name, t)
               or fnmatch.fnmatch(path, t) for t in tokens)


def estimate_task(info: dict, task_id: str, root: str | Path,
                  fixed: int = EXECUTOR_FIXED_TOKENS) -> dict | None:
    """Project one task's executor context and judge the small-tier conditions (ADR 0014)."""
    root = Path(root).resolve()
    task = next((t for t in info["tasks"] if t["id"] == task_id), None)
    if task is None:
        return None
    entries = list(task["files"]) + ([task["pattern"]] if task.get("pattern") else [])
    files: dict[str, dict] = {}
    for entry in entries:
        row = _file_size(root, entry)
        files[row["path"]] = row
    new = {_file_size(root, e)["path"] for e in task["files"] if e.rstrip().endswith("(new)")}
    unresolved = [{"path": r["path"], "reason": r["reason"]} for r in files.values()
                  if r["bytes"] is None and r["path"] not in new]
    byte_count = sum(r["bytes"] or 0 for r in files.values())
    read_tokens = (byte_count + BYTES_PER_TOKEN - 1) // BYTES_PER_TOKEN
    projected = math.ceil(round(read_tokens * SMALL_TIER_TOKENIZER_FACTOR, 6)) + fixed
    verify = task.get("verify") or ""
    tokens = _oracle_tokens(verify)
    declared = [_file_size(root, e)["path"] for e in task["files"]]
    oracle_in_files = sorted(p for p in declared if _is_oracle(p, tokens))
    irreversible = sorted(p for p in declared if p.lstrip("/").startswith(IRREVERSIBLE_ROOTS))
    protected = bool(verify) and bool(declared) and not oracle_in_files
    reasons = []
    if not verify:
        reasons.append("no verify: oracle")
    if not declared:
        reasons.append("no files: declared")
    if oracle_in_files:
        reasons.append("oracle inside files: " + ", ".join(oracle_in_files))
    if irreversible:
        reasons.append("writes under docs/ or knowledge/: " + ", ".join(irreversible))
    if unresolved:
        reasons.append("unresolved inputs")
    if projected > SMALL_TIER_CEILING:
        reasons.append(f"projected {projected} > {SMALL_TIER_CEILING}")
    return {"task": task_id, "files": sorted(files.values(), key=lambda r: r["path"]),
            "bytes": byte_count, "readTokens": read_tokens,
            "tokenizerFactor": SMALL_TIER_TOKENIZER_FACTOR, "executorFixedTokens": fixed,
            "projectedTokens": projected, "ceilingTokens": SMALL_TIER_CEILING,
            "verify": verify or None, "oracleProtected": protected,
            "oracleInFiles": oracle_in_files, "unresolved": unresolved,
            "smallTierEligible": not reasons, "reasons": reasons,
            "method": "ceil(UTF-8 bytes / 4) x 1.3 over files: + pattern:, plus executorFixedTokens"}


def _cmd_estimate_task(args, root: str, out: Emitter, info: dict) -> int:
    row = estimate_task(info, args.task, root)
    if row is None:
        return out.emit_err(args.json, {"code": "sp-unknown-task", "exit": 2,
                                       "message": f"spec {info['id']} has no task {args.task}"})
    payload = {"ok": True, "id": info["id"], "kind": "small-tier-projection", **row}
    verdict = "eligible" if row["smallTierEligible"] else "not eligible: " + "; ".join(row["reasons"])
    human = (f"{info['id']} task {args.task} — projected {row['projectedTokens']} tokens "
             f"(ceiling {row['ceilingTokens']}); oracle protected: {row['oracleProtected']}\n"
             f"  small tier: {verdict}")
    out.emit(args.json, payload, human)
    return 0


def cmd_estimate(args, root: str, out: Emitter) -> int:
    from quenching.common.config import setting
    if args.worker_budget is None:
        args.worker_budget, warning = setting(
            root, "shared", "contextBudgets.worker", WORKER_BUDGET_TOKENS,
            lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0)
        if warning:
            print(f"warning: {warning}", file=sys.stderr)
    write_only, warning = setting(root, "specs", "estimate.writeOnly", [], _valid_paths)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    if args.worker_budget <= 0:
        return out.emit_err(args.json, {"code": "sp-bad-worker-budget", "exit": 2,
                                       "message": "worker budget must be a positive token count"})
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    info, err = read_one(backend, args.spec, out)
    if err:
        return out.emit_err(args.json, err)
    if getattr(args, "task", None):
        return _cmd_estimate_task(args, root, out, info)
    payload = {"ok": True, "id": info["id"], **estimate_build(info, root, args.worker_budget,
                                                               write_only=write_only)}
    human = (f"{info['id']} — estimated declared reading (UTF-8 bytes / 4)\n"
             f"  max section: {payload['maxSectionTokens']} estimated tokens / "
             f"worker budget: {args.worker_budget}\n")
    for row in payload["sections"]:
        human += (f"  section {row['section']}: {row['estimatedTokens']} estimated tokens; "
                  f"{row['taskCount']} tasks; {len(row['unresolved'])} unresolved inputs\n")
        if row["topContributors"]:
            human += "    top: " + "; ".join(f"{c['source']} ({c['tokens']})"
                                             for c in row["topContributors"]) + "\n"
    human += ("  Known reading exceeds budget: propose epic decomposition before approval.\n"
              if payload["overBudget"] else "  No known-reading excess; this is NOT a build-fit verdict.\n")
    if not payload["complete"]:
        human += "  Declared input coverage is incomplete.\n"
    human += "  Excluded: " + "; ".join(payload["excluded"]) + "\n" + payload["uncertainty"]
    out.emit(args.json, payload, human)
    return 0
