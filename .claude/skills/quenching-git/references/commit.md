---
allowed-tools: Bash(cq git commit:*), Bash(cq git conventions:*), Bash(cq git specs:*), Bash(cq git state:*), Bash(cq specs next:*), Bash(cq components read:*), Read
---

# /quenching-git commit — commit what is staged, under the target's own convention

This command is the single commit boundary for standalone commits. `/quenching-specs execute` does
not delegate here per task: it commits each task through `.agents/quenching/bin/cq git commit --subject`. This command
resolves the subject, commits the existing index and reports the subject actually written. It never receives a request to stage an unrelated path.

**Input**: `$ARGUMENTS` — an optional explicit commit subject. When omitted, resolve one from the
current spec/task context; never invent it from the diff and never ask for confirmation.

Commits **only what is already in the index** — never `git add -A` on the human's behalf, and
never a guess at which files belong together.

## Workflow

### 1. Read-if-present, and the staged diff
```bash
.agents/quenching/bin/cq components read .agents/quenching/references/git/conventions.md \
  --sections "§The declared-directive layer" --sections "§The read-if-present rule"
.agents/quenching/bin/cq git conventions --json
.agents/quenching/bin/cq git state --json   # `staged` is the index, `branch` the current branch
```
One `.agents/quenching/bin/cq git conventions` call answers both declared layers: `config.commitSubject` is the target's
directive for this artifact, `governs`/`declared` the docs layer. Nothing staged → refuse: name that
the index is empty and say what to stage. **Done when:** which layer governs the subject
(`gitConventions.commitSubject`, the target's docs, or the plugin default below) and the staged file
list are both known.

### 2. Resolve the subject
§The declared-directive layer's table governs, for this one artifact: `config.commitSubject` from
step 1 wins where it is declared; else a target doc naming a subject format wins outright, and a doc
covering only part of it governs that part. With neither declared:
```bash
.agents/quenching/bin/cq components read .agents/quenching/references/git/commit.md --sections "§Commit messages"
```
`$ARGUMENTS` given → use it verbatim, wrapped rather than truncated past 72 characters where the
title allows; an explicit subject always wins. When it is omitted, resolve the current branch and
its `quenching-specs:` mark:
```bash
.agents/quenching/bin/cq git specs <current-branch> --json   # <current-branch>: `branch` from step 1's `.agents/quenching/bin/cq git state`
.agents/quenching/bin/cq specs next --spec <spec-id> --json
```
If that produces exactly one spec and one actionable task, derive the subject with the governing
convention from the spec id, task id and task title. If the execution caller already supplies a
unique spec/task pair, use that pair as the equivalent context. More than one candidate, no
candidate, or no actionable task is **ambiguous or missing context**: refuse, name the unresolved
fact, and tell the caller to supply an explicit subject or spec/task pair. Do not choose by
recency, branch order or staged-diff content. **Done when:** the exact subject string is fixed or
the refusal is reported.

### 3. Commit
```bash
.agents/quenching/bin/cq git commit --subject "<subject>" --json
```
The verb reports the `sha`, the `subject` git recorded and `subjectMatches`; exit 1 is git refusing
the commit (a hook, an identity) with HEAD unchanged, exit 2 a blank subject or an empty index.
Execute immediately after the staged-diff and hygiene checks; a resolvable omitted subject does
not add a confirmation question. The command commits the existing index only — it never stages
files, amends history, or bypasses hooks.
The `commit-msg` hook only *adds* (a ticket prefix, a sign-off) still matches by substring and needs
nothing; one that **replaces** the subject outright is a mismatch, reported as a finding rather than silently accepted. **Done when:**
the commit exists and its logged subject either matches or the mismatch is reported.

### 4. Report
State the sha, the subject, the committed files, and **which layer governed the subject** —
`gitConventions.commitSubject`, the target's docs, or the plugin default. **Done when:** all four
are named.

## Invariants

- **Never `git add -A`, or any equivalent of it.** Staging is the caller's decision; this command
  commits the index exactly as it finds it.
- **Never `--no-verify` or `--no-gpg-sign`.** A rejecting hook is a finding to report, not an
  obstacle to route around.
- **Never amend or rewrite an existing commit, and never force-push.**
- **Never install `docs/standards/git/**` into the target, and never write `gitConventions` into
  its `.agents/quenching.json`.** A convention read here is followed, never written back.
