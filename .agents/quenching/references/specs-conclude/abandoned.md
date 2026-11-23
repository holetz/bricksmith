# Where an abandoned outcome's writes land

Everything `/quenching-specs conclude`'s `--outcome abandoned` path writes — the emergent `/docs/` in
step 3, the archive move and `## Outcome` in step 4, the distillation's background note in step 5 —
lands in the checkout that already holds `<base>`, never on the work branch. There is no merge to
carry a branch commit home, so a record left on the branch would depend on a branch nobody adopted
still existing.

`/docs/standards/workflows/plan-git-record.md` §Every record is written where it needs to
survive is the contract, where a target repo keeps one; this file is the mechanics `conclude.md`'s
steps 3, 4, 5 and 6 cite rather than restate.

## Locating the checkout and writing into it

<!-- rules -->

Found the same way the merge itself is found
([merge.md](.agents/quenching/references/git/merge.md) §Merge strategies):

```bash
git worktree list --porcelain              # which checkout has <base> checked out
git -C <that path> status --porcelain      # must be empty before writing anything
git -C <that path> add <the paths just written>      # never -A
git -C <that path> commit -m "<subject>"
```

No checkout holds `<base>` → say so and stop, the same refusal the merge itself makes when nothing
has the base checked out; nothing is fabricated.

`git -C <that path> status --porcelain` non-empty → **refuse**: report it verbatim and stop without
writing anything — the checkout may be what the human is using for something else right now, and
there is no task here to separate an unrelated edit from. **Stage only the paths just written, never
`git add -A`** — the same contamination `/quenching-specs execute`'s own precondition refuses at the start of
a build, now guarded on someone else's tree instead of this run's own.

This clean check and this staging rule govern every write this outcome makes into that checkout —
steps 3, 4 and 5 all reuse both, not just the path.

Reading resumability signals (§Resuming) uses the same located path: `.agents/quenching/bin/cq specs --root <that
path>/.specs status --spec <id> --json`, in place of the bare form.

## What each step commits there

<!-- rules -->

- **Step 3, the emergent `/docs/`** — whatever the branch review surfaced, in its own commit.
- **Step 4, the archive move** — the promote itself lands on the `quenching` branch (and closes the card), which no
  code checkout owns, so there is nothing to mirror into the base for it. What this step commits into the base checkout
  is whatever local record the move produced beside it, and nothing else.
- **Step 5, the distillation's background note** — the same checkout, the same rule.

## The branch-delete offer, informed rather than defensive

<!-- rules -->

By the time step 6 offers anything, the closing itself already survives — nothing above depends on
the branch anymore. Say that split before asking: name the commits the branch would take with it
(`git log <base>..<work> --oneline`, the `branch` record's own `work`) and that the closing is
already safe on `<base>` regardless
of what happens to the branch next.

**Check `git worktree list --porcelain` before offering anything.** When it lists the work ref as
checked out somewhere — the ordinary case, since this outcome never removes that worktree —
`git branch -d` would fail with "Cannot delete branch checked out at" before it even reaches the
question the offer exists to ask. **Declare that instead of offering it**: name the worktree path,
and say the branch stays until the worktree is removed or the human deletes it from there. Only when
the branch is checked out nowhere does the offer below apply.

Then offer, default **keep** —
`/docs/standards/workflows/plan-git-record.md` §A branch is deleted with `-d`, never `-D`,
where a target repo keeps one, governs the offer itself and the refusal.
