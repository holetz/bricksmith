"""Measure the context a command's own reader will carry.

The measurement is a proxy, not a token counter: it uses UTF-8 bytes after frontmatter has been
removed, the same payload ``cq components read`` returns. A command body is always charged once;
each reference is charged either as the whole reference or as the section bodies named by ``§``.
Repeated citations are de-duplicated per command because a reader does not load the same file
twice in one turn.

The command is deliberately read-only. A ratchet is an input JSON document supplied by CI (or by
the repository's own proof tooling), and a regression is an error only when the measured total is
above its declared ``totalBytes`` ceiling. No baseline is invented or written by this verb.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from quenching.common.output import finding, refuse, report_findings
from quenching.components.commands.lint import (SURFACE_MISSING, resolve_lint_targets,
                                                     surface_root_of)
from quenching.components.sections import markdown_sections, select_sections
from quenching.components.surface import (body_after_frontmatter, load_surface, rel,
                                          surface_references)


REFERENCE_RE = re.compile(
    r"(\.agents/(?:quenching/references|skills/[a-z0-9-]+/references)/[A-Za-z0-9_./-]+\.md)"
)
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})", re.MULTILINE)
def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def _in_fence(text: str, position: int) -> bool:
    fence: str | None = None
    for match in FENCE_RE.finditer(text[:position]):
        marker = match.group(1)
        if fence is None:
            fence = marker[0] * len(marker)
        elif marker[0] == fence[0] and len(marker) >= len(fence):
            fence = None
    return fence is not None


def _citation_sections(text: str, start: int, end: int) -> list[str]:
    """Return section addresses attached to one path occurrence.

    A path is followed by its ``§`` addresses in either a prose paragraph or a shell command's
    continued line. Stop at the next paragraph or path so a later citation cannot inherit an
    earlier one's sections. The selector below still validates every address against the actual
    headings, so a permissive extraction cannot turn a dead address into a green measurement.
    """
    remainder = text[end:]
    next_path = REFERENCE_RE.search(remainder)
    paragraph = re.search(r"\n\s*\n", remainder)
    stops = [position for position in (
        next_path.start() if next_path else None,
        paragraph.start() if paragraph else None,
    ) if position is not None]
    attached = remainder[:min(stops)] if stops else remainder
    if _in_fence(text, start):
        # `--sections` arguments are commonly spread over several lines in one fenced shell
        # block. There, every address before the next path belongs to this reader call.
        return [fragment.strip() for fragment in attached.split("§")[1:] if fragment.strip()]

    # In prose a later `§` usually addresses the command's own step, not this reference. Keep
    # the marker on the path's line, or the immediately following line when the link wrapped.
    line_end = text.find("\n", end)
    line = text[end:line_end if line_end != -1 else len(text)]
    if "§" not in line:
        next_start = line_end + 1 if line_end != -1 else len(text)
        next_end = text.find("\n", next_start)
        next_line = text[next_start:next_end if next_end != -1 else len(text)]
        line = next_line if next_line.lstrip().startswith("§") else ""
    fragments = [fragment.strip() for fragment in line.split("§")[1:] if fragment.strip()]
    if len(fragments) == 1 and len(fragments[0].split()) <= 2:
        # A wrapped citation such as `§The` continues on the following prose line. Include one
        # continuation so the heading resolver can reach `The Handoff cadence` without treating
        # every later `§` in that paragraph as part of this reference.
        continuation_start = line_end + 1 if line_end != -1 else len(text)
        continuation_end = text.find("\n", continuation_start)
        continuation = text[continuation_start:continuation_end
                            if continuation_end != -1 else len(text)]
        if continuation and not continuation.lstrip().startswith("§"):
            fragments[0] = f"{fragments[0]} {continuation.strip()}"
    return fragments


def _section_candidates(fragment: str) -> list[str]:
    """Offer progressively shorter addresses for prose that explains an address.

    The shipped prose often writes ``§Subagents: ...`` or ``§The convergence contract: ...``:
    the heading is the stable prefix and the rest explains why this citation matters. A reader
    accepts a unique prefix, so try the longest token prefix that resolves and then fall back to
    the first stable words. Possessives and shell/markdown punctuation are syntax, not address
    content.
    """
    raw_tokens = fragment.replace('"', " ").replace("\\", " ").split()
    cleaned_tokens = fragment.replace("`", " ").replace('"', " ").replace("\\", " ").split()
    candidates = []
    for count in range(len(raw_tokens), 0, -1):
        for tokens in (raw_tokens, cleaned_tokens):
            candidate = " ".join(tokens[:count]).strip("\\\"'.,;:)]}")
            if candidate.endswith("'s"):
                candidate = candidate[:-2].rstrip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _reference_citations(command: dict) -> list[dict]:
    """Extract each reference path and the section addresses beside it from a command body."""
    body = command["body"]
    return [{"relpath": match.group(1),
             "sections": _citation_sections(body, match.start(), match.end())}
            for match in REFERENCE_RE.finditer(body)]


def _reference_index(root: str) -> dict[str, dict]:
    return {item["relpath"].replace(os.sep, "/"): item
            for item in surface_references(root)}


def measure_command(command: dict, references: dict[str, dict], root: str) -> tuple[dict, list[dict]]:
    """Measure one command and return its row plus unresolved citation findings."""
    whole: dict[str, dict] = {}
    sections: dict[tuple[str, str], dict] = {}
    findings: list[dict] = []
    for citation in _reference_citations(command):
        relpath = citation["relpath"]
        reference = references.get(relpath)
        if reference is None:
            findings.append(finding(
                "ct-cost-no-reference", "error",
                f"{command['command']} cites missing reference {relpath}",
                command=command["command"], path=relpath,
                remedy="repair the citation or add the reference before measuring cost"))
            continue
        if not citation["sections"]:
            whole[relpath] = {
                "path": relpath,
                "bytes": _bytes(reference["body"]),
            }
            continue
        heads = markdown_sections(reference["body"])
        addresses = []
        for fragment in citation["sections"]:
            selected = []
            for candidate in _section_candidates(fragment):
                selected, _ = select_sections(heads, [candidate])
                if selected:
                    break
            if selected:
                addresses.append(selected[0]["heading"])
            elif addresses:
                # A later `§` in prose can address the command's own step (for example `§2`)
                # rather than the cited reference. Once this citation has one valid address,
                # stop at that boundary instead of charging a false missing-section finding.
                break
            else:
                addresses.append(fragment)
                break
        selected, missing = select_sections(heads, addresses)
        for name in missing:
            findings.append(finding(
                "ct-cost-no-section", "error",
                f"{command['command']} cites absent section {name!r} in {relpath}",
                command=command["command"], path=relpath, section=name,
                remedy="use a heading present in the cited reference"))
        for heading in selected:
            key = (relpath, heading["heading"])
            sections[key] = {
                "path": relpath,
                "heading": heading["heading"],
                "bytes": _bytes(heading["body"]),
            }

    body_bytes = _bytes(command["body"])
    whole_rows = sorted(whole.values(), key=lambda row: row["path"])
    section_rows = sorted(sections.values(), key=lambda row: (row["path"], row["heading"]))
    total = body_bytes + sum(row["bytes"] for row in whole_rows + section_rows)
    row = {
        "command": command["command"],
        "path": rel(command["path"], root),
        "bodyBytes": body_bytes,
        "wholeReferences": whole_rows,
        "sections": section_rows,
        "totalBytes": total,
    }
    return row, findings


def measure_surface(root: str, commands: list[dict] | None = None) -> tuple[dict, list[dict]]:
    """Measure every command on a surface in invocation order. `commands` narrows the
    measured set to what the caller's target named; references always resolve on `root`."""
    surface = load_surface(root)
    if commands is not None:
        surface = {**surface, "commands": commands}
    references = _reference_index(root)
    rows, findings = [], []
    for command in surface["commands"]:
        row, command_findings = measure_command(command, references, root)
        rows.append(row)
        findings.extend(command_findings)
    payload = {
        "root": root,
        "commandCount": len(rows),
        "commands": rows,
        "totalBytes": sum(row["totalBytes"] for row in rows),
        "source": "UTF-8 bytes after frontmatter; references de-duplicated per command",
    }
    return payload, findings


def _ratchet_finding(path: str, current: int) -> tuple[dict | None, dict | None]:
    try:
        baseline = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        return None, finding("ct-cost-ratchet", "error", f"cannot read ratchet {path}: {exc}",
                             path=path, remedy="provide a readable JSON baseline")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, finding("ct-cost-ratchet", "error", f"ratchet {path} is not JSON: {exc}",
                             path=path, remedy="write a JSON object with numeric totalBytes")
    ceiling = baseline.get("totalBytes") if isinstance(baseline, dict) else None
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) or ceiling < 0:
        return None, finding("ct-cost-ratchet", "error",
                             f"ratchet {path} has no non-negative integer totalBytes",
                             path=path, remedy="declare the measured total as totalBytes")
    result = {"path": path, "baselineBytes": ceiling, "currentBytes": current,
              "within": current <= ceiling}
    if current > ceiling:
        return result, finding(
            "ct-cost-regression", "error",
            f"context cost grew from {ceiling} to {current} bytes",
            path=path, baselineBytes=ceiling, currentBytes=current,
            remedy="reduce the loaded context or update the baseline explicitly after review")
    return result, None


def cmd_cost(args, root: str) -> int:
    # The target the path names is measured, never the cwd surface: a root, a commands/
    # directory or one file, resolved exactly as `lint` resolves it.
    base, commands, _ = resolve_lint_targets(getattr(args, "path", None), root)
    if getattr(args, "path", None) and not commands:
        # Nothing to measure is a refusal, as in `lint`: a ratchet never approves a surface
        # that was not seen.
        target = os.path.abspath(args.path)
        return report_findings(args.json, f"components cost — {target}",
                               {"root": target, "commandCount": 0, "totalBytes": 0},
                               [finding("sk-no-commands", "error",
                                        f"no command file found under {target}",
                                        command=SURFACE_MISSING)], "command")
    root = surface_root_of(base, root) if getattr(args, "path", None) else root
    payload, findings = measure_surface(root, commands if getattr(args, "path", None) else None)
    if args.ratchet:
        ratchet, ratchet_finding = _ratchet_finding(args.ratchet, payload["totalBytes"])
        payload["ratchet"] = ratchet
        if ratchet_finding:
            findings.append(ratchet_finding)
    if findings:
        return report_findings(args.json, f"components cost — {root}", payload, findings,
                               "command")
    if args.json:
        print(json.dumps({"ok": True, **payload}, indent=2, ensure_ascii=False))
    else:
        print(f"components cost — {root}")
        print(f"  {payload['commandCount']} commands / {payload['totalBytes']:,} bytes")
        if args.ratchet:
            print(f"  ratchet: {payload['ratchet']['baselineBytes']:,} bytes — within ceiling")
    return 0
