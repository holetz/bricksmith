# Git conventions shared across the `git` pillar

The one home for the three things every command in the `git` pillar shares regardless of which git
operation it performs — the two layers a target may declare to win before the plugin writes a text,
which of them wins for which artifact, and the two prohibitions that bind every git command on any
repo. `isolation.md`, `commit.md`, `merge.md` and `pr.md` each cite this file instead of restating
any of them.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## The declared-directive layer

<!-- rules -->

A target declares its directives for the texts this pillar writes in `.agents/quenching.json`
(or the legacy `.agents/quenching.json` while it exists), under `gitConventions` — one sub-key per
artifact:

```json
{
  "gitConventions": {
    "commitSubject": "Prefixe o subject com o ticket entre colchetes; imperativo, sem ponto final.",
    "branchName": "feat/<ticket>-<handle-kebab>.",
    "prTitle": "O subject do primeiro commit, sem o prefixo do ticket.",
    "prBody": "Tres blocos: o que muda, como provar, o que fica de fora.",
    "mergeSubject": "Merge <branch> (<strategy>)."
  }
}
```

Every value is **prose an agent follows**, exactly as a `tagCatalog` value is. Nothing interpolates
a placeholder, expands a template, or checks the text that came out — `<ticket>` above is the
target's own notation for its own reader, not a field this pillar fills.

**Resolution is per artifact, and the artifacts never move together:**

| Order | What governs | When |
| --- | --- | --- |
| 1 | the caller's explicit value | `$ARGUMENTS` carries a subject, a title, a branch name |
| 2 | `gitConventions.<artifact>` | the sub-key is declared |
| 3 | `docs/standards/git/**` | §The read-if-present rule, for what the doc covers |
| 4 | the plugin's default | `commit.md` §Commit messages, `isolation.md` §Branch and worktree names |

Both declared layers arrive in **one** read, and it is the same call §The read-if-present rule
already runs:

```bash
.agents/quenching/bin/cq git conventions --json
```

`config` carries the declared directives, `configUnknown` the sub-keys nothing reads; `governs`
and `declared` answer the docs layer and only it. **State in the report which layer governed each
artifact it wrote.**

**Never write `gitConventions` into a target's `.agents/quenching.json`.** Same prohibition as
§What is never done, and for the same reason: a directive the plugin authored stops being the
target's.

<!-- rationale -->

**On the config outranking the target's own standard.** Both are the target's word, so the order
had to be stated rather than discovered. `gitConventions` wins because it is addressed to this
plugin by name and is narrower — one artifact per sub-key, against a document that governs humans
too — and because a key that lost to any `docs/standards/git/**` would be dead in exactly the
repositories most likely to declare both. The standard still governs every artifact the config does
not name, and still governs humans either way.

**On five sub-keys and not one free-form blob.** Resolution is per artifact because a target
routinely has a rule for its commit subjects and no opinion at all about merge subjects. A single
directive would force it to restate the plugin's defaults for the artifacts it does not care about,
which is how a default becomes a rule nobody agreed to.

## The read-if-present rule

<!-- rules -->

Before the first commit of a run, look for the target's own conventions — **once**, and cheaply:

```bash
ls docs/standards/git/ 2>/dev/null
```

| What is found | What governs |
| --- | --- |
| one or more `docs/standards/git/**.md` | **the target's docs**, read and followed verbatim |
| nothing | the defaults below |
| a doc that covers only part (e.g. commit subjects but not merges) | the target's for what it covers, the defaults for the rest |

State in the report which one governed. This rule answers the docs layer only — a
`gitConventions.<artifact>` declared in `.agents/quenching.json` outranks it for that
artifact, per §The declared-directive layer's table.

**Never install a git standard into a target.** Not as a fixup, not as a suggestion applied, not
"so the next run has something to read". If a human wants their conventions written down, that is
`/quenching-knowledge add`, on their word.

An `authority: background` git standard in the target still wins over these defaults. It is an
agreed-but-unproven rule someone wrote on purpose; that beats a plugin's opinion either way.

An omitted commit subject may use the default grammar only when the caller resolves exactly one
spec and one actionable task. An explicit subject takes precedence; missing or ambiguous context
refuses rather than guessing from the staged diff or recent history.

## What is never done, on any repo

<!-- rules -->

This file owns three prohibitions, and all are about **whose** conventions win:

- **Never install `docs/standards/git/**` into a target.** See §The read-if-present rule. A default
  written into the repo stops being a default.
- **Never write `gitConventions` into a target's `.agents/quenching.json`.** Same argument one key
  down; see §The declared-directive layer.
- **Never apply a convention the target did not declare and this file does not name.** A commit
  style inferred from reading `git log` is a guess, and a guess about house style is worse than the
  stated default — it looks deliberate.

Commit hygiene is not this file's to state: `--no-verify`, `--no-gpg-sign`, amending a commit,
force-pushing, `git init` on the human's behalf, and `git add -A` over a task's declared files are
all forbidden by
[specs-execute/execution.md](.agents/quenching/references/specs-execute/execution.md) §The commit and
§The precondition, which own them. They bind every command that touches git here, including the
ones in this pillar.

<!-- rationale -->

Writing `docs/standards/git/**` into a repository that
never asked for it is `/quenching-specs-*` reaching into `/quenching-knowledge align`'s territory, and it converts a default
this file *offers* into a rule the repo now *declares* — which then wins over this file forever,
without anyone having agreed to it.

**On reporting which governed.** "Read the repo's `docs/standards/git/commit-messages.md`"
and "used the plugin default" are different facts about the same commit, and only one of them means
the human's convention was honoured.
