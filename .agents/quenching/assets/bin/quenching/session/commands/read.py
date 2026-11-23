"""`list` — every command a session ran, with its tool-call span.

Moved verbatim out of the pre-refactor session script."""
from __future__ import annotations

import json
from pathlib import Path

from quenching.common.output import FINDINGS, OK, refuse
from quenching.session.parse import read_session, silence_refusal
from quenching.session.transcript import resolve_transcript


def cmd_list(args) -> int:
    path, how = resolve_transcript(args.transcript, Path.cwd().resolve())
    if path is None:
        return refuse({"code": "se-no-transcript", "message": how}, args.json)

    model = read_session(path)
    model.pop("_commands")
    model.pop("corrections")
    model["resolvedBy"] = how

    silent = silence_refusal(model)
    if silent:
        return refuse(silent, args.json)
    model["ok"] = True

    if args.json:
        print(json.dumps(model, indent=2, ensure_ascii=False))
    else:
        s = model["session"]
        print(f"transcript: {path}  ({how})")
        print(f"session:    {s['sessionId']}  cwd={s['cwd']}  branch={s['gitBranch']}")
        print(f"lines:      {model['lines']}  unparsed={model['unparsed']}"
              f"  unattributed tool calls={model['unattributedToolCalls']}")
        for a in model["anomalies"]:
            print(f"  ! {a['code']} line {a['line']}: {a['message']}")
        # A zero-command parse never reaches here — silence_refusal already exited 2.
        print(f"\n{len(model['commands'])} command(s):")
        for c in model["commands"]:
            run = c["attributedRun"]
            print(f"  {c['command']}")
            print(f"    entry: {', '.join(c['entryForms'])}"
                  f"  lines {run['firstLine']}-{run['lastLine']}"
                  f"  tool calls: {c['toolCalls']}"
                  f"{'' if run['closed'] else '  [UNCLOSED]'}")
            if not run["closed"]:
                print(f"    ! attribution never returned to {run['mayIncludeTurnsFrom']} — "
                      f"these counts are an upper bound")
            if c["tools"]:
                top = ", ".join(f"{k}={v}" for k, v in list(c["tools"].items())[:6])
                print(f"    tools: {top}")
            for inv in c["invocations"]:
                by = f" (invoked by {inv['invokedBy']})" if inv["invokedBy"] else ""
                arg = f" args={inv['args']!r}" if inv["args"] else ""
                print(f"    - {inv['form']} at line {inv['line']}{by}{arg}")
    return FINDINGS if model["anomalies"] else OK
