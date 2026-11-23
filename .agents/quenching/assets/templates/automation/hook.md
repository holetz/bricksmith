# Hook mold — the three wiring shapes, narrowest first

The mold `/quenching-components hook-new` copies from. A hook is not one file: it is a **wiring** (where it
is declared, which decides its scope) plus, for a `command` handler, a **script**. Pick the
shape from the scope ladder
(`.agents/quenching/references/components-command-new/capabilities.md` §Hooks) — the narrowest
rung that still catches what the hook exists to catch — and delete the other two.

## Shape 1 — skill-scoped (frontmatter `hooks:` in the owning command)

Fires **only while that command runs**. The default home for a check tied to one workflow.
Merged into the command's own frontmatter, never a separate file:

```yaml
hooks:
  PostToolUse:
    - matcher: "Write|Edit"
      hooks:
        - type: command
          command: 'test -f "./.claude/hooks/<name>.py" || exit 0; python3 "./.claude/hooks/<name>.py"'
          timeout: 10
```

The `test -f … || exit 0` guard is **not optional** when the script is one another command merely
*offers* to install: `python3 <missing-file>` exits 2, which the hook protocol reads as an error,
so an unguarded handler reports a failure on every matched call in any repo that never installed
it. Drop the guard only when the script ships with the hook and cannot be absent
([capabilities.md](../../references/components-command-new/capabilities.md) §Hooks).

## Shape 2 — operation-scoped (settings.json, event + matcher)

Fires on the matched tool calls, session-wide. The home for a check tied to an *operation*
("every edit under `db/migrations/`"), not a workflow. **MERGE** this block into the target's
`.claude/settings.json` — never overwrite the file:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ./.claude/hooks/<name>.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

A wide event (`Stop`, `UserPromptSubmit`) climbs the ladder only gated: a marker-file dirty
gate so an untouched turn costs one stat, or `once: true` for a per-session check. An
unmatched tool-event hook is `sk-hook-unmatched`; a `prompt`/`agent` handler on a tool event
is `sk-hook-llm-frequent` — both demand a stated reason in the plan.

## Shape 3 — the script (`.claude/hooks/<name>.py`)

Stdlib-only, and the fast path is the whole economics — on no-match it prints `{}` and the
model never sees a token:

```python
#!/usr/bin/env python3
"""<name> — <one line: what it checks, on which event, and its fast-path cost>."""
import json, sys

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        print("{}")
        return 0                      # fail OPEN on infrastructure — never block on a crash

    # fail CLOSED on scope: an event/tool this hook cannot classify matches NOTHING
    if payload.get("hook_event_name") != "<Event>":
        print("{}")
        return 0
    if payload.get("stop_hook_active"):   # Stop hooks only: the re-fire loop guard
        print("{}")
        return 0

    # <the deterministic check — decide from payload["tool_input"], the repo, a marker file>
    finding = None
    if finding is None:
        print("{}")                   # the zero-token fast path
        return 0

    # WARN by default (context for the model); BLOCK only when the human chose it at mint
    print(json.dumps({"systemMessage": finding}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

<!-- MOLD (quenching · scoped hook) → wired by /quenching-components hook-new under
     `/docs/standards/automation/hooks.md`, one plan → one OK.

     THE COST CLAIM. Every hook enters its plan with one line of arithmetic: which event ×
     how often it fires in this repo × handler cost per firing × the fast-path cost on
     no-match. A hook that cannot state that line is not ready to install.

     POLICY DEFAULTS: warn by default, block by consent; an intrusive rule (a Stop block) is
     born disabled with its body saying when to enable it; every hook carries a timeout sized
     to its event's frequency; matching hooks run in parallel and never see each other's
     output, so each is written independent. -->
