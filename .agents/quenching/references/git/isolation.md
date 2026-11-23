# Taking isolation, and recording it

How a checkout gets onto its own branch or worktree before code is written, and what gets recorded
once it has — the default form, the names, and the two records that survive it: the
`branch:` frontmatter record and the branch's own description line. Read
[conventions.md](.agents/quenching/references/git/conventions.md) first: the read-if-present
rule and the two prohibitions bind here too.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## Isolation happens on the way into a build

<!-- rules -->

Isolation was a command of its own for one release, offered at any stage. It is now a **step inside**
`/quenching-specs execute`, taken once, before the first task is written — and taken only when the
session is standing on the repository's base branch:

| Where the session stands | What `execute` does |
| --- | --- |
| **on** the spec's work ref | nothing — the spec is isolated, and the loop starts |
| on any other non-base branch | **adopts it** as `work`, stamps `branch:`, and starts the loop |
| on the base, work ref alive and unclaimed | cuts a **worktree** over it, unasked, and starts the loop |
| on the base, no work ref | cuts a **worktree** from the remote base, stamps `branch:`, and starts the loop |

**The build always runs in a worktree, and the worktree is the default.** Standing on the base is
the only case this step exists for, and its answer is fixed: a worktree cut from the remote base,
with no question in front of it. The question had one safe answer — every other form ends with the
code in the main checkout — so asking it every build only cost the turn it took to answer it.

Standing anywhere but the base means the human already answered the isolation question at checkout,
so asking again buys nothing and costs the turns it takes.

**A plain branch is an override, never an offer.** It stays reachable when the human asks for it in
the run's own words — "no worktree", "use this checkout" — and for a build that already started on a
branch someone checked out by hand. It is never offered beside the worktree, no default leads to it,
and a failed `git worktree add` is a **stop**, never a fallback: a silent branch cut would leave the
build in the main checkout, which is the outcome the default exists to prevent.

**Isolated means "this checkout is on the work ref", never "the ref exists".** The two come apart in
one ordinary case — the base checked out, `plan/<id>-<handle>` sitting one branch over from an earlier
session, nobody holding it — and treating existence as the answer sends the loop to build and commit
onto the base, which is precisely what the isolation is for. The work ref itself is the `branch` record's
`work` when one is stamped, else `plan/<id>-<handle>`; a live ref held by another worktree is a **stop**,
not a cut, because two checkouts building one spec fork it.

`/quenching-specs create` and `/quenching-specs develop` take no branch at all — they write into
`plans/` and stay wherever they were run. That is a narrowing from the retired command, and the
trade is deliberate: the isolation costs a worktree, and the stage where it reliably pays is the one
about to write code.

**Asking "am I isolated?" writes nothing and is answered elsewhere.** `.agents/quenching/bin/cq specs next --front`
reads the live ref and reports it, and demotes a spec whose branch is checked out somewhere else.
`/quenching-specs status` reads the frontmatter records and deliberately never asks git.

**Merging is not here.** It stays inside `/quenching-specs conclude`, behind that command's review and
archive gates — a merge invocable on its own could run against a spec nobody reviewed and nothing
archived, and would have to duplicate every refusal `conclude` already owns.

<!-- rationale -->

**On `plan/<id>-<handle>` as the default name.** A branch named to it is greppable against
`.agents/quenching/bin/cq specs list`,
and `git branch --list 'plan/*'` is the list of work cut by the default. A human who already checked
out `fix/isolate-flow` or `123-my-branch` before running `execute` gets that branch recorded, not a
second one cut beside it.

**On putting the worktree beside the repo.** Inside the repo it lands in the tree the spec is about
to change, and shows up in every `git status`, every glob, and every checker this plugin runs. A
worktree leaves the main checkout untouched, which is what lets several specs be built at once and
what keeps satisfying the clean tree the loop demands of itself. A default that changes from repo
to repo cannot be documented in one sentence, and guessing somebody else's build is how a default
becomes a silent trap. So the form is fixed, and the override is what the human says rather than
something the command discovers.

**On asking nothing.** The offer was a question whose answers did not move the work: every form it
carried ended with the code in a checkout of its own, and the one form that did not was removed for
producing builds on the base. What the question still cost was real — a turn, a decision sheet, and
a confirmation to read before any code moved — so the form the command recommends is now the form
it takes, and choosing otherwise is an explicit instruction rather than an answer to a menu.

**The configuration has a neutral home.** The target's `.agents/quenching.json` (or the legacy
`.agents/quenching.json`) carries `worktreeSetup` alongside the provider placement settings, so the
same isolation contract applies
regardless of where the spec is hosted. Whether the command resolves can only be judged against
the new worktree's path, which `.agents/quenching/bin/cq specs` is never told.

**On stamping `branch:` for a branch this plugin never cut.** A human may have checked one out by
hand before running `execute`, and a spec built there with nothing stamped leaves
`/quenching-specs conclude` unable to say what it merges into.

**On removing the worktree after a merge.** Isolation nobody cleans up accumulates: directories
beside the repo, each pointing at an already integrated branch, none of them obviously safe to
delete.

## Branch and worktree names

<!-- rules -->

```
plan/<id>-<handle>
```

The **default** when the isolation cuts the worktree. The **ID leads and
is the whole identity** — `plan/974` resolves the spec on its own; the `<handle>`, kebab-case from
the title, rides along so `git branch` reads as names rather than as numbers and is never parsed
back. `.agents/quenching/bin/cq specs next --front` ranks on whether `plan/<id>-<handle>` is **alive**, so a branch named
to the default is what tells that ranking this spec is already under way without a stamped `branch`
record.

**The name is a default, never a contract.** When `/quenching-specs execute` starts on a branch that is not
the repository's base — whatever it is named — that branch is **adopted** as `work` outright: no
rename, no refusal, and no requirement that it match `plan/<id>-<handle>`.

A worktree goes beside the repo, never inside it, and it is a **slot**:

```bash
.agents/quenching/bin/cq git worktree acquire --branch plan/<id>-<handle> --base <base> --spec <id>
```

### Worktrees are slots

<!-- rules -->

A slot is a registered worktree at `../<repo>-slot-<n>` that outlives its spec. `acquire` locks one
and switches it to the spec's branch; `.agents/quenching/bin/cq specs reconcile --write` releases it
after the merge — detached at the remote base, unlocked, `.venv` and every regenerable cache kept.
`.agents/quenching/bin/cq git worktree slots` lists who holds each.

- `shared.worktreeSlots.max` (default 12) is a **ceiling**: at it, `acquire` refuses, or with
  `--wait` waits, and a timed-out wait is reported to the human. No worktree is cut past it.
- At the ceiling, `acquire` takes a held slot only when its branch has a commit of its own in its
  reflog (`commit`, `cherry-pick`, `revert`) and its tip is in the base, its tree is clean, it holds
  no ignored content `release` would refuse on, and no process has its cwd inside it. A branch that
  only fast-forwarded to the base is never taken. The taken slot's `.quenching/codex/` is archived
  in the primary checkout, as `release` does.
- `shared.worktreeSlots.min` (default 4) is a **floor**: `/quenching-git cleanup` never offers
  the lowest-numbered free slots up to it (`slot-floor`); free slots above it are candidates.
- A slot is released only clean: a dirty tree, or ignored content no `shared.regenerablePaths`
  entry covers, keeps it held — nothing of one spec leaks into the next, and nothing is committed or
  discarded to free it.
- Only slots count. A worktree under any other name — cut before slots, or by the harness — is
  listed and never counted, never taken; a merged one is removed with `retire`.

<!-- rationale -->

A fresh worktree costs a scan of every file an on-access antivirus sees — tens of thousands with a
venv — and a reinstall; a branch switch inside a warm slot rewrites only what differs.

### The worktree is the form

<!-- rules -->

`/quenching-specs execute` takes a **worktree**, unconditionally and unasked, whenever the
build starts from the base. No heuristic sniffs the target for `package.json` or `.venv/`: what the
report states is the **cost** — a fresh checkout carries only what git tracks, no installed
dependencies, no `.env`, no venv, no build output — and the `worktreeSetup` command that closes it
where the target declares one.

**A plain branch in the current checkout is taken only on an explicit human request**, in the run's
own words. It runs `git checkout -b` in the checkout the session already has, stamps the same
`branch:` record, and skips the setup: there is no new tree to prepare.

### A target may declare a setup command

<!-- rules -->

`.agents/quenching.json`, at the repo root, under the `worktreeSetup` key:

```json
{"worktreeSetup": "./scripts/wt-setup.sh"}
```

The same file may declare repo-relative paths that must survive across checkouts, under
`sharedPaths`:

```json
{"sharedPaths": ["trabalho"]}
```

The isolation displays the declared list **verbatim** before it links anything. After
`git worktree add`, `.agents/quenching/bin/cq git worktree link` materialises each path as a link to its sibling store
before the setup command runs. A setup script must not re-create, replace or remove a path declared
in `sharedPaths`; the link command owns that path for every checkout.

The sibling store sits beside the primary checkout: `<repo>.<path>`, the path's separators turned
into dots and its leading dots dropped (`.tmp` → `<repo>.tmp`, `a/b` → `<repo>.a.b`). A store left
under the older name with the dot kept (`<repo>..tmp`) is renamed to the new name by the next
`link` when the new one does not exist yet. A link pointing at the old name is then repointed. When
both exist, `link` refuses with `git-worktree-store-conflict` and moves nothing; the human merges
them.

See
[plugin-configuration.md](.agents/quenching/references/specs-align/plugin-configuration.md)
§The envelope and recognised namespaces.

Read by `.agents/quenching/bin/cq specs config --json` (exit 0 whether or not anything is declared) and run **once** by
`.agents/quenching/bin/cq git worktree add`, immediately after the worktree is cut and linked, with **cwd inside the
new worktree** — the tree lacking the dependencies is the tree that must install them. The verb
reads the declaration from the worktree it just cut, the fetched base, never from the caller's
tree, so an uncommitted edit cannot choose what runs; `--no-setup` skips it. A raw
`git worktree add` (a second worktree inside a build) runs it by hand, the same way.
`.agents/quenching/bin/cq specs` reads the value and never executes it.

No file, no key, or a command that does not resolve all mean **no setup**, and none of them is a
finding — most repos declare nothing, and declaring nothing must cost nothing. `.agents/quenching/bin/cq specs doctor`
reports only the two ways it can be *wrong*, both `warn`: `sp-config-unknown-key` and
`sp-config-unparseable`. They exist for the one real failure mode of a machine-read config —
`worktree_setup` written where `worktreeSetup` was expected, and silence afterwards.

**Its consent is the declaration, displayed verbatim before it runs.** The command sits in the
target's own tracked configuration, and the isolation displays it **verbatim** in the block that
reports the cut: running it is the default, under `autonomous` too, where no human reads the display
and the declaration is the whole authorisation. No second prompt, no remembered
"this repository is authorised" state. A failing setup is reported and **never undoes the
worktree**.

## Recording the isolation

<!-- rules -->

```bash
.agents/quenching/bin/cq specs record <id> branch --set base=main --set work=plan/<id>-<handle>
```

`base` is whatever was checked out when the branch was cut. It is **not** assumed to be `main`.
`work` is derivable from `git branch --show-current` while the branch is checked out; `base` is
not — **after the merge, git cannot say what the branch was cut from**, which is the whole reason
the record exists and why it is captured while still true.

**Every resolved work ref gets `branch:` stamped**, including a branch this plugin never cut. A
record reading `work == base` is no longer written — no form of the isolation leaves the build on
the base — but records stamped before that, when declining isolation stamped `work` equal to
`base`, still exist, and an absent record and `work == base` remain different claims: nobody has
decided yet, versus a human declined isolation.

**`work == base` means no isolation was taken, and every consumer must read it that way.** It is
not a work ref that happens to be alive: the base is always alive, so anything ranking, diffing or
merging on the mere PRESENCE of the record gets the in-place case backwards. `.agents/quenching/bin/cq specs next` guards
it explicitly, and `/quenching-specs conclude` branches on `work != base` rather than on the record
existing — there is no branch diff to review and nothing to merge when the work never left the base.

**For a branch this plugin cuts, `base` is an observed fact** — it was what stood checked out the
moment the branch was created. **For a branch it adopts, `base` is inferred**, in this order,
stopping at the first that answers:

1. the spec's own `branch.base` record, when one already exists;
2. `git symbolic-ref refs/remotes/origin/HEAD` — the default the remote declares;
3. `git config init.defaultBranch`, and then `main`.

**Show the inference on the same line as the confirmation, before stamping** —
`base: main — inferred; this branch was not cut by this command` — because the record is
write-once and that is the only moment disagreeing with it is cheap. Never derive `base` from
`git merge-base` or `--fork-point`: both answer a **commit**, not a branch name, and a commit
ancestral to three branches identifies none of them.

The record is `writeOnce: true`, and `.agents/quenching/bin/cq specs record` enforces it: a second stamp refuses (exit 2)
naming the value already held. A later run **reads** it rather than rewriting it, and a current
branch that disagrees with `work` is a finding to report, never a value to correct. Never edit the
frontmatter to get past that refusal — the refusal is the rule, working.

**The record is not the signal.** A human may cut `plan/<id>-<handle>` by hand and stamp nothing, and a
record outlives the branch it names. Anything asking "is this spec in flight?" asks git for a live
ref — the record only supplies the ref's name when it is not the default.

### Marking the branch with the specs it built

<!-- rules -->

Beside the `branch:` frontmatter record, `/quenching-specs execute` also marks the branch itself, in a
form that survives outside the spec's own file — a recognizable line in the branch's own
description:

```bash
git config branch.<name>.description
```

the same slot `git branch --edit-description` opens an editor on. After every task's commit, read
the current description and rewrite — never duplicate — the one line it owns:

```
quenching-specs: <id1>,<id2>
```

Any other text already in the description — a line a human wrote by hand — is preserved untouched
above and below it. A second spec built on the same branch appends its native ID to the same line
rather than writing a second one, and the line is rewritten on every task commit, which is what keeps it
current with HEAD without a separate cleanup pass. The read-merge-write, run by
`/quenching-specs execute` after every task's commit:

```bash
.agents/quenching/bin/cq git specs <work> --add <id> --json
```

**Never marked on the base.** The mark exists only when this command controls the branch it is
building on — one adopted or cut by the isolation. A legacy spec recorded with `work == base`
carries no mark, because the base is shared and was never this command's to describe.

The description is tied to the **ref**, not to a worktree's physical directory, so it reads back
identically whether the isolation taken was **Worktree** or the explicit plain **Branch** override.

`/quenching-specs conclude`, without a `--spec` argument, reads this same line to resolve which spec(s)
built the branch it is closing — the record git alone cannot reproduce once the branch is merged and
deleted.

<!-- rationale -->

The branch description was chosen over a dedicated `git config` key, a branch-name convention, or a
versioned file in the branch: it needs no new git plumbing, ties to the ref rather than a directory
or a name a human might rename, and never leaks into the branch's own tree. Its cost is symmetric
with its simplicity — the description is local to the `.git` that wrote it and does not survive a
clone or fetch, which `/quenching-specs conclude`'s own fallback exists to cover when it does not
resolve.
