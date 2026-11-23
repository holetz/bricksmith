# Wording a commit, and anchoring it to the task it closes

The default subject grammar this front writes, and how a task's commit resolves back to the task
line that recorded it. Read
[conventions.md](.agents/quenching/references/git/conventions.md) first: the read-if-present
rule governs whether the defaults below apply at all.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## Commit messages

<!-- rules -->

The default subject, one per task:

```
plan/<id>-<handle>: <task-id> <task title>
```

```
plan/session-tokens: 3.2 Add rate limiting to the auth middleware
```

Wrap the title rather than truncating it, and keep the subject under 72
characters where the title allows.

When the caller omits a subject during an incremental spec build, resolve the same grammar from
exactly one current spec and one actionable task. An explicit subject wins. No spec/task context,
no actionable task, or more than one candidate is a refusal — never derive a generic subject from
the staged diff, branch recency, or task order.

The other subjects this front writes follow the same grammar:

```
plan/<id>-<handle>: merge (<strategy>)
plan/<id>-<handle>: record <what>
```

An optional **merge-time squash** produces one commit on the base instead of carrying the task
commits into its history. It does not change the task subjects on the work branch; preserve that
branch when the archived records must continue resolving by subject.

## The subject is the anchor

<!-- rules -->

The task→commit link is the **subject line of the commit**, written onto the task line by
`.agents/quenching/bin/cq specs task --check --subject`:

```markdown
- [x] 3.2 Validate the token
      files: src/auth.py
      subject: plan/session-tokens: 3.2 Validate the token
```

It resolves by substring match:

```bash
git log --grep="<the recorded subject>" --fixed-strings
```

**The subject is known before the commit exists.** That is the whole reason it is the anchor, and
it has one consequence everywhere: every record is written *before* the thing it describes, so
nothing is left to write afterwards.

- `/quenching-specs execute` ticks the box **first**, then commits the code and the ticked box together.
  The per-task bookkeeping commit is gone — it existed only because a sha cannot be known before the
  commit that carries it. Each task's own commit remains the task's anchor.
- `/quenching-specs conclude` stamps `merge: {strategy, subject}` on the work branch, so the **merge is the
  last action of the run** and nothing is ever committed to the base branch after it.

**Two forms are read, forever.** A spec built before this change carries `commit: <sha>` and
resolves by sha. Neither form is backfilled: a recorded sha describes a commit that exists, and
rewriting an archived spec to "modernise" it would falsify when the record was made.

**Still no trailer and no machine-readable anchor inside the message.**

The commit path never stages files on the caller's behalf. It commits the existing index only,
keeps hooks enabled, and never amends or rewrites an existing commit.

<!-- rationale -->

The link is the subject the
target's own convention produced — if the repo's standard prefixes a ticket or appends a sign-off,
the recorded subject is whatever that convention actually wrote. The record follows; it never
imposes.

**On the default subject `plan/<id>-<handle>: <task-id> <task title>`.** Three properties earn it: `git log
--oneline` reads back as the spec's `## Tasks`; a subject grepped by ID returns exactly that
spec's commits; and the task id makes a `git revert` of one task unambiguous.

Under the sha anchor, rebase rewrote every recorded commit and left the archived spec's `commit:`
fields pointing at commits that no longer existed — it was the one strategy that made the record
strictly worse rather than merely narrower.

**On `fast-forward` and `rebase` recording an explicit none.** Under both, the per-task commits
land on the base directly and their subjects resolve there, so a merge pointer would add nothing.

Stopping at the open PR is simpler and is wrong for two reasons, both contracts this file already
states. `## Outcome` is
written before the merge and says what the run **delivered** — an open, unmerged PR archived as
`done` would assert something that has not happened yet. And `merge:` is stamped before the merge
so that it is the run's last action; a run that ends before the merge leaves the record stamped and
nothing merged, which is recoverable but is not what "the merge is last" promises.

### Where the subject can fail, and what is done about it

<!-- rules -->

A `commit-msg` hook that **replaces** the subject outright breaks the link. Substring matching
survives every hook that merely *adds* — a ticket prefix, a `Change-Id` trailer, a sign-off — which
is nearly all of them. After committing, `/quenching-specs execute` compares `git log -1 --format=%s` against
what it recorded and **reports a mismatch as a finding, writing nothing**: a correction made after
the commit would reintroduce the very ordering this design removed.
