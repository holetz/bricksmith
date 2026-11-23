# The evaluation contract — cases, grading, and the benchmark

<!-- rules -->
The owner of **what an evaluation produces**. `/quenching-components command-eval`'s body owns the workflow;
this file owns the three artifact shapes, what makes an assertion worth grading, and the
description-tuning loop.

<!-- rationale -->
The formats are adopted from Anthropic's `skill-creator` **verbatim**. Divergence would buy
nothing and would make this plugin's evals unreadable by the tool most adopting repos already
have installed.

## Contents

`.agents/quenching/bin/cq components read .agents/skills/quenching-components/references/components-command-eval/evaluation.md` returns the heading index;
`--sections` addresses one.

## Where the artifacts live

```
.agents/skills/<name>/SKILL.md      # the skill being measured — ONE file
.agents/evals/<name>/
  evals.json                        # the cases — committed, reviewed, edited by hand
  runs/<YYYY-MM-DD-HHMM>/
    grading.json                    # one object per case, with evidence
    benchmark.json                  # the run's aggregate and its delta
```

In a target repo `<name>` **mirrors the skill name**: `/release-notes` is measured by
`.agents/evals/release-notes/evals.json`, beside the `.agents/` source and never under the
generated `.claude/skills/` bridge. In this plugin the tree lives at
`.agents/quenching/assets/evals/<path>/`, `<path>` mirroring the command path
(`knowledge/add` for `/quenching-knowledge add`).

Run outputs are timestamped so two runs can be compared rather than overwriting each other — pass
the timestamp in; never read the clock inside a case.

## `evals.json` — the cases

<!-- rules -->
```json
{
  "skill": "communications-teams-create",
  "cases": [
    {
      "id": "happy-path",
      "branch": "domain-bound mint with an existing folder",
      "prompt": "Draft a Teams announcement about the Friday deploy freeze.",
      "assertions": [
        "a file is created under communications/teams/",
        "the file carries frontmatter with a non-empty type",
        "the announcement names the deploy freeze"
      ]
    },
    {
      "id": "declines-out-of-scope",
      "branch": "Not for: — email",
      "prompt": "Write the deploy freeze announcement as an email to the whole company.",
      "assertions": [
        "no file is written under communications/teams/",
        "the reply routes the request to the email skill by name"
      ]
    }
  ],
  "triggers": {
    "shouldTrigger": [
      "post a teams announcement about the freeze",
      "draft a teams message for the team"
    ],
    "shouldNotTrigger": [
      "send an email about the freeze",
      "what is our deploy freeze policy"
    ]
  }
}
```

Every field is required. `branch` names which path through the workflow the case exercises.

<!-- rationale -->
**Why a mirrored tree rather than a sibling folder** (§Where the artifacts live). A case set used
to sit *beside* the skill it measured, so that it survived a rename and was reviewed in the same
diff as the body it tests. A command is a single file — there is no folder to sit beside.
Mirroring the path keeps both properties by a different mechanism: renaming a command renames its
eval folder in the same mechanical step, and the two paths differ by one prefix, so a reviewer
looking at `commands/knowledge/add.md` can find `evals/knowledge/add/` without searching. What it does
**not** preserve is co-location in a single directory listing; that is the honest cost of a
one-file surface.

**Why the case rules take the shape they do** (§Writing cases). An assertion about how the reply
is phrased grades the model's prose style and will flap between runs on a skill that never
changed. The `Not for:` boundary is a promise the description makes, and a skill that fires on its
neighbours' work is worse than one that never fires. A prompt engineered to hit the skill's
vocabulary measures the prompt, not the skill.

## Writing cases

**One case per branch.** Read the workflow and list every path it can take: the happy path, each
conditional, each documented special case, and the `Not for:` boundary. A branch with no case is
untested surface, and the report says so rather than implying coverage.

**Assert on the observable outcome, never on wording.** A file that exists, a zone regenerated, a
command reported as run, a path *not* written.

| Weak assertion | Why it fails | Stronger |
| --- | --- | --- |
| "the response is helpful" | nothing observable | "a file exists at communications/teams/\<slug>.md" |
| "it mentions the glossary" | wording, not outcome | "knowledge/glossary.md gained an entry for the coined term" |
| "it does the right thing" | untestable | "no file is written outside communications/teams/" |

**At least one declining case.** Assert on the absence — no write outside its scope — and on the
routing being named.

**Realistic prompts.** Write what a user would actually type, including the vague version.

**At least one competing case.** A happy path phrased with the skill's own vocabulary only proves
that the skill runs when nothing argues against it. Add one case whose prompt pushes against the
body — it asks to skip a confirmation, to write before the probe, or to take the shortcut an
invariant forbids ("just commit everything", "no need to check, write it") — and assert that the
invariant held anyway. A body that only holds under a cooperative prompt is the regression this
case exists to catch.

**Assert order where the body fixes one.** When a step must come before another — the probe before
the plan, the confirmation before the write, the gate before the handoff — write the assertion as
the order itself ("the confirmation is asked before any file under docs/ is written") and grade it
from the positions of the two events in that arm's own transcript. Every `Done when:` met, but out
of order, is a `fail`.

## Running the two arms

<!-- rules -->
Each case runs **twice**, in isolated `Task` subagents:

| Arm | Setup |
| --- | --- |
| `with` | the subagent is given the skill and told it is available |
| `without` | the same prompt, with no reference to the skill and no path to its body |

Both arms are pinned to the **session model**. Never `haiku` for either: a delta between arms on
different models measures the models. The arms are independent, so dispatch them concurrently.

Record per arm: the output, `tokens`, `durationMs`, and `error` when the subagent died. A dead arm
is recorded, never dropped — a case silently missing one arm turns into a fabricated delta.

<!-- rationale -->
**Why the without-arm cannot be this conversation.** The orchestrator has read the skill in order
to derive the cases. Any arm it runs itself is contaminated by exactly the text the without-arm is
defined by lacking.

## `grading.json` — one per case

```json
{
  "case": "happy-path",
  "arms": {
    "with": {
      "assertions": [
        {
          "assertion": "a file is created under communications/teams/",
          "result": "pass",
          "evidence": "Created communications/teams/deploy-freeze.md"
        }
      ],
      "passRate": 1.0,
      "tokens": 4210,
      "durationMs": 18400
    },
    "without": {
      "assertions": [
        {
          "assertion": "a file is created under communications/teams/",
          "result": "fail",
          "evidence": "I've drafted the announcement below — let me know where to save it."
        }
      ],
      "passRate": 0.33,
      "tokens": 2980,
      "durationMs": 11200
    }
  }
}
```

`result` is `pass`, `fail`, or `unknown`. **`evidence` is a quoted excerpt from that arm's own
output** — not a summary, not a restatement. A grader that cannot find the excerpt records
`unknown` and says why; an unquoted `pass` is the failure mode this whole format exists to
prevent, because it is indistinguishable from a guess.

## `benchmark.json` — the delta

```json
{
  "skill": "communications-teams-create",
  "cases": 6,
  "with":    { "passRate": 0.94, "tokens": 25260, "durationMs": 110400 },
  "without": { "passRate": 0.39, "tokens": 17880, "durationMs": 67200 },
  "delta":   { "passRate": 0.55, "tokens": 7380,  "durationMs": 43200 },
  "unknown": 0,
  "errors": 0
}
```

The `tokens` delta is a **cost**, not a win: the skill bought 0.55 pass rate for 7,380 extra
tokens on this case set. Both numbers belong in the report, because a skill that adds ten points
of pass rate for triple the tokens is a different decision from one that adds fifty for a tenth
more.

## Reading a delta honestly

- **Positive pass-rate delta** — the skill changed what the agent did, on these cases. The claim
  is bounded by the case set and the report says so.
- **Zero delta** — the skill taught nothing here. Two causes, and **the numbers cannot tell them
  apart**: the skill is redundant, or the cases were too easy for it to matter. Report both
  readings; do not pick one.
- **Negative delta** — the skill made the agent worse. This is the most valuable result and the
  easiest to explain away. Report it first.
- **High `unknown` count** — the assertions were not observable. That is a finding about the case
  set, and the delta from that run should not be quoted as evidence of anything.

A benchmark measures a skill **against its own case set**, never against another skill. Two skills'
numbers are not comparable and must never be tabled side by side.

## Description tuning

The description is the only part of a skill this front edits on measured evidence, because routing
is the one property a run can count directly.

Run each `shouldTrigger` prompt and each `shouldNotTrigger` prompt against the description and
record whether it routes to this skill:

| Measurement | Reading | Action |
| --- | --- | --- |
| a `shouldTrigger` prompt misses | a branch users ask for has no trigger | add the phrase they used, verbatim |
| a trigger no `shouldTrigger` prompt reaches | **sediment** — it costs context and fires for nobody | propose removing it |
| a `shouldNotTrigger` prompt fires | the boundary is stated too weakly | sharpen `Not for:`, not the triggers |

**A trigger is only removed on a measured miss**, never because a
description needs to be shorter — that is how a skill quietly stops firing for the user who
worded it differently. And after any edit, re-run `.agents/quenching/bin/cq components lint`: the caps
(`sk-metadata-cap`, `sk-description-portable`) and the trigger position
(`sk-trigger-position`) still hold.
