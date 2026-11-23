---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(git status:*), Bash(git branch --show-current:*), Bash(git remote:*), Bash(git rev-parse:*), Bash(git rev-list:*), Bash(git log:*), Bash(git reflog:*), Bash(git ls-remote:*), Bash(git push:*), Bash(python3:*), Read, AskUserQuestion
---

# /quenching-git push — publish one branch, without opening a PR

**Input**: `$ARGUMENTS` — omitted means the current branch on `origin`; one positional argument
names a branch; `--remote <remote> [branch]` names the remote and optionally the branch. A branch
name is never interpreted as a remote. This command publishes only; it does not open or update a
pull request.

## Workflow

### 1. Resolve the destination and local preconditions

Read the current branch, working-tree status, remotes, remote URL and the base from:

```bash
git status --porcelain --untracked-files=all
git branch --show-current
git remote -v
git remote get-url <remote>
.agents/quenching/bin/cq git base --json
```

Refuse a dirty tree, detached HEAD, missing remote, missing branch, or target equal to the resolved
base. The default remote is `origin`; an alternate remote requires `--remote`. **Done when:** the
remote URL, local branch, target ref and base comparison are known, or the refusal names its cause.

### 2. Measure upstream, divergence and commits to publish

Resolve the local branch and its upstream with `git rev-parse`; inspect the remote ref with
`git ls-remote`; when it exists, measure ahead/behind with `git rev-list --left-right --count` and
list the commits to publish with `git log`. A branch behind or divergent from its remote is a
refusal for `/quenching-git sync` **unless** `git reflog show <branch>` proves the remote tip was
the branch's prior tip and the local commits are the same work after a rebase. That one proven
case is a lease publication, not a normal push; an unproven divergence remains a refusal. An
absent remote branch is a new upstream candidate. No commits ahead is an informational no-op.
**Done when:** the exact refspec, upstream action, lease expectation and commit list are known, or
publication is refused without a push.

### 3. Prepare the normal push command

Use the explicit refspec `HEAD:refs/heads/<branch>`. If no upstream exists, use `git push --set-upstream
<remote> HEAD:refs/heads/<branch>`; otherwise use `git push <remote> HEAD:refs/heads/<branch>`. For
the one rebase proven in step 2, prepare
`git push --force-with-lease=refs/heads/<branch>:<expected-remote-sha> <remote> HEAD:refs/heads/<branch>`.
Never use bare `--force`, bypass a hook, fetch, stage, commit, rebase, reset, or alter the refspec
to work around an unproven refusal. **Done when:** one normal or lease push command is fixed, or
the no-op/refusal is reported.

### 4. Show the write and ask once

Show the remote URL, local branch, destination branch, upstream action, ahead/behind counts and
every commit that will be sent. For a lease push, also show the expected remote SHA, the current
remote SHA and the commits being replaced. Put a decision sheet ([specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md) §1; A confirm, B decline) for confirmation of exactly
that normal or lease push, and end the turn. A changed destination, branch, remote URL, lease or commit list requires
a fresh run and a new confirmation. **Done when:** the human confirms or declines the exact
publication.

### 5. Publish and report

Run only the confirmed normal or lease `git push` command. Preserve Git's failure and output
verbatim; do not retry with a stronger option. On success, report the new upstream and pushed commits, then name
`/quenching-git pr-create` as a possible later action without invoking it. **Done when:** the push
succeeded with its remote facts, or its refusal/failure is reported and no bypass was attempted.

## Invariants

- Publish only the selected local branch to the selected remote; never open, edit or merge a PR.
- Refuse dirty trees, detached HEAD, base branches, missing remotes, unproven behind/divergent
  upstreams and bare force requests before any push.
- A `--force-with-lease` push is allowed only for the proven local-rebase case, after showing the
  expected remote SHA and receiving a fresh confirmation.
- Never fetch, stage, commit, rebase, reset, amend, bypass hooks or rewrite the refspec in this
  command.
- Never infer success from local refs; report the push command's own result.
