---
type: standard
title: Test run cost
description: What a run may write, how many workers it may take and how much of the suite each tier runs — one temp root outside the repository, a git configuration of the run's own, a shared worker budget and three scopes, with a narrow local gate that never replaces CI
resource: AGENTS.md, /docs/standards/ci-cd/test-cost.md
tags: [tests, performance, gate, temp, workers, ci]
generated: { by: quenching/7.5.0, at: 2026-11-06T00:00:00Z }
audience: both
authority: current
source: spec 2181 — the measured baseline, the temp-root matrix and the three tiers; spec 2183 — the three tiers as three different proofs
maintainer: quenching
---

# Test run cost

A run costs what it writes and what it runs. Left alone, a suite writes hundreds of megabytes of
temporary files per run and pays itself in full for a one-line change. The rules below keep the gate
cheap without making it narrower: what changes is where the bytes go and how much of the suite each
tier runs — never whether the gate runs.

## One temp root, outside the repository

A run resolves **one** temp root and points `TMPDIR`/`TEMP`/`TMP` at it before the runner starts, so
every temporary file, every worker and every child process inherits it with no test edited. The root
is chosen RAM-first — a writable tmpfs that allows executing a temporary program and has free space
— and falls back to the system temp directory with a notice. Each run creates its own directory
under that root and removes it on exit, including on `SIGINT` and `SIGTERM`.

Two consequences follow, and both are rules:

- **A test never creates a temp path of its own.** A test-local root escapes the one directory the
  cleanup removes and outlives the run.
- **Nothing a later step must read lives inside that directory.** A coverage artifact written there
  is deleted before the report is combined; it belongs beside the gate's other evidence.

## A git configuration of the run's own

The run writes its own git configuration under the temp root and exports it, so a developer's hooks,
signing and `init.defaultBranch` never reach a test, while the repository's own configuration still
outranks it. A test that shells out to `git` therefore sees the same world on every machine, and the
gate stops failing for reasons that live in `~/.gitconfig`.

## A worker budget, not a worker count

Parallelism is bounded for the machine, not chosen per invocation. Concurrent gates divide the
available cores through one shared budget held by a kernel lock, and a nested run executes serially
inside its parent's quota instead of queuing behind it. A run that takes every core turns two
concurrent gates into two slow gates: the measured contention case here ran 399 s against 227 s once
the workers were bounded.

## Three tiers, one gate

The gate stays the only gate; what changes is what each stage proves. A task pays for the evidence
its own change needs, the close pays for the branch, and CI pays for the whole suite. A narrower
scope never replaces CI: the merge still waits for a green full run.

| Stage | Proof | Suite |
| --- | --- | --- |
| a task | targeted — the file or directory the task touched | that file or directory's tests, with the expensive layer deferred and counted |
| a spec's close | the branch — the diff's selection | the same selection inside the whole gate, the coverage floor reported `SKIPPED` |
| CI and the release | the whole suite | everything, the floor enforced |

A task's `verify:` is not the repository gate. The gate is the close's proof and the branch's
contract; the cadence it runs at is the spec's `verification` policy — `per-task`, `per-section` or
`end-of-plan`, declared once per spec — never encoded in a task's command.

The selector maps each source surface to the tests that cover it, adds the tests whose direct
imports reach a changed module, and takes the diff from the merge base plus the working tree plus
untracked files. It answers "the whole suite" whenever it cannot be sure — a global file, a path no
rule covers, a rule naming a test that no longer exists, an unresolvable base — and prints the
reason instead of guessing.

## The evidence

Measured in spec 2181 on this plugin's own suite, before and after: with `-n auto` the suite went
from 688.6 MB written and 54,538 write operations in 63.0 s to 1.7 MB and 1,536 operations in 44.7 s;
the whole gate from 723.2 MB and 57,985 operations in 89.2 s to 36.3 MB and 3,002 operations in
79.8 s, the remainder being the documentation build. A task-sized diff ran 89 tests in 3.07 s
against 27.98 s for the whole suite.

## Adopting it in a target

The runner belongs to the target, and the plugin never installs one. What a target declares is the
contract, in `.agents/quenching.json`:

```json
{"proof": {"cost": {
  "tempRoot": "isolated",
  "workers": "auto",
  "scopes": {"task": "…", "close": "…", "ci": "…"}}}}
```

`tempRoot` is `isolated` (outside the repository, removed when the run ends) or `system`; `workers`
is `auto` or a positive integer; `scopes` names the three commands. `cq proof doctor` grades the
declaration and reports `pf-cost-undeclared` for what is missing, `pf-temp-shared` for a `system`
root, `pf-scope-wide` when the close scope is the CI scope, and `pf-task-wide` when the task scope is
the close scope. The values are rendered into the generated proof README, so the declaration and the
file a reader opens cannot drift apart.

These are judgement findings, not repairs: moving a temp root, adding a diff selector, choosing a
narrower close scope and narrowing a task scope are changes to the target's runner, and the align
reports them with the command that closes them instead of editing the runner.
