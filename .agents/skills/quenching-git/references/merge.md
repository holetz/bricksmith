---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(git worktree:*), Bash(git merge:*), Bash(git rebase:*), Bash(git branch:*), Bash(python3:*), Read, AskUserQuestion
---

# /quenching-git merge — bring a branch home, on the human's own strategy

**Input**: `$ARGUMENTS` — a spec id (its `branch.work`/`branch.base` records name the branch and
target) or a bare branch name to merge into the resolved base. Omitted → ask.

The four strategies, the squash caveat and the post-merge worktree removal are in
[git/merge.md](.agents/quenching/references/git/merge.md).

## Workflow

### 1. Load the rules and resolve branch, base and checkout
```bash
.agents/quenching/bin/cq components read .agents/quenching/references/git/merge.md \
  --sections "§Merge strategies" --sections "§The squash caveat" \
  --sections "§The worktree is removed after a successful merge"
.agents/quenching/bin/cq components read .agents/quenching/references/git/conventions.md \
  --sections "§The declared-directive layer"
.agents/quenching/bin/cq git conventions --json   # `config.mergeSubject`, or absent
.agents/quenching/bin/cq git base --json
git worktree list --porcelain
```
An ID in `$ARGUMENTS` → `.agents/quenching/bin/cq specs status --spec "<id>" --json`, its `branch.work` is the branch
to merge and `branch.base` the target (falling back to `.agents/quenching/bin/cq git base`'s answer where absent). A bare
branch name → merge it into `.agents/quenching/bin/cq git base`'s answer. No checkout in the worktree list holds the base
→ **stop without merging**, naming the base and the fix (provide a checkout or add a worktree of it
outside this command). Never check out the base here.
**Done when:** the branch, the base, and the checkout that holds it are all resolved, or the run has
stopped for lack of one.

### 2. Offer the strategy, and the branch's fate under squash
Show the trade in one line each, from §Merge strategies, and put it on ONE decision sheet ([specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md) §1) that ends the turn; the options
of `D1`, with A the recommended one:

- **A (recommended) merge commit** — every per-task commit stays on the base, one extra commit.
- **B squash** — one commit on the base; the per-task commits survive only on the branch itself.
- **C rebase** — linear history, per-task commits preserved through the rewrite.
- **D fast-forward** — nothing rewritten or added; only possible when the base has not moved.

**Squash chosen → offer, separately, not to delete the branch** — deleting it strands every commit
subject the archived record resolves against. The offer records the human's preference, but this
command never deletes the branch. **Done when:** the strategy is chosen, and, under squash, the
branch-survival preference is recorded.

### 3. Run it in the base's own checkout
The subject the strategy writes follows §The declared-directive layer's table:
`config.mergeSubject` from step 1 where declared, else the target's docs, else
`plan/<id>-<handle>: merge (<strategy>)` from [git/commit.md](.agents/quenching/references/git/commit.md)
§Commit messages. `fast-forward` writes no commit, so it has no subject to govern.
```bash
git -C <the checkout from step 1> <the chosen strategy's command>
```
Never `git checkout <base>` — from inside a worktree it fails outright
(`fatal: '<base>' is already used by worktree at …`, exit 128). A failure is reported verbatim; nothing downstream runs. **Done when:** the
merge command exits 0, or the run has stopped on its failure.

### 4. Stamp, with an ID
```bash
.agents/quenching/bin/cq specs record "<id>" merge --set strategy=<strategy> --set subject=<the merge commit's subject, or "none — <why>" under fast-forward/rebase>
```
Write-once — a record already present is read, never overwritten. No ID → nothing to stamp.
A local merge is not a sync point of the git spec store, so the stamp stays in the clone's pending
commit; send it, with every other pending write, in the same call:
```bash
.agents/quenching/bin/cq specs push
```
Then close the spec, now that its work is merged — the archive waits for the merge, and this is
the merge:
```bash
.agents/quenching/bin/cq specs reconcile --write --json
```
A `needs-human` row (`## Outcome` not written, a task still open) is reported and never forced.
Then bring the local base up to the remote with `.agents/quenching/bin/cq git refresh --json`: fast-forward only, it keeps uncommitted
changes and switches no branch, and a `diverged` or `merge-refused` result is reported, never forced.
**Done when:** the record is stamped and pushed and the spec archived or its `needs-human` row
reported (with an ID), or explicitly skipped (without one).

### 5. Remove the worktree, only after a verified merge
Only when step 1's worktree list held a **separate** worktree for the merged branch (not the base's
own checkout) and step 3 exited 0. A **slot** (`../<repo>-slot-<n>`) is never removed here: step 4's
`reconcile` already released it (its row's `slot`), and a slot it could not release stays held
with the reason reported. A slot still locked to the merged branch with **no** `reconcile` row
(the spec was archived by an earlier write, so `reconcile` had nothing to do) is released here —
never `cq git worktree retire`, which removes worktrees and refuses a locked slot:
```bash
.agents/quenching/bin/cq git worktree release --path <the slot path> --json
```
Any other worktree:
```bash
git -C <the base's checkout> worktree remove <the worktree path>
```
**Never `--force`.** `git worktree remove` refuses a tree holding modified or untracked files on its
own; report the refusal and leave the worktree standing. Never deletes the branch itself. **Done when:** the worktree is removed, refused with
its own error reported, or the step was skipped because there was none to remove.

### 6. Report
State the strategy, the merge commit (or the explicit none) and **which layer governed its
subject**, what was stamped, and the worktree's fate. **Done when:** all four are named.

## Invariants

- Never `git checkout <base>` to reach the checkout that merges — always `git -C`.
- Never merge without a checkout that already holds the base; never manufacture one or check it out
  from this command.
- Never `--force` a worktree removal, and never remove one before a merge verified at exit 0.
- Offer the strategy; never choose it for the human.
- Never install `docs/standards/git/**` into the target, and never write `gitConventions` into its
  `.agents/quenching.json`; a declared convention is read here, never written back.
