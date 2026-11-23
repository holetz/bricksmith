"""`cq specs handoff`: the one handoff, with two doors.

A session's handoff is written by whoever can afford it: `/quenching-handoff` when the chat's
prompt cache is warm (the main model writes the judgement), and the `quenching-cache` plugin's
`/quenching-cache handoff` when it has expired (Haiku writes it, and the expired chat is never
resent). Both doors go through this verb, so both handoffs read the same and land in the same place.

`facts` is the half no model should write: the files the session edited, the commits it made, the
user's last real message, the branch and the spec that owns it — read from the session's own
transcript, exactly. `write` is the other half's door: it refuses a handoff without the required
sections (`REQUIRED`), names it `handoff-<date>-<focus-slug>.md`, stores it beside its spec on
the `quenching` branch (or under `_handoffs/` when no spec owns the branch), reads it back, pushes
when asked, and prints the direction block the next session starts from. A repository without a
git specs store keeps it under `.quenching/handoff/` and says so: the fallback is stated, never
silent, and any other refusal stays the failure it is.

The section list the two doors write is `quenching/skills/quenching-handoff/references/template.md`.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

from quenching.common.io import write_text
from quenching.specs.backends import open_backend
from quenching.specs.backends.base import BackendRefusal
from quenching.specs.commands.find import find_match
from quenching.specs.commands.output import Emitter, display_locator

CQ = ".agents/quenching/bin/cq"
REQUIRED = ("## Last Request", "## Next Actions")
FALLBACK_CODES = {"sp-artifact-unsupported", "sp-artifact-no-store"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
COMMIT = re.compile(r"^\[[\w./-]+(?: \(root-commit\))? ([0-9a-f]{7,})\] (.+)$", re.MULTILINE)
META = ("<command-name>", "<command-message>", "<local-command", "<system-reminder>",
        "<task-notification>", "Stop hook feedback", "[Request interrupted", "Caveat:")
LAST_MESSAGE_CHARS = 4000
LANGUAGE = re.compile(r"^Language:\s*([A-Za-z0-9-]+)", re.MULTILINE)

WORDS = {
    "pt": {"paste": "Cole numa sessão nova:",
           "read": "Leia o handoff em {locator} antes de agir: `{command}`.",
           "read_local": "Leia o handoff em {locator} antes de agir.",
           "owned": "Ele pertence à spec {spec}; siga com `/quenching-specs execute {spec}`.",
           "orphan": "nenhuma spec está atrelada; pergunte o que fazer.",
           "unsent": f"não enviado — {CQ} specs push",
           "local": "só local — este repositório não tem store git de specs"},
    "en": {"paste": "Paste into a new session:",
           "read": "Read the handoff at {locator} before acting: `{command}`.",
           "read_local": "Read the handoff at {locator} before acting.",
           "owned": "It belongs to spec {spec}; continue with `/quenching-specs execute {spec}`.",
           "orphan": "no spec is attached; ask what to do.",
           "unsent": f"not sent — {CQ} specs push",
           "local": "local only — this repository has no git specs store"},
}


def language(root: str) -> str:
    """`pt` or `en`: the conversation language the root harness declares (communication.md)."""
    for name in ("AGENTS.md", "CLAUDE.md"):
        try:
            found = LANGUAGE.search((Path(root) / name).read_text(encoding="utf-8"))
        except OSError:
            continue
        if found:
            return "pt" if found[1].lower().startswith("pt") else "en"
    return "en"


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    out = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")[:48].strip("-")
    return out or "next-session"


def _branch(root: str) -> str | None:
    run = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True,
                         text=True, encoding="utf-8", errors="replace", check=False)
    return (run.stdout.strip() or None) if run.returncode == 0 else None


def _spec_for(backend, branch: str | None) -> int | str | None:
    if backend is None or not branch:
        return None
    try:
        hit = find_match(backend, branch=branch, commit_subject=None, pr=None)
    except BackendRefusal:
        return None
    return hit["info"]["id"] if hit else None


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)]
    return []


def transcript_facts(path: Path, root: str) -> dict:
    """Edited files, commits and the last real user message, from a Claude Code transcript."""
    files: list[str] = []
    commits: list[str] = []
    last = None
    base = Path(root).resolve()
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            msg = rec.get("message") if isinstance(rec, dict) else None
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if rec.get("type") == "assistant" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use" \
                            and block.get("name") in EDIT_TOOLS:
                        target = (block.get("input") or {}).get("file_path") \
                            or (block.get("input") or {}).get("notebook_path")
                        if isinstance(target, str) and target:
                            p = Path(target)
                            try:
                                target = str(p.resolve().relative_to(base))
                            except (ValueError, OSError):
                                pass
                            if target not in files:
                                files.append(target)
            if rec.get("type") != "user" or rec.get("isMeta"):
                continue
            if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result"
                                                 for b in content):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        for text in _texts(block.get("content")):
                            for sha, subject in COMMIT.findall(text):
                                entry = f"{sha[:7]} {subject.strip()}"
                                if entry not in commits:
                                    commits.append(entry)
                continue
            text = "\n".join(_texts(content)).strip()
            if text and not text.startswith(META):
                last = text[:LAST_MESSAGE_CHARS]
    return {"filesModified": files, "commits": commits, "lastUserMessage": last}


def _facts(args, root: str, out: Emitter) -> int:
    from quenching.session.transcript import resolve_transcript   # the session pillar's resolver
    import os
    sid = args.session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    path, how = resolve_transcript(args.transcript or sid, Path(root))
    if path is None:
        return out.emit_err(args.json, {"code": "sp-handoff-no-transcript", "exit": 2,
                                        "message": f"no transcript to read facts from: {how}"})
    backend, _err = open_backend(root)
    branch = _branch(root)
    facts = transcript_facts(path, root)
    spec = _spec_for(backend, branch)
    obj = {"ok": True, "transcript": str(path), "resolvedBy": how, "branch": branch, "spec": spec,
           **facts}
    human = "\n".join([f"branch: {branch or '-'}", f"spec: {spec if spec is not None else '-'}",
                       "files modified:", *[f"  {f}" for f in facts["filesModified"] or ["none"]],
                       "commits:", *[f"  {c}" for c in facts["commits"] or ["none"]],
                       f"last user message: {(facts['lastUserMessage'] or '-')[:200]}"])
    out.emit(args.json, obj, human)
    return 0


def missing_sections(text: str) -> list[str]:
    heads = [line.strip() for line in text.splitlines()]
    return [need for need in REQUIRED if not any(h.startswith(need) for h in heads)]


def direction(lang: str, locator: str, command: str | None, spec, sent: bool, local: bool) -> dict:
    """The block the next session starts from: locator, the command that reads it, the prompt."""
    w = WORDS[lang]
    first = w["read_local"].format(locator=locator) if local \
        else w["read"].format(locator=locator, command=command)
    second = w["owned"].format(spec=spec) if spec is not None else w["orphan"]
    lines = ["## Handoff", "", locator, "", w["paste"], "", f"    {first}", f"    {second}"]
    if local:
        lines += ["", w["local"]]
    elif not sent:
        lines += ["", w["unsent"]]
    return {"prompt": f"{first} {second}", "block": "\n".join(lines)}


def _unique(backend, spec, name: str) -> str:
    taken = {row["name"] for row in backend.list_artifacts(spec)}
    if name not in taken:
        return name
    stem = name[:-3]
    n = 2
    while f"{stem}-{n}.md" in taken:
        n += 1
    return f"{stem}-{n}.md"


def _write(args, root: str, out: Emitter) -> int:
    try:
        text = Path(args.from_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return out.emit_err(args.json, {"code": "sp-handoff-unreadable", "exit": 2,
                                        "message": f"cannot read '{args.from_path}': {exc}"})
    missing = missing_sections(text)
    if missing:
        return out.emit_err(args.json, {
            "code": "sp-handoff-incomplete", "exit": 2, "missing": missing,
            "message": f"the handoff lacks {', '.join(missing)}; nothing was written"})
    lang = args.lang if args.lang in WORDS else language(root)
    when = args.date or date.today().isoformat()
    name = f"handoff-{when}-{slug(args.focus)}.md"
    backend, err = open_backend(root)
    if err:
        return out.emit_err(args.json, err)
    raw_spec = (args.spec or "").strip()
    spec = (int(raw_spec) if raw_spec.isdigit() else raw_spec) if raw_spec \
        else _spec_for(backend, _branch(root))
    local, sent, reason = backend is None, False, None
    locator = None
    if backend is not None:
        try:
            name = _unique(backend, spec, name)
            located = backend.write_artifact(spec, name, text)
            if backend.read_artifact(spec, name) != text:
                return out.emit_err(args.json, {"code": "sp-handoff-readback", "exit": 1,
                                                "message": f"{name} read back differs from what was written"})
            locator = display_locator(located, root)
        except BackendRefusal as refusal:
            if refusal.err.get("code") not in FALLBACK_CODES:
                raise
            local, reason = True, refusal.err.get("code")
    else:
        reason = "sp-artifact-no-store"
    if local:
        rel = f".quenching/handoff/{name}"
        (Path(root) / rel).parent.mkdir(parents=True, exist_ok=True)
        write_text(str(Path(root) / rel), text)
        locator, command = rel, None
    else:
        command = f"{CQ} specs artifact read" + (f" --spec {spec}" if spec is not None else "") \
            + f" --name {name}"
        if args.push and hasattr(backend, "push"):
            try:
                backend.push()
                sent = True
            except BackendRefusal as refusal:
                reason = refusal.err.get("code")
    shown = direction(lang, locator, command, spec, sent, local)
    obj = {"ok": True, "spec": spec, "name": name, "locator": locator, "read": command,
           "sent": sent, "local": local, "reason": reason, **shown}
    out.emit(args.json, obj, shown["block"])
    return 0


def cmd_handoff(args, root: str, out: Emitter) -> int:
    verb = getattr(args, "handoff_cmd", None)
    if verb == "facts":
        return _facts(args, root, out)
    if verb == "write":
        return _write(args, root, out)
    return out.emit_err(args.json, {"code": "sp-handoff-verb", "exit": 2,
                                    "message": "choose: facts or write"})


def add_parser(sub, add_json) -> None:
    sp = add_json(sub.add_parser("handoff", help="the session handoff both doors write: the facts "
                                                 "a transcript proves, and the stored, directed write"))
    hsub = sp.add_subparsers(dest="handoff_cmd", metavar="VERB")
    fp = add_json(hsub.add_parser("facts", help="edited files, commits, the last user message, the "
                                                "branch and its spec, read from the session transcript"))
    fp.add_argument("--session", help="the session id (default: CLAUDE_CODE_SESSION_ID, then the "
                                      "most recent session of this checkout)")
    fp.add_argument("--transcript", help="a transcript path, instead of a session id")
    wp = add_json(hsub.add_parser("write", help="store a handoff beside its spec, read it back and "
                                                "print the direction block"))
    wp.add_argument("--from", dest="from_path", metavar="PATH", required=True,
                    help="the handoff draft (under .quenching/handoff/)")
    wp.add_argument("--focus", default="next session", help="the next session's focus; names the file")
    wp.add_argument("--spec", help="the owning spec (default: the spec whose branch is checked out)")
    wp.add_argument("--push", action="store_true", help="send it with the store's pending writes")
    wp.add_argument("--lang", choices=sorted(WORDS), help="the block's words (default: AGENTS.md Language)")
    wp.add_argument("--date", help="YYYY-MM-DD (default: today)")
