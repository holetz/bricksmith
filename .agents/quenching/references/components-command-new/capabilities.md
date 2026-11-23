# The execution profile — strategic capability use, priced

Every the host lever a command, agent, or hook may use, each stated three ways: what it
buys, what it costs, and the default. Thresholds and finding codes live in `.agents/quenching/bin/cq components` and
`docs/standards/automation/skills.md` — this file names the code, never the number.

**The premise: a lever is bought, never collected.** A lever whose buy nobody can state is
bloat wearing a feature's name.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## The cost model — where each byte lands

**Fonte:** <https://code.claude.com/docs/en/skills>, <https://code.claude.com/docs/en/sub-agents>, <https://code.claude.com/docs/en/hooks>. A página oficial não declara versão para o modelo de custo; Conferido em 2026-10-11 contra the host 2.1.296.

<!-- rules -->
Five places a capability's cost can land, from most expensive to least:

| Where | What lands there | When it is paid |
| --- | --- | --- |
| **Always-on context** | every command description; every agent description | every request, every session, whether or not anything fires |
| **The session cache key** | the model, the effort level, the tool set | an inline switch invalidates the whole prompt cache — the next request recomputes every input token |
| **Per event** | every hook matched to that event | each time the event fires — per tool call for `PreToolUse`/`PostToolUse`, per turn for `UserPromptSubmit`/`Stop` |
| **Per invocation** | the command body; a forked context's fresh start | each run of that command |
| **On demand** | bundled references; `!`-command output; deferred MCP schemas | only when a step actually reaches for them |

Read every lever below against this table: name what it buys and which row it charges.

<!-- rationale -->
A quality gain charged to the always-on or
per-event rows is paid by every iteration in the repo, including the ones it never helps.

A validation hook that runs on the whole session
buys its quality gain at the price of every iteration in the project — including every
iteration that touches nothing it validates.

## The default profile

**Fonte:** <https://code.claude.com/docs/en/skills>. Campos de frontmatter de skill conforme a tabela de referência da página; sem versão declarada; Conferido em 2026-10-11 contra the host 2.1.296.

A minted command starts with **all levers off**:

- no `context: fork`, no `agent`, no `background`;
- no `model` or `effort` pin — it inherits the session's;
- no `hooks`, in frontmatter or in `settings.json`;
- `allowed-tools` scoped to what the steps run (`sk-unscoped-bash` otherwise);
- default invocation — no `user-invocable`, no `disable-model-invocation`, no `paths`.

This profile costs one description and nothing else, and most commands never need more.
Each departure is chosen at mint against the section that owns it, and the reason travels
into the plan (§The profile is part of the plan). "It might help" is not a reason;
"step 3 greps the whole repo and only the summary matters" is.

## `context: fork` — isolation, priced

**Fonte:** <https://code.claude.com/docs/en/skills>, <https://code.claude.com/docs/en/sub-agents>. Fork em segundo plano por padrão e `background: false` exigem the host v2.1.218 ou superior (declarado na página de skills); Explore e Plan pulam o AGENTS.md (página de sub-agents). Conferido em 2026-10-11 contra the host 2.1.296.

**What it does.** The command runs in a forked context: a fresh subagent whose prompt is the
command body, with **no conversation history**, its own cache, and — by default — background
execution. `agent: <name>` selects the runner (`general-purpose` default; `Explore` for
read-only work, and it skips `AGENTS.md`, keeping the fork small; or a custom
`.claude/agents/` definition). `background: false` makes the invoking turn wait for the
result.

**When it pays** — all three at once:

1. the body is **self-contained** — it needs no fact from the live conversation;
2. the run is **noisy** — greps, file reads, build output that would otherwise sit in the
   main context forever;
3. only the **summary** matters — the caller needs the report, not the trail.

Read-only status views, audits, research sweeps, and log analyses are the home cases. The
main conversation pays one invocation line and one result instead of the whole trail.

**When it is a trap:**

- **Any mid-flow gate.** A forked context cannot present a plan and wait for the OK, and
  cannot ask the user a question. `context: fork` beside an `AskUserQuestion` grant is
  incoherent by construction — `sk-fork-gate`, an error. This is the standing rule this
  plugin applies to its own sweeps, generalized.
- **Work that needs the conversation.** The fork starts blank; a command that reacts to what
  was just discussed forks away exactly the context it runs on.
- **Small tasks.** A fork costs a fresh context build on a cold cache; a two-tool-call
  command pays more to fork than to run inline.
- **Backgrounded writes.** A backgrounded fork's edits sit outside the session's
  checkpoints — they cannot be rewound, only reverted through git. A fork that writes should
  either run `background: false` or write to a branch.

## Model and effort — pin only what is mechanical

**Fonte:** <https://code.claude.com/docs/en/skills>, <https://code.claude.com/docs/en/sub-agents>. `effort` e `model` no frontmatter conforme a página de skills; sem versão declarada; Conferido em 2026-10-11 contra the host 2.1.296.

**`effort: low`** fits work that is genuinely mechanical — a single-field edit, a capture
with zero interrogation, a fixed-vocabulary classification the plan gate re-checks. Judgment
— ranking, classification that gates a deletion, plan authoring, anything a human confirms
*because it can be wrong* — inherits the session's model and effort, always.

**A cheaper `model` pin** (`haiku`, `sonnet`) belongs only on work whose failure is cheap and
caught: extraction and collection cross-checked by the orchestrator, bucketing of an
already-computed hit list. The standing prohibition: never on a step whose output authorizes
a deletion, or that lands in production code **without a protected oracle** — a
misclassification there becomes a wrong irreversible action, which is this plugin's
`import-memory` rule generalized. A protected oracle is an executable `verify:` whose test
files sit outside the task's `files:`, in a disposable worktree, with the projected context
at or under 80k (`.agents/quenching/bin/cq specs estimate --spec <id> --task <tid>` reports `smallTierEligible`);
there `haiku` may execute ONE task, and a failed `verify:` returns it to the session model.
Judgment never descends.

**The cache trap.** The model and the effort level are part of the session's prompt-cache
key. A pin on an *inline* command switches them for the turn and invalidates the whole
cache — the next request recomputes every input token, which on a long session can cost more
than the cheap model saved. Two placements are cache-safe, because they run in their own
context: a pin inside a `context: fork` command, and a pin in a subagent definition. Price an
inline pin against the session it interrupts, not against the single call it makes cheaper.

## Subagents — when delegation pays

**Fonte:** <https://code.claude.com/docs/en/sub-agents>. `omitClaudeMd` exige v2.1.271 ou superior (declarado na página). Sem versão declarada para os demais campos; Conferido em 2026-10-11 contra the host 2.1.296.

**The test:** the returned summary is much smaller than the work that produced it. A
repo-wide grep sweep, a many-file audit, a documentation read that ends in one table —
delegation converts a long trail into one result message, and parallel slices multiply that.
Delegation **wastes** when the output is as large as the work (a rewrite, a full-file
transform — the result lands in context anyway), when the task is one quick lookup, or when
the subagent must be handed so much context that the handoff costs what the isolation saves.

**The definition contract** (`.claude/agents/<name>.md` — the one Claude-only home: the host
2.1.312 does not read `.agents/agents/`, so no bridge applies): frontmatter `name`, `description`
(what it does **and when to invoke it** — the description is always-on context, the same
cost discipline as a command's), `tools` scoped to the narrowest set, and `model`/`effort`
under the pinning rules above (an agent's pin is cache-safe — it has its own context). Two
fields default safe and are opened deliberately: spawning (a subagent spawns subagents of its
own only when `Agent` is in its `tools`, within the depth limit; a type list in parentheses is
ignored in a subagent definition and honoured only for `claude --agent`, so in a subagent `Agent(...)` grants spawning of any type and restricts nothing — an agent that can
spawn agents can run away, so leave `Agent` out unless the job needs it), and `skills` (a preloaded skill's **full
body** persists for the agent's whole run — preload only what every run reads). `memory:` (scope `user`, `project` or `local`)
only when the agent genuinely learns across sessions; its memory file is a recurring cost on
every run.

**The verifier pattern.** A verifier
*inspects and reports; it never edits*. Its definition carries: the numbered check areas;
an explicit **"not checked here"** list (the false-positive control — an LLM verifier's
failure mode is flagging everything it can see); *if-present* guards on every optional
feature, so minimal artifacts pass clean; and a fixed report shape — status, critical
findings (breaks function or safety), warnings (works but suboptimal), passed checks,
each recommendation citing the standard it applies. Fixes re-enter through the command that
owns authoring, under its own confirmation — the same authoring boundary every conductor in
this plugin holds.

## Hooks — the scope ladder and the handler ladder

**Fonte:** <https://code.claude.com/docs/en/hooks>, <https://code.claude.com/docs/en/skills>. `timeout` (padrão 600 s para `command`) e `stop_hook_active` conforme a página de hooks; sem versão declarada; Conferido em 2026-10-11 contra the host 2.1.296.

<!-- rules -->
A hook is the only capability that charges **other people's operations**: it fires on events
the command that installed it does not own. The doctrine is therefore a ladder: **install
every hook at the narrowest scope that still catches what it exists to catch**, and climb
only with evidence.

**The scope ladder** — narrowest first:

1. **Skill-frontmatter `hooks:`** — declared in a command's own frontmatter, firing only
   while that command runs. The default home for a check tied to one workflow ("this
   command's writes are always validated"). Costs nothing to any other operation.
2. **A settings hook with an event + `matcher` + `if` gate** — fires only on the matched
   tool calls (`matcher: "Write|Edit"`, `if: Bash(git commit *)`). The home for a check tied
   to an *operation* ("every edit under `db/migrations/` is checked") rather than a workflow.
3. **A gated wide event** — `Stop`/`UserPromptSubmit` hooks made cheap by construction:
   dirty-gated by a marker file so a turn that touched nothing relevant costs one stat (the
   `.agents/quenching/bin/cq knowledge validate stopScan: "dirty"` precedent), or `once: true` for a per-session check.
4. **An unmatched session-wide hook** — the top of the ladder, and a finding
   (`sk-hook-unmatched`) unless its body states why nothing narrower catches its cases.

**The handler ladder** — cheapest first:

1. **`command`** — a deterministic script. Its fast path: on no-match it prints `{}` and the
   model never sees a token. Per-event cost is process time, never context.
2. **`prompt`** — one cheap-model judgment call. Seconds and tokens per firing.
3. **`agent`** — a full session-model inference per firing. On a per-tool-call event this is
   a finding (`sk-hook-llm-frequent`).

Climb the handler ladder only when the rung below cannot express the check — and when a
`prompt`/`agent` handler sits on a tool event, its matcher earns extra scrutiny. The two rungs
compose: a fast `command` hook that decides the deterministic 95% and a `prompt` hook on the
same matcher for the judgment tail keeps the expensive rung off the common path.

**Policy defaults**, regardless of rung:

- **Warn by default; block by consent.** A blocking hook (`PreToolUse` deny, `Stop` block)
  is chosen by the human at mint, per rule — never defaulted into.
- **Intrusive rules are born disabled.** A hook that can hold the session hostage (an
  unsatisfiable `Stop` block) ships with its gate off and its body saying when to enable it.
- **Fail open on infrastructure, fail closed on scope.** A crashed script or unparseable
  rule never blocks the session (exit 0, report the breakage); an event or tool the hook
  cannot classify matches **nothing**, not everything.
- **Every hook carries a `timeout`**, sized to its event's frequency — a per-tool-call hook
  has no business with the 10-minute default.
- **A `Stop` hook honors `stop_hook_active`** — the loop guard against a block that re-fires
  on its own consequence.
- **Matching hooks run in parallel and never see each other's output** — each hook is written
  independent, and two hooks that need an order are one hook.

**The cost claim.** Every hook enters its plan with one line of arithmetic: *which event ×
how often it fires in this repo × what the handler costs per firing × the fast-path cost on
no-match*. A hook that cannot state that line is not ready to install.

**A handler whose script may not be installed guards its own absence.** `python3 <missing-file>`
exits **2**, and exit 2 is the hook protocol's *error* code — on `PostToolUse` it feeds stderr
back as a failure. Lead with the guard, and keep the checker's own exit code
rather than swallowing it:

```yaml
command: 'test -f "<path>" || exit 0; python3 "<path>"'
```

`… || true` is the wrong shape. The
rule is one-directional: a **missing** handler is a no-op, a **failing** one still reports.

<!-- rationale -->
So a hook pointing at a tool some other command merely *offers* to install
turns every matched tool call into a reported error in exactly the repos that declined the offer,
and it does so where nobody wired it.

`… || true` hides the handler's real failures alongside its absence.

## Invocation-surface controls — what each costs

**Fonte:** <https://code.claude.com/docs/en/skills>. `disable-model-invocation`, `user-invocable` e `paths` conforme a tabela de frontmatter; sem versão declarada; Conferido em 2026-10-11 contra the host 2.1.296.

The invocation/permission decision table lives in `docs/standards/automation/skills.md`. The
price of each control:

- **`disable-model-invocation: true`** makes a command human-only — and removes its
  description from always-on context entirely. A utility the human runs by name costs
  nothing in always-on context. The price: no conductor can reach it, no spoken phrase
  routes to it. Never on a conductor stage.
- **`user-invocable: false`** hides the `/` entry; the model can still fire it and its
  description **still loads**. This is a routing control, not a cost control.
- **`paths:`** binds *autonomous* firing to glob patterns — the model reaches for the
  command only when the files in play match. The natural reinforcement for a domain-bound
  command: `/communications:teams:create` with `paths: ["communications/teams/**"]` stops
  costing spurious-invocation risk everywhere else in the repo. Typed invocation is
  unaffected.

## Dynamic context — pay at render, not in turns

**Fonte:** <https://code.claude.com/docs/en/skills>. Injeção `` !`comando` `` e `${CLAUDE_SKILL_DIR}` conforme a seção de contexto dinâmico; sem versão declarada; Conferido em 2026-10-11 contra the host 2.1.296.

A `` !`command` `` line in a command body runs **at render time**, before the model reads the
body, and its output lands inline. Used well, it converts turns into text: a workflow whose
step 1 is "run `git status`" or "run `.agents/quenching/bin/cq specs list --json`" can carry the answer into the
body instead of spending a tool round-trip on it — deterministic state, fetched once, at
exactly the moment it is fresh.

Two bounds keep it a lever instead of a leak:

- **Bound the output.** The rendered output is body-sized context, paid on every invocation.
  Inject summaries and `--json` views, never an unbounded dump (`git log` without `-n` is a
  context leak wearing a convenience's name).
- **Inject only what every run reads.** Output the body merely *might* use belongs to a step
  that fetches it on demand.

`${CLAUDE_SKILL_DIR}` (the command's own directory) and `.` (the repo
root) substitute in bodies and `allowed-tools`, and are how a command cites its assets
portably. Re-invocation is deduplicated by rendered content: a second run whose render is
identical costs a note, not the body again — one more reason to keep dynamic output stable.

## The profile is part of the plan

The mint's ONE plan shows the chosen profile — each non-default lever with its stated
reason — so the human confirms the capability spend along with the files. The sweeps read
the same page in reverse: `.agents/quenching/bin/cq components` reports the mechanically decidable slice
(`sk-fork-gate`, `sk-profile-value`, `sk-hook-unmatched`, `sk-hook-llm-frequent`,
`sk-agent-no-description`), and the doctrine audit reads what no parser can — a fork that
forks away its own context, a hook whose cost claim no longer holds, an agent whose
description routes nothing — each reported with the command that fixes it
(`/quenching-components command-new`, `/quenching-components hook-new`, `/quenching-components agent-new`), never rewritten in place.
