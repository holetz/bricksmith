---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), AskUserQuestion, Write, Edit
---

# /quenching-opensource align — converge the open source front

**Input**: `$ARGUMENTS` (an optional target repository root; omit to use the current repository).

This command owns the open source front's convergence pass. It writes only the artifacts the
catalog gives to `opensource`, each through `.agents/quenching/bin/cq opensource apply` from a template whose upstream
text is byte-identical to the published one, and leaves every choice — license, maintainers,
contact, funding — with the target owner. The common front floor is in
[front-align/mold.md](.agents/quenching/references/front-align/mold.md), the catalog
contract is in
[opensource-controls/catalog.md](.agents/skills/quenching-opensource/references/opensource-controls/catalog.md),
and the finding bands and closure boundary are in
[opensource-align/bands.md](.agents/skills/quenching-opensource/references/opensource-align/bands.md).

Set `TARGET_ROOT` to the supplied argument, or to `.` when the argument is omitted. Resolve `cq`
per [tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool, pass the same root to every call, and branch on the JSON and exit code:
`0` is a clean answer, `1` carries findings, and `2` is a refusal.

## Workflow

### 1. Probe before reading the target

Run the route before reading or editing a target:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" opensource doctor --json
```

The probe reports `applicability`, template integrity and the `os-*` findings. Invoked directly,
this command runs even when `applicability.state` is `not-applicable` — that is the path of an owner
who wants to open the code; say so once and continue from the `status` payload the doctor embeds.
An `os-template-drift` finding stops the pass before any write: report it and write nothing. A
clean result with no advisory is conformant; report it and stop. On exit `2`, report the refusal
exactly.

**Done when:** applicability, the findings and the advisories are fixed, or a clean or drifted
result has stopped the pass without a write.

### 2. Classify every code by disposition

Use the `findings`, `advisories`, `artifacts` and `owners` rows already returned by the doctor; do
not walk the target again. Map each code once through `opensource-align/bands.md`:

| Band | Codes | Disposition |
| --- | --- | --- |
| Mechanical | none in the target | — |
| Structural | `os-contributing-missing`, `os-conduct-missing`, `os-support-missing`, `os-templates-missing` | apply the template once the owner has declared its placeholders in the plan, then re-run status |
| Judgement | `os-license-missing`, `os-governance-missing`, `os-codeowners-missing`, `os-citation-missing`, `os-funding-missing`, `os-reuse-missing`, `os-readme-media-missing`, `os-language-skew`, `os-template-drift`, any other `os-<control>-missing` | write only what the owner chooses explicitly; with no answer, report the evidence and the closing action |

A requirement of another owner is never a finding here: report it with the `command` its owner
block carries and leave it to that front.

**Done when:** each code has its band, evidence, target artifact and a permitted closing shape.

### 3. Present one plan and ask once

Show the complete plan: for each artifact to write, its `artifact` name, target path, template
source and the placeholders `sources.json` declares for it; every residual judgement hand-off; and
the closing `opensource status` command. Ask once, in the same question, for the routine batch and
for every placeholder value the plan needs — project name, discussion venue, conduct contact. A
judgement artifact enters the batch only with the owner's explicit answer: the SPDX id (`MIT` or
`Apache-2.0` are vendored) with year and holders, the maintainers, the code owners, the citation
authors, the funding account. Never propose a default for any of them.

Every community artifact and every owner-supplied placeholder prose is written in English: each
template declares `language: en`, and the `artifactLanguage` block of the probe carries that
declaration. Ask the owner for English prose when collecting placeholders; apply accepts the values
unchanged and answers with the informational `os-var-language-unverified` warning, because nothing
here detects a language. README and SECURITY are hand-offs in `artifactLanguage.handoffs`, left to
their owners.

If authorization is rejected, write nothing and report the findings unchanged.

**Done when:** one complete plan has been presented and either authorized, with every declared
value in hand, or rejected without a target edit.

### 4. Apply through the route, never through the model

For each authorized artifact, first dry-run, then write:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" opensource apply --artifact <name> --var <key>=<value> ... --dry-run --json
.agents/quenching/bin/cq --root "$TARGET_ROOT" opensource apply --artifact <name> --var <key>=<value> ... --json
```

The license takes `--var spdx=<id>` as its selector. Never write, paste or adapt a template's text
with `Write` or `Edit`; the route copies it byte for byte and substitutes only the declared
placeholders. A refusal — `os-artifact-unknown`, `os-license-unvendored`, `os-var-missing`,
`os-var-unknown`, `os-target-exists` — is reported as it came and that artifact is skipped; an
existing file is never overwritten. Then re-run the closing verifier:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" opensource status --json
```

If findings remain, distinguish unanswered judgement residue from a refused or failed apply. Do not
repeat an empty pass or broaden the target surface.

**Done when:** every authorized artifact was applied or its refusal reported, no owner choice was
invented, and the closing verifier has been run.

### 5. Report the front

Report the target root, applicability, the baseline level before and after, every artifact written
with its source and substituted placeholders, every refusal, the closing verifier result and all
residual findings with their evidence and owner action. State explicitly when the front was already
conformant, stopped on template drift, or remains non-conformant. This command's report is the run
record; do not append a log file to the target.

**Done when:** the report distinguishes applicability, evidence, applied artifacts, verifier state
and every residual owner action.

## Invariants

- The doctor is probed before any confirmation, and the closing verifier is `.agents/quenching/bin/cq opensource status --json`.
- Every write goes through `.agents/quenching/bin/cq opensource apply`; upstream text is never adapted and an existing file is never overwritten.
- License, maintainers, code owners, conduct contact, citation and funding are the owner's answers, never defaults.
- Community artifacts and placeholder prose are English by template declaration, never by detection; a residual target-language doubt is the owner's judgement, not a finding.
- Another owner's control is reported with its owner's command, never closed here.
- No configuration namespace is created: the owner's answers live in the plan and in the artifacts.
