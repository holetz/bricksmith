---
allowed-tools: Bash(cq git base:*), Bash(cq git conventions:*), Bash(cq git specs:*), Bash(cq git state:*), Bash(cq git worktree acquire:*), Bash(cq git worktree add:*), Bash(cq specs config:*), Bash(cq specs status:*), Bash(cq components read:*), Read, AskUserQuestion
---

# /quenching-git branch — take isolation before writing code

**Input**: `$ARGUMENTS` — optionally a spec id (stamps that spec's `branch:` record and its
`quenching-specs:` marking) or a short name for standalone work with no spec behind it. Omitted →
ask what the isolation is for.

Cuts a **worktree** before the first line of code — the form is the default and is taken unasked; a
plain branch in the current checkout only when the human asks for it. The mechanics — the names,
`worktreeSetup`, `sharedPaths` and the two records a taken isolation leaves behind — are owned by
[git/isolation.md](.agents/quenching/references/git/isolation.md), cited below rather
than restated; this command is its standalone entry point for a caller with no build loop of its own.

**Grants.** `allowed-tools` holds only the `cq` verbs a caller that holds no `Skill` also holds, so the body stays followable from a `tools:` list of `cq` verbs and a call outside them here would leave the step blocked. `git checkout -b` and the `.agents/quenching/bin/cq specs record` stamps go through the normal permission prompt. The declared `worktreeSetup` runs inside `cq git worktree add`, so its grant covers it: the declaration in the target's tracked configuration is the consent ([git/isolation.md](.agents/quenching/references/git/isolation.md)).

## Workflow

### 1. Load the rules and the state in one read
```bash
.agents/quenching/bin/cq components read .agents/quenching/references/git/isolation.md \
  --sections "§Isolation happens on the way into a build" --sections "§Branch and worktree names" \
  --sections "§Recording the isolation"
.agents/quenching/bin/cq components read .agents/quenching/references/git/conventions.md \
  --sections "§The declared-directive layer" --sections "§The read-if-present rule"
.agents/quenching/bin/cq git conventions --json   # `config.branchName`, or absent
.agents/quenching/bin/cq git state --json           # `branch`, `status` (porcelain), `staged`, `remotes`
.agents/quenching/bin/cq git base --json
.agents/quenching/bin/cq specs config --json        # `worktreeSetup`, `sharedPaths`, or their empty values — exit 0 either way
```
`status` non-empty → **an authorized cut is the one exception, and the authorization is the
caller's word, never this command's inference.** The autopilot's step 1 authorizes a worktree cut
from `origin/<base>` when the base is dirty and no fix spec may absorb it; a caller that says so in
its own words ("authorized cut", "cut from origin/<base> with the base dirty") proceeds. Name the
dirty paths first, say plainly that they stay in the base and never enter the new worktree, then
cut in step 3 from the remote tip, which carries none of them. The exception is **worktree-only**:
the Branch form runs `git checkout -b` in this checkout and would carry the dirt into the first
commit on the new ref, so a dirty tree there stays a refusal. Anything short of that explicit
authorization → refuse, name the offending paths, and stop; isolating a dirty tree carries whatever
was already sitting there into the first commit on the new ref, silently.
**Done when:** the rules are loaded, the tree is confirmed clean or its dirty paths were named
under an authorized cut, and the base branch is resolved.

### 2. Resolve the ID, when one was given
`$ARGUMENTS` naming a spec → `.agents/quenching/bin/cq specs status --spec "<id>" --json` and read its `records.branch`.
Already stamped → report it (`base`, `work`) and **stop**; a second isolation for an already-isolated
spec is a no-op, not a fresh cut. No record → continue to the cut below, carrying the ID through
to step 4. `$ARGUMENTS` naming no spec (or empty) → continue with no ID; nothing gets stamped in
step 4, only the worktree itself. **Done when:** the ID (or its absence) and any
existing `branch:` record are resolved.

### 3. Take the worktree — only from the base, and with nothing to ask
If the current branch is already different from the resolved base, report that the checkout is
already isolated and carry it forward; do not cut a second branch or worktree. If it is the base,
the form is **Worktree**, unasked, and the worktree is a **slot**: a reusable `../<repo>-slot-<n>`
that outlives the spec, so its `.venv` and caches are not rebuilt — or rescanned by an on-access
antivirus — for every spec. State the base branch (from step 1), the branch name that will be
cut — `config.branchName` from step 1 governs it where declared, else the target's docs, else
`plan/<id>-<handle>` with an ID and a kebab-case name derived from `$ARGUMENTS` or asked for without
one — and the worktree path, with its cost in the same block: a fresh checkout carries only what
git tracks — no installed dependencies, no `.env`, no venv, no build output; a reused slot already
holds them, and only a new slot pays that cost. `worktreeSetup`
non-null is displayed **verbatim** there and runs as part of the cut; `sharedPaths` non-empty is
displayed verbatim too. There is no decision sheet on the form.

**A plain branch is the human's override, and only theirs.** When the request says so in its own
words — "no worktree", "just a branch", "use this checkout" — run `git checkout -b <branch>` in this
checkout instead, and skip the setup: there is no new tree to prepare. Never offer the two as a
choice, never pick Branch because the target looks awkward, and never fall back to it after a
`git worktree add` failure: a failed cut is reported verbatim and the run stops, because a silent
branch would leave the build in the main checkout. **Done when:** the form is resolved — Worktree by
default, Branch only on an explicit request — or the run stops on a git error.

### 4. Take a slot, run the setup, and stamp
Take a slot; a failure is reported verbatim and nothing is stamped. **The branch starts from the
remote base**, because a PR merged through `gh` moves no local ref and the local `<base>` may miss
the dependency the run waited on:
```bash
.agents/quenching/bin/cq git worktree acquire --branch <branch> --base <base> [--spec "<id>"] --json
```
`acquire` locks a free clean slot (`git worktree lock` is the mutex, its reason naming spec,
session, host and date), else cuts a new one while fewer than `shared.worktreeSlots.max` (12) exist,
else frees a held slot whose branch is already merged; any other worktree is never counted and
never taken. At the ceiling it refuses `git-worktree-slots-full` and names every holder with the
`release` command that frees it: rerun with `--wait` (it polls every `pollSeconds` up to
`waitMinutes`), and on `git-worktree-slots-timeout` (`needsHuman: true`) **stop and tell the human
the limit was reached** — never fall back to a one-off worktree or a branch. The payload's `path`
is the worktree from here on. `add` (`--path <path>` instead of a slot) cuts a one-off worktree
only when the human asks for one by path.
With an ID from step 2, pass `--spec`: the verb refuses (exit 2, nothing cut) a spec that does not
exist or does not own `<branch>`, and after the cut stamps the `branch:` record itself
(`branchRecord` in the payload; a failed stamp is exit 1 and leaves the worktree). The verb fetches `origin <base>`, switches the slot to `<branch>` — an existing one, or one cut from
the fetched tip resolved to a commit — so
the branch has no upstream and a push without an explicit destination never targets the base —
and links the declared shared paths inside the slot. A fetch error is exit 2 and stops the
run; only a missing `origin` falls back to the local `<base>` (`fromRemote: false`, the
`NO-REMOTE` line), and the report says so. A link failure is exit 1 with `linkRefusal`: it does not
undo the worktree, and the verb does not run `worktreeSetup`. When the link succeeds, the verb
runs a declared `worktreeSetup` once with cwd inside the new worktree, printing the command
verbatim and its output on stderr (`setup` in the payload: `command`, `exit`, `ok`); a failing setup
is exit 1 and does not undo the worktree — report it apart from the cut. Do not run the setup a
second time; `--no-setup` skips it only when the human asks. **A worktree outside the project
directory is not the shell's cwd:** the harness may return the cwd to the base checkout after every
command, so from here on each command carries the worktree explicitly — `cd <worktree> && …` in
the same call, `git -C <worktree>`, `.agents/quenching/bin/cq --root <worktree>` — or a cwd-dependent tool (`cq` without
`--root`, `git diff`, tests) reads the base checkout and reports green over a tree that did not
change. `.agents/quenching/bin/cq git …` takes no `--root` on its write verbs
(a usage error): it always runs as `cd <worktree> && .agents/quenching/bin/cq git …`. Then, only with an ID from step 2
(the Worktree form already stamped `branch:` through `--spec`; **Branch** form stamps it by hand):
```bash
.agents/quenching/bin/cq specs record "<id>" branch --set base=<base> --set work=<branch>   # Branch form only
.agents/quenching/bin/cq git specs <branch> --add "<id>" --json
```
**Done when:** the worktree (or the requested branch) exists on disk, the setup ran and was reported
(worktree only), and — with an ID — `branch:` and the `quenching-specs:` marking are stamped.

### 5. Report
State the form taken, the branch name and **which layer governed it**, the worktree path, the setup's
result (if run), and what was stamped. Under an authorized cut, also state the dirty paths that
stayed in the base — the round reports them, and nothing of them is in the worktree.
**Done when:** the summary names every fact step 4 produced.

## Invariants

- Never isolate over a dirty tree, except the authorized worktree cut from `origin/<base>`: there
  the dirty paths are named, reported as staying in the base, and never enter the new worktree. The
  Branch form in a dirty checkout is always a refusal.
- **Take a worktree by default, unasked.** No decision sheet on the form, no offer, and no sniffing
  the target repo to pick something else; a plain branch only when the human asks for it in the
  run's own words.
- Never fall back to a branch or the base after a failed `git worktree add` — report the failure and
  stop.
- Never leave the work on the base — no form of the isolation commits there.
- Stamp `branch:` only once per id — a record already present is read, never overwritten.
- Never install `docs/standards/git/**` into the target, and never write `gitConventions` into its
  `.agents/quenching.json`; a repo's own conventions are read, never written, by this command.
