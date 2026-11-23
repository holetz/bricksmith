---
allowed-tools: Bash(cq git base:*), Bash(cq git conventions:*), Bash(cq git state:*), Bash(cq git push:*), Bash(cq git pr create:*), Bash(cq git pr probe:*), Bash(cq git pr record:*), Bash(cq specs config:*), Bash(cq specs status:*), Bash(cq specs show:*), Bash(cq components read:*), Bash(gh repo view:*), Read, AskUserQuestion
---

# /quenching-git pr-create — push and open the pull request

**Input**: `$ARGUMENTS` — a spec id (derives title, body and the provider's issue/work-item
link), or free text to use as the PR title. Append `remote:<name>` to select a remote; omitted
→ `origin`. Omitted title → ask. The word `autonomous` is the human's pre-answer to step 4's
confirmation, given by a conductor launched with `--autonomous`: see step 4.

Opens the PR and **stops there** — merging is `/quenching-git merge`'s, on its own confirmation,
except in an `autonomous` orchestrated run, where the `spec-orchestrator` merges the PR
of a spec the next wave depends on.
The route follows `.agents/quenching/bin/cq specs config --json`, and both go through `.agents/quenching/bin/cq git pr` verbs with a fixed argv:
`github` runs `gh` underneath, `azure-boards` runs `az repos` with no `--auto-complete` and no
`--bypass-policy`; an unknown or unauthenticated provider has no route here and stops plainly, rather than being treated
as a GitHub repository.

## Workflow

### 1. Confirm the route exists, and resolve the base
```bash
.agents/quenching/bin/cq specs config --json
.agents/quenching/bin/cq components read .agents/quenching/references/git/conventions.md \
  --sections "§The declared-directive layer"
.agents/quenching/bin/cq git conventions --json   # `config.prTitle`, `config.prBody`
.agents/quenching/bin/cq git state --json   # `remotes`: every remote and its URL
.agents/quenching/bin/cq git base --json
.agents/quenching/bin/cq git pr probe --json   # the provider's read-only probe: exit 0 route, exit 1 `reason`
```
Confirm the selected remote's host matches the configured provider. A missing/mismatched origin,
`NO-ROUTE`, or a probe exiting 1 (`no-route`, `unauthenticated`) → say plainly there is no PR route
here and stop.
`.agents/quenching/bin/cq git base --json` supplies the base and `isDefault`. **Done when:** the provider route, the base
branch, and which layer governs `prTitle` and `prBody` are all resolved.

### 2. Resolve title, body and the provider link
A spec id in `$ARGUMENTS` → read its provider-owned status once, then read the named sections once:
```bash
.agents/quenching/bin/cq specs status --spec "<id>" --json
.agents/quenching/bin/cq specs section "<id>" \
  "Problem,Proposal,Impact,Validation,Tasks" --json
```
The status payload supplies the spec `title`, `path`, `branch` facts and task counts. The section
payload supplies the text. A caller without the `.agents/quenching/bin/cq specs section` grant (the `spec-orchestrator`) reads
the same sections through `.agents/quenching/bin/cq specs show --spec "<id>" --full`, a verb with no write flag.

§The declared-directive layer's table governs each of the two artifacts independently:
`config.prTitle` and `config.prBody` from step 1 where declared, else the target's docs, else the
default builder below. **The declared directive says how to shape the text, never what facts to
invent** — it is followed over the same spec material, and a section the spec does not carry stays
absent under any directive.

The default builder, and the fallback under a directive that shapes only one of the two: the spec
title; the non-empty `Problem`, `Proposal`, `Impact` and `Validation` sections under headings with
those names; a `Tasks` summary using the status counts (`checked`/`total`); and the resolved
branch facts when present, in that order.
Omit an absent or empty optional section — never replace it with invented prose.
Build the body whole on both providers, and never shorten it to fit. On Azure the API refuses a PR
description longer than 4000 characters, and markdown does not change the count. Past that limit,
`.agents/quenching/bin/cq git pr create` puts the body cut at the last `\n## ` section boundary
that fits into the description, with the closing note "Corpo completo no primeiro comentário deste
PR.". Once the PR exists, it posts the whole body as the PR's first comment thread (markdown,
closed). The payload then carries `descriptionTruncated` and `bodyComment`. A failed comment does
not undo the PR: the verb still exits 0, and its `findings` carries
`git-pr-azure-comment-failed`, which step 6 reports.

The status `path` is the provider locator. On `github`, the spec id is the issue `<n>`: take it from
the status `id`, never from `path`, and append exactly `Closes #<n>` to the generated body. On `azure-boards`, its trailing number is a
work item `<n>` and pass it as `--work-item <n>` to `.agents/quenching/bin/cq git pr create`; do not invent a `Closes #<n>` sentence.
Free text → that text is the title; ask for a body and, when applicable, whether an issue/work item
should be linked. **Done when:** the title, body, provider-native link (or its absence), and base
are fixed.

### 3. State the link's real effect before pushing
For `github`, **measured**: `Closes #<n>` populates `closingIssuesReferences` only when the PR's
base **is** the repository's own default branch; otherwise it cross-references the issue but does
not close it on merge. State that case from `isDefault`. For `azure-boards`, state that the native
`--work-item <n>` association will be attached to the PR; `--transition-work-items`, when
chosen, asks Azure to transition linked work items when the PR is completed; and
`--delete-source-branch`, when the separate deletion offer is accepted, asks Azure to delete
the source branch after the PR is completed and merged. Preserving the source branch is the default.
**Done when:** the provider-native link and branch-deletion effects are stated before publication.

### 4. Push and open, on one confirmation
**With `autonomous` in the input**, the confirmation is already given: state the remote (`origin`
unless `remote:<name>` was passed), the branch and the title (the spec's title, verbatim), skip the
question, and run the commands below. The token covers exactly those three and nothing
else: never add `--delete-source-branch`, and an absent title or a missing spec id is still
asked, never invented. Without the token, the confirmation below stands.

Show the remote, the branch name it pushes under, and the title/body, and put them on a
decision sheet ([specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md) §1; A confirm, B decline) that ends the turn — and a sub-agent holds no `AskUserQuestion`, so without `autonomous` it returns `STATE: blocked`, `NOTE: needs-human:` carrying the sheet (§1):
For Azure, show the work item id, whether the separate source-branch deletion offer was accepted
(`--delete-source-branch`), and whether `--transition-work-items` is included in the
command the human is confirming. The deletion offer defaults to preserve the branch.
```bash
.agents/quenching/bin/cq git push --branch <branch> --remote <remote> --json
.agents/quenching/bin/cq git pr create --base <base> --head <branch> --title "<title>" --body "<body>" [--spec <id>] --json
# azure-boards only, appended to the same call:
#   [--work-item <n>] [--transition-work-items] [--delete-source-branch]
```
The verb selects the host from the configured provider and refuses the Azure-only flags on GitHub.
Its payload carries `number`, `url` (the `webUrl`) and, on Azure, `apiUrl`: show `webUrl` as **Link para revisão** and
`apiUrl` as **API URL**. For Azure, `webUrl` is
`repository.webUrl/pullrequest/pullRequestId`, while the response's `url` remains `apiUrl`; never
show a URL containing `/_apis/` as the Link para revisão. The target branch is explicit
on both routes: `--base`, which may not be omitted. The source is explicit too: `--head <branch>`, so the PR leaves the branch resolved in step 1 and never the one the cwd happens to hold; run from a worktree, `cd <worktree> && .agents/quenching/bin/cq git push --branch <branch> …` carries the push. `.agents/quenching/bin/cq git push` publishes
`refs/heads/<branch>` under its own name with upstream and never forces it (only the spec store branch, synced after it, is rewritten under `--force-with-lease`); `.agents/quenching/bin/cq git pr create`
returns the PR's `number` and `url`, and with `--spec <id>` stamps the spec's `pr` record with them
(step 5), reporting it as `pr` in its payload; exit 1 with `reason: stamp-failed` means the PR exists and the record does not. Read
the created PR's `id`, `webUrl` and `apiUrl` from the normalized JSON/CLI result. **Done when:**
the PR exists, or the
push/create failed and its error is reported verbatim.
**Done when:** the provider-specific publication succeeded or its failure is reported.

The PR's number and URL are a git fact no derivation reproduces, which is why step 5 stamps them
rather than leaving them to be read back later — the same admission test the `branch:` and `merge:`
records pass. A target that carries `standards/workflows/plan-git-record.md` states it there, under
§Three frontmatter records.

### 5. Stamp, with an ID
On both providers the verb of step 4 already stamped it: pass `--spec "<id>"` there and skip this
step. The verb checks `--spec` (it exists, and owns `--head`) before the PR is created, so a wrong id
opens nothing, and it writes the record through `.agents/quenching/bin/cq specs record`'s function, so the `spec-orchestrator`, which holds no spec write, stamps it too; exit 1 with `reason: stamp-failed` means the PR
exists and the record does not. A PR opened outside the verb is stamped with
`.agents/quenching/bin/cq git pr record --spec "<id>" --head "<branch>" --number <provider-pr-id> --url <webUrl> --json`.
`pr:` is **write-many** — a later PR on the same spec is a new fact.
No ID → nothing to stamp; report the PR number and URL only. **Done when:** the record is stamped
(with an ID) or the report carries the PR's own facts (without one).

### 6. Report
State the provider, PR id, **Link para revisão** (`webUrl`), **API URL** (`apiUrl`), base it targets, the effect of the native issue/work-item
link (step 3), and **which layer governed the title and the body**. **Done when:** all five facts
are named.

## Invariants

- Never route an Azure repository through `gh`, or a GitHub repository through `az`.
- Publish to the selected remote (`origin` when omitted); never silently substitute another
  remote or treat a branch name as one.
- Never omit `--base` on `.agents/quenching/bin/cq git pr create`, and never open the PR with a raw `gh` or `az` call.
- Never pass `--delete-source-branch` by default. Offer it as a separate choice, explain that
  it removes the source branch after completion, and include it only when the human confirms that
  exact deletion.
- Never push or open a PR without the human's confirmation on the exact remote, branch and title
  shown — the `autonomous` input word is that confirmation, pre-given, and only for those three.
- Never claim a provider-native issue/work-item link closes or transitions anything beyond the
  host CLI's documented effect; report the provider and the selected base plainly instead.
- Never install `docs/standards/git/**` into the target, and never write `gitConventions` into its
  `.agents/quenching.json`; a declared convention is read here, never written back.
