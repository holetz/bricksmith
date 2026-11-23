# Merging a work branch home — the four strategies, and the caveats each carries

The strategies `/quenching-git merge` offers once a spec's branch is reviewed and archived, what
each buys and costs, and how the worktree that held the work comes down afterward. Read
[conventions.md](.agents/quenching/references/git/conventions.md) first: the read-if-present
rule and the two prohibitions bind here too.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## Merge strategies

<!-- rules -->

Offered by `/quenching-specs conclude`, never chosen for the human. State the trade in one line each.

**Every one of them runs in the checkout that already holds the base**, located first and merged
into in place:

```bash
git worktree list --porcelain                 # which checkout holds <base>
git -C <that path> <the chosen command>
```

**Never `git checkout <base>`.** From inside a worktree it fails outright —
`fatal: '<base>' is already used by worktree at …`, exit 128 — so it is broken for exactly the
isolation recommended above. `git -C` is one path, not two special cases: from a worktree it names
the main checkout, from the main checkout it names itself. When no checkout holds the base,
`/quenching-specs conclude` says so and stops without merging rather than manufacturing one.

| Strategy | Command | What it buys | What it costs |
| --- | --- | --- | --- |
| **merge commit** *(default)* | `git merge --no-ff plan/<id>-<handle>` | every per-task commit stays on the base branch and every recorded subject resolves from it | one extra commit, and the base branch's history carries the task-level detail |
| **squash** | `git merge --squash plan/<id>-<handle>` then commit | one commit on the base branch; the spec reads as a single change | **the per-task commits live only on the branch** — deleting it leaves every recorded subject resolving to nothing |
| **rebase** | `git rebase <base> plan/<id>-<handle>`, then fast-forward | linear history, per-task commits preserved | rewrites every commit it moves — but a subject is carried along by the rewrite, so the records survive it |
| **fast-forward** | `git merge --ff-only plan/<id>-<handle>` | nothing is rewritten and nothing is added | only possible when the base has not moved |

Whatever is chosen is recorded as `merge: {strategy, subject}` and stated in `## Outcome`, because
a future reader resolving a task's subject needs to know which of these happened.

### When there is no merge commit to name

<!-- rules -->

`fast-forward` and `rebase` create none, so the record carries an explicit none rather than a
fabricated pointer:

```yaml
merge:
  strategy: fast-forward
  subject: none — fast-forward creates no merge commit; the branch's commits ARE the
    base branch's history
```

`.agents/quenching/bin/cq specs validate` reports the mismatched cases both ways — an
anchorless strategy carrying a real subject, and a merge-producing strategy carrying an explicit
none (`sp-bad-merge`).

### The squash caveat

<!-- rules -->

**The per-task commits survive only on the
branch.** So when squash is chosen, `/quenching-git merge` offers **not** to delete the branch, and says
why. Keeping it costs a ref; deleting it silently turns every `subject:` field in the archived spec
into a reference that resolves to nothing.

### Source branch deletion is a separate offer

<!-- rules -->

Provider PR creation preserves the source branch by default. GitHub and Azure deletion are separate
choices made only after the source branch, the PR and the effect on recorded task subjects have been
shown; Azure includes `--delete-source-branch true` only when that choice is confirmed. A spec whose
task subjects still resolve from that branch must keep it, especially after a squash merge.

### Rebase is no longer the strategy that destroys the record

<!-- rules -->

A subject survives a rebase's rewrite, so a rebased branch's
records still resolve. Rebase now sits on the same footing as the others, and the old warning
applies only to specs still carrying the sha form.

### The worktree is removed after a successful merge

<!-- rules -->

Once the merge exits 0,
`/quenching-git merge` removes the worktree — from the base's checkout, because nothing removes the tree
it is standing in:

```bash
git -C <the base's checkout> worktree remove <the worktree path>
```

**Never `--force`.** `git worktree remove` refuses a tree holding modified or untracked files on
its own (`contains modified or untracked files`, exit 128), so the irreversible case is one git
already declines — no prompt buys safety here, and one always answered "yes" is only friction. A
clean tree leaves the disk silently; a refusal is reported with the path and git's own output, and
the worktree stays.

Three bounds: only after a merge verified at exit 0, never for an abandoned spec, and it does
**not** delete the branch — that stays the separate offer it already was. A worktree **slot** is
never removed: `cq specs reconcile --write` releases it (detached at the remote base, unlocked, its
caches kept) — see [isolation.md](isolation.md) §Worktrees are slots. When the spec was archived
before the merge's own `reconcile` (an earlier `push` or `record` archived it), `reconcile` returns no
row and the slot stays locked to the merged branch: `cq git worktree release --path <slot>` frees it.
`cq git worktree retire` is the wrong verb for a slot — it removes a worktree and refuses the slot's
lock.
