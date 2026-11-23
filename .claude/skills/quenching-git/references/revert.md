---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(git status:*), Bash(git branch --show-current:*), Bash(git rev-parse:*), Bash(git rev-list:*), Bash(git merge-base:*), Bash(git log:*), Bash(git show:*), Bash(git diff:*), Bash(git revert:*), Bash(python3:*), Read, AskUserQuestion
---

# /quenching-git revert — compensate one commit without rewriting history

**Input**: `$ARGUMENTS` — `commit:<ref>` names a commit or tag; `spec:<id> task:<id>` resolves the
recorded task subject to exactly one commit; append `mainline:<n>` when the target is a merge commit.
No input, an ambiguous subject or a missing target refuses without touching the index or history.

The commit contract in `.agents/quenching/references/git/commit.md` owns task subjects
and their anchor semantics. A successful revert of a task's implementing commit also reopens that
task with a reason: the task anchor is no longer true, and the explanation belongs in the spec's
`## Discoveries` section.

## Workflow

### 1. Confirm a safe starting point

Read the current branch, HEAD, working-tree status and resolved base:

```bash
git status --porcelain
git branch --show-current
git rev-parse --verify HEAD
.agents/quenching/bin/cq git base --json
```

Refuse a dirty tree, detached HEAD, missing repository, or a request to rewrite history. The
operation runs on the current branch and never checks out another ref. **Done when:** the branch,
HEAD, base and clean-index precondition are known, or the refusal is reported.

### 2. Resolve one target and its task anchor

For `commit:<ref>`, resolve exactly one commit with `git rev-parse --verify <ref>^{commit}`. For
`spec:<id> task:<id>`, read `.agents/quenching/bin/cq specs status --spec <id> --json`, select that task's recorded
subject, and run `git log --grep=<subject> --fixed-strings` until exactly one commit matches. Zero
or multiple matches refuse; a task SHA that no longer exists is not replaced by a guessed subject.
**Done when:** one target SHA, its source selector and — when applicable — its task anchor are
known, or the refusal names the ambiguity.

### 3. Inspect reachability, parent shape and effect

Confirm the target is an ancestor of the current branch with `git merge-base --is-ancestor`; read
parents with `git rev-list --parents -n 1`; show author, date, subject, files and summary with
`git show`; and show the inverse effect with `git diff <target>^ <target>`. A target outside the
current branch, a missing parent or an unsupported shape refuses. A merge commit requires an
explicit `mainline:<n>` naming one valid parent; never choose a mainline by position silently.
**Done when:** the exact target, parent/mainline choice, reachability and inverse effect are shown.

### 4. Show the revert and ask once

Show the branch, target SHA, original subject, author/date, files, selected mainline, expected
inverse effect and the native `git revert` command. Put a decision sheet ([specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md) §1; A confirm, B decline) for confirmation of
that exact new commit, and end the turn. Before confirmation, do not alter spec records, task checkboxes, remote refs
or the target commit. **Done when:** the human confirms or declines the exact revert.

### 5. Create and verify the compensating commit

For a normal commit, run `git revert <target>`; for a merge, run `git revert -m <mainline> <target>`.
Keep hooks enabled and use no history-rewriting or bypass option. If Git reports a conflict, stop
immediately, preserve its state and message, and leave `git revert --continue` or `git revert --abort`
as the human's explicit recovery choice; do not run either one. On success, read `git rev-parse HEAD`
and `git show --stat --summary HEAD`. If the input resolved through `spec:<id> task:<id>`, only after
that new revert commit is verified, run:

```bash
.agents/quenching/bin/cq specs task --spec <id> --uncheck <task> \
  --reason "reverted by <new-revert-sha>"
```

That one specs write removes the task's stale `subject:`/`commit:` anchor and records the reason
in `## Discoveries`. If the task update fails, report the backend failure explicitly; do not claim
the spec is reopened. A direct `commit:<ref>` revert has no task record to update. **Done when:**
the new revert SHA and subject are verified and, when applicable, the task reopening is confirmed,
or the conflict/failure is reported without a recovery action.

### 6. Report the boundary

State the original target, new revert commit, branch, whether a mainline was used, files affected,
and, for a task selector, the `--uncheck --reason` write to `## Discoveries`; for a direct commit
selector, state that no spec record was selected. In either case no remote was changed. Name
`/quenching-git push` only as a later human action; do not invoke it. **Done when:** the compensating
commit or the unchanged conflict state is fully reported.

## Invariants

- Create a new revert commit; never erase, amend, reset, rebase, force-push or alter the original.
- Require a clean tree, attached current branch, unique target and explicit mainline for merges.
- Never resolve a conflict, alter an unrelated spec, or change remote state in this command. For a
  confirmed `spec:<id> task:<id>` selector, reopening that exact task with `.agents/quenching/bin/cq specs task --uncheck
  --reason` is the required post-revert bookkeeping, and happens only after the revert commit.
- Preserve Git's own failure and conflict output; never infer a successful revert from a changed file.
