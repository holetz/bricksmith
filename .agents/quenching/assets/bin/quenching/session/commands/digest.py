"""`digest` — one command's evidence: counts, redundant reads, corrections.

Moved verbatim out of the pre-refactor session script."""
from __future__ import annotations

import json
from pathlib import Path

from quenching.common.output import FINDINGS, OK, refuse
from quenching.session.evidence import assign_corrections, clip, digest_command
from quenching.session.model import normalize
from quenching.session.parse import read_session, silence_refusal
from quenching.session.transcript import resolve_transcript


def cmd_digest(args) -> int:
    path, how = resolve_transcript(args.transcript, Path.cwd().resolve())
    if path is None:
        return refuse({"code": "se-no-transcript", "message": how}, args.json)

    model = read_session(path)
    model_objects = model.pop("_commands")
    objects = model_objects
    corrections = model["corrections"]

    silent = silence_refusal(model)
    if silent:
        return refuse(silent, args.json)

    if args.command:
        want = normalize(args.command)
        objects = [c for c in objects if c.name == want]
        if not objects:
            return refuse({"code": "se-unknown-command",
                           "message": f"no command named {want!r} in this transcript; "
                                      f"found {', '.join(c.name for c in model_objects)}"},
                          args.json)

    # Assignment runs over EVERY command in the session, not just the ones being digested:
    # a `--command` filter must not silently hand one command a turn that belonged to the
    # stage running next to it.
    attached, unassigned = assign_corrections(corrections, model_objects)
    digested = [digest_command(c, attached[c.name], args.max_quote, args.cap)
                for c in objects]

    payload = {
        "ok": bool(digested),
        "transcript": model["transcript"],
        "resolvedBy": how,
        "session": model["session"],
        "lines": model["lines"],
        "unparsed": model["unparsed"],
        "anomalies": model["anomalies"],
        "unattributedToolCalls": model["unattributedToolCalls"],
        "maxQuote": args.max_quote,
        "commands": digested,
        "unassignedCorrections": [{"line": c["line"], "kind": c["kind"],
                                   "quote": clip(c["text"], args.max_quote)}
                                  for c in unassigned[:args.cap]],
    }

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"transcript: {path}  ({how})   lines={payload['lines']}"
              f"  unparsed={payload['unparsed']}")
        for a in payload["anomalies"]:
            print(f"  ! {a['code']} line {a['line']}: {a['message']}")
        for c in digested:
            n = c["counts"]
            print(f"\n{c['command']}  [{', '.join(c['entryForms'])}]"
                  f"  lines {c['attributedRun']['firstLine']}-{c['attributedRun']['lastLine']}"
                  f"{'' if c['attributedRun']['closed'] else '  [UNCLOSED]'}")
            print(f"  tool calls: {n['toolCalls']}"
                  f"  | files read: {n['distinctFilesRead']}"
                  f"  | files written: {n['distinctFilesWritten']} ({n['writes']} writes)"
                  f"  | redundant read targets: {n['repeatedReadTargets']}"
                  f"  | repeated shell: {n['repeatedShellCommands']}"
                  f"  | interrupts: {n['interrupts']}"
                  f"  | interjections: {n['interjections']}")
            if c["tools"]:
                print("  tools: " + ", ".join(f"{k}={v}" for k, v in c["tools"].items()))
            for r in c["repeatedReads"]:
                if r["redundant"]:
                    print(f"  redundant read x{r['count']}: {r['target']}  {r['windows']}")
            for s in c["repeatedShell"]:
                print(f"  repeated shell x{s['count']}: {s['command']}")
            for k in c["corrections"]:
                print(f"  {k['kind']} ({k['when']}) @ line {k['line']}: {k['quote']}")
            # A capped list that does not say it was capped reads as a complete one.
            for key, n in sorted(c.get("truncated", {}).items()):
                print(f"  … {n} more {key} not shown (--cap {args.cap})")
    return FINDINGS if payload["anomalies"] else OK
