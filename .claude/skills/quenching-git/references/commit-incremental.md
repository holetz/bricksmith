---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Bash(git status:*), Bash(git diff:*), Bash(git ls-files:*), Bash(git add:*), Bash(git commit:*), Bash(git log:*), Bash(python3:*), AskUserQuestion 
---

# /quenching-git commit-incremental — turn the current worktree into atomic commits

**Input**: `$ARGUMENTS` — an optional subject prefix; absent → use the target convention or the
fallback below.

Operate on the current checkout only. Read the shared git rules from
`.agents/quenching/references/git/conventions.md` and
`.agents/quenching/references/git/commit.md`; those references own convention precedence,
subject anchoring and commit hygiene. The report is in the repository's declared conversation
language, while commit subjects and messages remain canonical English artifacts.

## Workflow

### 1. Snapshot the worktree and guard the run

Read the target's git conventions, then collect one initial snapshot:

```bash
.agents/quenching/bin/cq git conventions --json
git status --short
git diff --name-status
git diff --cached --name-status
git ls-files --others --exclude-standard
```

Treat tracked edits, staged edits, deletions and untracked files as the pending worktree. A clean
snapshot reports a no-op and ends. An unmerged status (`UU`, `AA`, `DD`, `AU`, `UA`, `DU` or `UD`),
or a file that cannot be read safely is a pre-staging refusal: report the paths and keep every
pending change in place. Read the content of each eligible file before staging, with a ceiling of
1 MiB per file and 10 MiB total. Scan content, not only filenames, for private-key headers and
credential assignments (`api[_-]?key`, `token`, `secret`, `password`, `client[_-]?secret`); on a
match report only the path and pattern, never the value. A size, unreadable-file or secret match is
also a refusal.

**Done when:** the initial path lists, convention layer and safety disposition are reported, with
no `git add` run before every guard passes and no bypass path available.

### 2. Read and classify every pending path

Read the complete current content of every eligible file with `Read`; for a deletion, read its
complete staged or unstaged diff instead. Use the status and diff together when a path has both
staged and unstaged content, because this command commits the path's complete current state.
The per-file and total-byte ceilings and content scan from step 1 still apply to the bytes read
here.
Classify each path by the single concern its change serves. Keep the classification in the current
run; no external plan or confirmation is needed.

**Done when:** every eligible path has a recorded classification, and no sensitive value appears in
the report.

### 3. Form atomic groups and resolve subjects

Create the smallest coherent groups by logical affinity. Put every eligible path in exactly one
group, keep unrelated concerns apart, and give each group one short imperative subject. Apply an
explicit `$ARGUMENTS` prefix first, then `gitConventions.commitSubject`, then the target's declared
git standard, and finally the fallback `chore: <short English imperative>` when no target rule
applies. The subject describes the group's concern, not its file list.

**Done when:** every group has a disjoint explicit path list and one resolved English subject.

### 4. Confirm, then stage and commit each group

Before the first `git add`, show the full group table, paths, diffs, subjects and the two byte/secret
guards' clean result. Put one decision sheet ([specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md) §1; A carry out, B decline) on whether to carry out the listed commits, and end the turn;
decline leaves every pending path untouched. Then, for each group in dependency order, quote each
path and stage only that list:

```bash
git add -- <path-1> <path-2>
git commit -m "<resolved subject>"
git log -1 --format="%H %s"
```

Execute the sequence immediately after its group is resolved. Hooks stay enabled. If staging fails
or a commit fails, stop the sequence at that group, leave earlier commits intact, preserve the remaining
worktree changes, and report the command output. Continue only on a later invocation after the
failure has been fixed.

**Done when:** each successful group has one new commit whose logged subject and paths match the
resolved group, or the first failure has stopped the run with its residue named.

### 5. Report the result and final state

List the initial disposition, every created commit with its subject and paths, the failed group if
any, and the final `git status --short`. Report a clean final tree only when the final status proves
it; report remaining changes and the stopping reason otherwise.

**Done when:** the report accounts for every initial path and the final status has been collected
after the last commit or refusal.

## Invariants

- Stage paths explicitly with `git add --`; the command never uses `git add -A`, `git add .`, or an
  equivalent broad selector.
- Keep hooks and signing enabled; never use `--no-verify` or `--no-gpg-sign`.
- Create new commits only; never amend, reset, rewrite history or force-push.
- Keep secrets and credential values out of both commit messages and the report.
