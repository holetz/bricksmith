---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(python3:*), Bash(py:*), Read, Grep, Glob, Write, Edit, Task, AskUserQuestion
---

# /quenching-components command-eval — measure whether a skill teaches anything

## Resolving the tool

Resolve `.agents/quenching/bin/cq components` per
[align/tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool.

**Input**: optionally a skill name. If omitted, infer from context; if more than one is plausible,
put a decision sheet ([specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md) §1) and end the turn.

## Workflow

### 1. Select the skill and check its floor
Resolve the skill folder. Run `.agents/quenching/bin/cq components lint <skill-folder> --json`: an **error** means the skill
does not load as intended — report it and stop. Record a **warn** and continue.
**Done when:** one skill is named back to the user and `lint` has run.

### 2. Read the command as its own specification
Read the command file and every reference it points to. The description's promise —
*when it fires, and what exists when it finishes* — is what the cases must test. Note each
distinct branch the workflow can take; a branch with no case is untested surface.
**Done when:** every branch and every promise is listed.

### 3. Derive the cases
Per [components-command-eval/evaluation.md](.agents/skills/quenching-components/references/components-command-eval/evaluation.md) §Writing cases: one case per branch, each
a realistic user prompt plus assertions about the **observable outcome**. Include at
least one case the skill should **decline** (its `Not for:` boundary), and the should-trigger /
should-not-trigger prompts step 7 needs.
**Done when:** every branch has a case and every case has at least one observable assertion.

### 4. Present ONE plan → gate on the OK
Show: the skill, the case list with each case's branch and assertions, the arm count
(cases × 2), and the trigger prompts. State the cost plainly — this is the one skill on the front
that spawns subagents per run, and the human is authorizing that spend.
**Done when:** the user has answered; declined → nothing written, run ends.

### 5. Write the case set, then run both arms
Write `evals.json` beside the skill it measures — `.agents/evals/<name>/` in a target repo
(the skill name, never the generated `.claude/skills/` bridge), `.agents/quenching/assets/evals/<path>/`
in this plugin, `<path>` mirroring the command path
([components-command-eval/evaluation.md](.agents/skills/quenching-components/references/components-command-eval/evaluation.md)
§Where the artifacts live). Then, per case, dispatch **two** `Task` subagents — one told the
skill is available, one given the same prompt with no reference to it. Pin both to the session
model; **never `haiku`**. Record each arm's
output, token count, and duration.

Arms are independent: dispatch them concurrently, and a subagent that dies is recorded as
`error`, never silently dropped.
**Done when:** every case has two recorded arms or a recorded error.

### 6. Grade with evidence, then aggregate
Grade every assertion in every arm pass/fail with a quoted excerpt, into the per-case
`grading.json`. Aggregate into `benchmark.json`: pass rate, tokens, and duration for each arm and
the **delta** between them.
**Done when:** `grading.json` and `benchmark.json` exist and every assertion carries a grade with
evidence or an explicit `unknown`.

### 7. Tune the description on the hit rates (optional, own confirmation)
Run the should-trigger and should-not-trigger prompts and count how often the description routes
correctly. A trigger no should-trigger prompt reaches is **sediment**; a should-not-trigger prompt
that fires is a boundary the description fails to state. Propose the edit, show the measured rates
behind it, and apply on its own OK — then re-run `.agents/quenching/bin/cq components lint` so the caps still hold.
**Done when:** the rates are reported and the edit is applied or declined.

### 8. Report
The skill; cases run; pass rate with and without, and the delta; tokens and duration for each arm;
every assertion graded `unknown` and why; the trigger hit rates; and the artifacts written. Say
plainly when the delta is **zero**.
**Done when:** the delta is stated in the report as a number.

## Invariants

- Never run only the with-skill arm, and never reuse this context as an arm.
- Never grade without evidence — `unknown` is an honest grade, an unquoted pass is not.
- Never downgrade an arm's model to `haiku`; the delta is only comparable between equal arms.
- Never rewrite a skill body here — report it with its `/quenching-components command-new` invocation. The description is
  the single exception, and only under step 7's own confirmation.
- Never duplicate an unchanged `.agents/quenching/bin/cq components lint` check; after editing a description, rerun it and
  report the new findings by code.
- Never hand this command file `context: fork` — steps 4 and 7 gate mid-flow.
