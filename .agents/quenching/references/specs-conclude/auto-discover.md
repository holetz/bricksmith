# Auto-discover — resolving which spec `conclude` closes without `--spec`

How `/quenching-specs conclude` resolves the ID to close when it is called with none: reading the
current branch's own `quenching-specs:` marking — written by `/quenching-specs execute`, per
[isolation.md](.agents/quenching/references/git/isolation.md) §Marking the branch with the
specs it built — filtering it to what still resolves, and falling back to a diff-based offer when
nothing does. The marking exists because it is the fact the base branch's history cannot reproduce
once the branch is gone.

## Reading the marking

<!-- rules -->

```bash
git config branch.<current>.description
```

Parse the `quenching-specs: <id1>,<id2>` line, if the description carries one — the format is
[isolation.md](.agents/quenching/references/git/isolation.md) §Marking the branch with the
specs it built, not restated here. No branch, no description, or no such line → treated exactly as
**no marking**, which is §The fallback below.

## Filtering to valid IDs

<!-- rules -->

Every marked ID is checked with `.agents/quenching/bin/cq specs status --spec <id> --json` before it is offered as a
candidate. One that resolves under `plans/` is valid. One that errors (`sp-unknown-id`) or now
resolves under `archive/` is **invalid** — a branch reused after the spec that marked it was already
concluded, still carrying its old ID. An invalid ID is dropped silently before resolution: never
surfaced as a choice, and never assumed to be the answer because it was the only one on the line.

## Resolving what remains

<!-- rules -->

| Valid IDs remaining | What `conclude` does |
| --- | --- |
| exactly one | resolves it, and names the marking as the source in its report |
| more than one | lists the candidates on a decision sheet and proceeds with the one picked — the "one spec per call" contract does not change |
| zero | falls to §The fallback |

## The fallback — no valid marking

<!-- rules -->

1. **Measure the diff.** `git diff <base>...HEAD --stat`, where `<base>` resolves the same chain
   [isolation.md](.agents/quenching/references/git/isolation.md) §Recording the isolation
   already uses: the branch's own `branch.base` record if one exists, else `origin/HEAD`, else
   `init.defaultBranch`, else `main`.
2. **Always ask — never a size threshold.** Show the measurement and ask, on a
   decision sheet, whether to materialize a minimal spec from this diff before concluding:
   `.agents/quenching/bin/cq specs new`, with a title and `## Problem` drafted from the diff summary. No diff size skips
   the question or answers it by default — "big enough to matter" is a human judgment that changes
   by repository, and the question is already cheap.
   - **Accepted** → the new spec is minted, and its ID threads into the rest of `conclude` exactly
     as a marked one would.
   - **Declined** → `conclude` runs **headless**: the branch review, the emergent `/docs/`, and the
     distillation still happen, but there is no spec file — `## Outcome` has nowhere to land, so the
     run's report carries that summary instead.

## Invariants

- Never guess an ID out of an ambiguous or partly stale marking — filter first, then ask if more
  than one valid candidate remains.
- Never skip or auto-decide the minimal-spec offer on diff size — it is asked every time the
  fallback is reached, and the human's answer is what decides, never a threshold.
- Never write the marking from here — reading it is this file's whole job; writing it is
  [isolation.md](.agents/quenching/references/git/isolation.md) §Marking the branch with
  the specs it built, owned by `/quenching-specs execute`.
- The headless path never fabricates a spec file just to have something to conclude.
