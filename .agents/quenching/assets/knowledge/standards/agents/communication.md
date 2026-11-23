---
type: standard
title: Agent communication
description: The two languages a repo declares for what its agents write — the conversation tag on the root harness line, and an artifact language declared by a standard or the artifactLanguage config key — and the conduct contract that holds whether or not either is declared
resource: /docs/**, /.specs/**
tags: [agents, language, communication, harness]
generated: { by: quenching/6.13.0, at: 2026-07-30T00:00:00Z }
audience: both
authority: background
source: quenching skeleton
maintainer: <the team>
---

# Agent communication

How an agent communicates here, in two halves: the **language** it writes in, which each repo
declares for itself, and the **conduct** it owes whoever is reading, which no repo overrides.

One is a variable, the other a constant — see [Why conduct is not declared](#why-conduct-is-not-declared).

## The language

### Declaring it

A repo declares its **conversation** language as **one BCP-47 tag**, on a single line of its **root** harness file
(`AGENTS.md`; `AGENTS.md` is legacy):

```
Language: pt-BR — the contract is /docs/standards/agents/communication.md
```

That line carries **a value and a citation, and nothing else**. It never paraphrases the rule below:
a harness file that restates a standard is precisely the drift `/quenching-components harness-align` exists to remove,
while a value plus a pointer is not a restatement. It carries **one** value — a second configuration
key on that line is a defect, not a feature.

Three properties earn this form:

- it is **in context at session start**, so reading it costs zero tool calls;
- it works in a repo that has `specs/` and **no `/docs/` bundle** at all;
- it is one line, so there is nothing to keep in sync.

**Only the root harness carries the declaration.** A nested harness file
(`/docs/standards/AGENTS.md`) loads when that folder is touched, not at session start, so it would
give up the zero-cost property this form was chosen for. A tag found in a nested harness file is a
mistake to report, never a second place to look.

**The value is a tag, not a language name.** `pt-BR`, `en`, `ja` — one spelling each. A name invites
`Português`, `portugues` and `Portuguese` to mean the same thing, and no amount of citation fixes
that.

### What it governs — conversation only

The declared `Language:` tag governs the **conversation band** and nothing else:

| Band | Examples | Language |
| --- | --- | --- |
| **Conversation** | an answer to the human · a question a command asks · a report a command prints | **the declared tag** |
| **Artifact** | a concept doc's body · a spec's body, `## Handoff` and `## Tasks` included · a commit subject and message · a PR body | **the artifact language, declared separately — see [Artifact language](#artifact-language)** |

The conversation band is the one that costs most per day when it is missed: an agent that reads the
tag at session start and still answers, asks and reports in a language the human did not choose has
followed none of this.

Two exclusions, and only two:

1. **The canonical structure.** Folder names, file slugs, frontmatter keys, enum values, the `type`
   vocabulary and the parsed `##` headings stay canonical English, so they stay greppable across
   repos. That rule is not this doc's — it belongs to
   [../naming/command-surface.md](../naming/command-surface.md), and this doc only names it as its
   own boundary.
2. **This plugin's own command surface.** Every body under `quenching/skills/**` is
   English whatever a target repo declares. **Their output is not.** A question a command asks and a
   report it prints are prose aimed at the human, and they follow the tag. The body in English, the
   output in the language — translating a body is the error this distinction exists to prevent.

### Artifact language

**The repository decides its own artifact language**, independently of the conversation tag. It
declares it in one of two places:

- a **standard** under its own `/docs/standards/` that names the language its artifacts use; or
- the **`artifactLanguage`** key (one BCP-47 tag) in its quenching configuration —
  `.agents/quenching.json` (or the legacy `.agents/quenching.json`) or, when specs-axis
  configuration lives on the `quenching` branch, the `quenching.json` at that branch's root.

Either one is enough. If both exist and disagree, the standard is the written contract and the key
is the defect to fix. The two bands may take different languages: a team can converse in `pt-BR`
and write its durable artifacts in `en`, or the reverse.

### What silence means

**A target that declares nothing is under no constraint** — in either band. Silence is not a
default of `en` and it is not a finding. No artifact language declared means the agent writes
artifacts in whatever the surrounding material already uses, and asks only when that is unclear.
Adoption is opt-in per target: nothing becomes retroactively non-conformant, and no repo that
already carries the bundle has to change. Declaring a language does not retranslate the docs already
written — that is a migration, and a separate piece of work.

Nothing machine-checks any of this. A validator cannot reliably identify the language of a
document, so this rule is applied by convention — as every rule about what prose *says*, rather
than where it sits, must be.

## The conduct

The second half is a **constant**: identical in every repo, not configurable, and never written on
the harness line. It is *stated* here, not declared anywhere.

Three obligations hold in **every** task, whether or not a command is driving and whatever the
declared language:

- **Report what actually happened.** A check that failed is reported failing, with its output. A
  step that was skipped is named as skipped. Work that is done and verified is said plainly,
  without hedging. A green summary over a red run is the one failure mode from which nothing
  downstream can recover.
- **Confirm before the irreversible.** Anything hard to undo or outward-facing is confirmed first,
  unless the human has already authorized it durably. Approval in one context does not extend to
  the next.
- **Ask rather than guess — but only what the answer changes.** A question is owed when different
  readings of the request lead to materially different work. Routine judgment calls are made, not
  escalated.

### This half cites; it never restates

Most of the ground near these three already has an owner, and a second statement of a rule someone
else owns would commit — one layer up — the very defect this doc exists to cure. So the boundary is
drawn explicitly:

| Ground | Owner | This doc |
| --- | --- | --- |
| What one command promises, refuses and routes elsewhere | that command's own body and `description` | never restates it |
| The mechanics of actually asking — how a question is posed, accumulated and applied | the spec tooling that drives the asking — in a quenching-managed repo, its `specs-develop/questions.md` §The four shared mechanics | cites it |
| How commands, hooks and agent definitions are classified, authored, budgeted and swept | the repo's `/docs/standards/automation/` subject | cites it |

What is left — and what this doc owns — is only what holds in **every** task, command or not. That
is the band none of the three above occupies, and keeping to it is the single failure mode this
half has.

## Why conduct is not declared

The test is which of the two facts actually varies.

**Conversation language varies between repos.** That is why it needs one place per repo to be said, on the harness line. The artifact language is a second declaration, but a cold one: read when an artifact is written, from a standard or a config key, never on the harness line.

**Conduct does not.** "Report what happened", "confirm before the irreversible" and "ask when the
answer changes the work" are not local preferences; they are the contract. Declaring what does not
vary buys nothing and costs an entire configuration surface: the set of valid profiles, its
evolution, and the end of the *one line, zero tool calls* argument that chose this form in the
first place.

A doc with one variable and one constant is a single definition. Two values on the harness line would be two
configurations sharing a line.
