---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(python3:*), Read, AskUserQuestion 
---

# /quenching-git cleanup — prune what `.agents/quenching/bin/cq git stale` already found

**Input**: `$ARGUMENTS` — optional `remote:<name>`; omitted → `origin`. This command always starts
from a fresh report for the selected remote.

## Workflow

### 1. Ask the tool for the current report
```bash
.agents/quenching/bin/cq git stale --remote <remote> --json
```
The report contains local `staleBranches` (removable), `keptBranches` (staying, with reasons), fetched `remoteBranches` from `<remote>`, registered
`orphanWorktrees`, assessed `worktrees`, the `manifest` and its `groups`, and unregistered `unregisteredWorktrees` siblings with their path, branch and
size. Each assessed worktree and its `manifest` entry carries
`ignored: {regenerable: [...], unrecognized: [...]}`. An otherwise eligible worktree with only
regenerable ignored content is `candidate` and `recommended: true`; unrecognized ignored content
makes it `preserved`, with blocker `ignored-content-unrecognized` and the exact paths listed. A
worktree slot (`../<repo>-slot-<n>`) carries its `slot` number: a held slot is `locked`, and the
lowest-numbered free slots up to `shared.worktreeSlots.min` are `preserved` with blocker
`slot-floor` — warm on purpose. Only a free slot above the floor is a `candidate`
(`slot-above-floor`). A **held** slot appears in the `manifest` as `kind: slot`, never as
`kind: worktree`, and in `heldSlots` with its evidence: it is a `candidate` with reason
`archived-holder` only when every fact is in favour — the spec its lock names is in `archive`, its
HEAD is contained in `<remote>/<base>`, its porcelain is clean, no ignored content sits outside the
regenerable class (`.quenching/handoff/` and `.quenching/codex/` excepted: the release carries them
to the main checkout), and no process has its cwd inside it. Anything missing or contrary keeps it
`preserved` with the blocker named (`holder-not-archived`, `holder-unknown`, `holder-not-a-spec`,
`foreign-lock`, `base-unresolved`, `unmerged-head`, `dirty`, `ignored-content-unrecognized`,
`process-in-slot`, `process-lookup-unavailable`, `inspection-failed`).
`manifest` empty → say so and stop; there is nothing to prune. The `manifest` is the complete list, so a candidate that sits in no legacy list (a detached worktree) still keeps the run going. Do not fetch or run `git
remote prune` here: if the caller needs newer remote facts, it must fetch explicitly and start a
fresh cleanup run. The report's `remote` must equal the selected remote. **Done when:** the manifest and the selected remote are in hand.

### 2. Let the human pick what to prune
The report's `manifest` is the complete list: every entry carries a stable `id`, `kind`, `ref`, the
observed `tip`, `tipDate` (the committer date of that tip, never creation or last use; `null` when
unknown) with its `dateSource`, `status` (`candidate` or `preserved`), `reasons`, the exact
`action` and a `recommended` flag. Show every `preserved` entry (kept branches, ineligible
worktrees) with its reasons as information under "kept — not removable", and do not include it in the
prune choices. Show each `unregisteredWorktrees` finding with its path, branch and size as
information too.

For branches, present concise choices, not one line per entry: summarize the report's `groups` (homogeneous
candidates by scope, kind, reasons and date provenance) with their count and `oldest`–`newest` tip
date, and expand an entry only when it stands alone. A group resolves to the `ids` it displays —
never to a target discovered later — and every selected item has its exact action available on
request (`git branch -d <branch>`, `git worktree remove <path>`; `.agents/quenching/bin/cq git prune` runs them).

Put ONE decision sheet ([specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md) §1) that ends the turn, with four separate decisions:
**D1**, the local selection of branches; **D2**, the remote selection; **D3**, the worktree
selection; and **D4**, the archived slots to release. Each lists numbered choices and accepts several in the reply (`D1 1,3`), or `none`:

- **D1 — local branches:** list the local branch groups and items. Option A (recommended) is the
  `recommended` subset when the report marks one, else `none`; never "all".
- **D2 — remote branches:** list the branch groups and items from the fresh `remoteBranches`
  list, with the same recommendation rule. Show each branch's exact destructive action
  `git push <remote> --delete <branch>`, which `.agents/quenching/bin/cq git prune --remote-branch`
  runs. A remote branch is never included in the local selection.
- **D3 — worktrees:** list eligible (`candidate`) worktrees separately from branches; Option A
  (recommended) is this eligible subset, else `none`. Show each worktree's exact path and both
  ignored classes: `ignored.regenerable`, the paths removal would discard, and
  `ignored.unrecognized`. Show preserved worktrees under "kept — not removable" with their exact
  paths, blockers and both classes; never offer them for pruning.
- **D4 — archived slots:** list the `kind: slot` candidates with slot number, path, the spec its
  lock names, the tip, and the handoff files the release will move to the main checkout's
  `.quenching/handoff/` (never overwritten: a taken name gets a suffix). Option A (recommended) is
  this candidate subset, else `none`. A release detaches the slot at the base, unlocks it and
  deletes its merged branch; it never removes the directory. Show `preserved` slots under "kept —
  not removable" with their blockers; never offer them.

A recommendation never authorizes anything. Ask again on an unclear reply. A live `worktrees`
candidate is eligibility only: it is merged, clean and unlocked, which is not evidence that nobody
uses it. Ask for the owner's explicit confirmation that each selected worktree is inactive,
separate from the D3 selection; selecting it in D3 is not that confirmation. Without it, drop that
worktree. The same holds for each D4 slot: the evidence found no process in it, which is still not
the owner's word. **Done when:** the D1, D2, D3 and D4 selections are settled, every selected item
has one explicit action, and each selected live worktree or slot has its own inactivity
confirmation or is dropped.

### 3. Delete the chosen branches
```bash
.agents/quenching/bin/cq git prune --branch <branch> --id <id> --expect-tip <tip> --json
```
`<id>` and `<tip>` are the ones the manifest displayed. The verb re-reads the stale report and acts only on a branch it still lists at that tip (exit 2 otherwise, and a moved tip means a fresh run);
git refusing (`error: the branch '<branch>' is not fully merged`, exit 1) → report why and leave it standing;
this command never escalates to `-D`. **Done when:** every chosen branch is deleted, or its refusal
is reported.

### 4. Remove the chosen worktrees
```bash
.agents/quenching/bin/cq git prune --worktree <path> --id <id> --expect-tip <tip> --confirm-inactive --json
```
`--confirm-inactive` is passed only for a worktree the owner confirmed inactive. Orphan worktrees (directory missing) take neither flag. Worktree removal is **never --force**. A refusal (modified or untracked files inside) is reported with git's own
message, and that worktree is left standing. **Done when:** every chosen worktree is
removed, or its refusal is reported.

### 4b. Release the chosen archived slots
```bash
.agents/quenching/bin/cq git prune --slot <path> --id <id> --expect-tip <tip> --confirm-inactive --json
```
The verb re-reads the report and releases only a slot it still lists as a `kind: slot` candidate
at that tip and id (exit 2 otherwise), through `cq git worktree release` itself. Report each
release with the handoff files it moved (`handoff: [{from, to}]`) and whether the branch was
deleted; a release that stays held (exit 1) is reported with its message and the slot is left
standing. **Done when:** every chosen slot is released, or its refusal is reported.

### 5. Delete the chosen remote branches
```bash
.agents/quenching/bin/cq git prune --remote-branch <branch> --remote <remote> --id <id> --expect-tip <tip> --json
```
Run this only for a selected item from the fresh `remoteBranches` list, after the separate remote
confirmation. The command is an external write: report its exact remote/branch and its output. A
refusal leaves the server branch standing; never retry with another command or infer success from a
local remote-tracking ref. **Done when:** every selected remote branch was deleted, or its refusal
was reported.

### 6. Report
State what was deleted or released, what was refused (and why), and what was left untouched
because it was never chosen. Separate local branches, remote branches, worktrees and slots in the
report, naming every handoff file moved to the main checkout. **Done when:** the three outcome
classes are named.

## Invariants

- Never prune a branch or worktree `.agents/quenching/bin/cq git stale` did not report.
- Never offer `keptBranches` or `preserved` manifest entries for pruning: each is listed with the reason it stays.
- Never offer a worktree with unrecognized ignored content for pruning.
- Never release a slot the report does not list as a `kind: slot` candidate, and never discard a
  `.quenching/handoff/` file: the release moves it to the main checkout.
- Never remove a live worktree without the owner's explicit inactivity confirmation, the displayed `id` and the approved tip.
- Never offer `unregisteredWorktrees` for pruning: `git worktree remove` does not act on an
  unregistered directory, and this class carries no action.
- Never delete a remote branch that `.agents/quenching/bin/cq git stale` did not report in `remoteBranches`.
- Use only the selected remote (`origin` when omitted); a branch reported by another remote is not
  a candidate for this run.
- Never pre-select "all" in the prune offer — every item is the human's own pick.
- Never fetch or run `git remote prune` implicitly; the report is the fresh fact this run acts on.
- Never `git branch -D`, `git push --force`, or `git worktree remove --force`; a refusal is
  reported and the target is left standing for that specific refusal.
