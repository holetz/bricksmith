---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(python3:*), Bash(git remote get-url:*), Bash(git branch:*), Bash(gh repo view:*), Bash(gh pr view:*), Bash(gh api graphql:*), Bash(az repos pr:*), Bash(az devops invoke:*), Read
---

# /quenching-git pr-status — read one pull request snapshot

**Input**: `$ARGUMENTS` — `spec:<id>`, a provider PR number or URL, or omitted for the current
branch's PR.

Read the provider and field contract in
`.agents/quenching/references/git/pr.md`; it owns the query shapes, unknown-state
rules and recommendation vocabulary used below.

## Workflow

### 1. Resolve the provider and selector

Read the specs backend, the `origin` URL and the current branch. A `spec:<id>` selector reads the
spec status and its recorded PR; a provider URL or number selects that PR; an omitted selector
uses the PR for the current branch. Confirm the host matches the configured provider before using
`gh` or `az`. **Done when:** the provider route and one selector are known, or a refusal names the
missing, ambiguous, mismatched or unauthenticated route.

For the initial facts, use the bundled `cq` route and the local git identity:

```bash
.agents/quenching/bin/cq specs config --json
git remote get-url origin
git branch --show-current
```

For `spec:<id>`, also read `.agents/quenching/bin/cq specs status --spec <id> --json` and use only its recorded PR
number or URL. Do not substitute an issue number for a PR number. **Done when:** the provider
route and one selector are known, or a refusal names the missing, ambiguous, mismatched or
unauthenticated route.

### 2. Collect the provider snapshot

Read the GitHub or Azure section of the bundled reference and run only that provider's read-only
queries. Fetch PR identity, source/base, state, checks, reviewers, approvals, mergeability and
review threads. Follow pagination and record truncation. **Done when:** the provider response is
complete, or its not-found, authentication, permission or unknown cause is preserved.

### 3. Normalize the evidence

Build one report with `provider`, `pullRequest`, `source`, `base`, `checks`, `approvals`,
`unresolvedThreads`, `mergeability`, `spec` and `nextStep`. Keep provider-specific fields under
their provider section. Represent unavailable evidence as `unknown` plus its cause; do not infer
approval, green checks, clear threads or mergeability. **Done when:** every common field is present
with a fact or an explained unknown.

Within `pullRequest`, normalize the identity as `id`, `webUrl` and `apiUrl`. Render `webUrl` with
the label **Link para revisão** and `apiUrl` with **API URL**. The first is the browser address a
human opens; the second is the technical endpoint. A value containing `/_apis/` is never rendered
as Link para revisão; if no safe browser link can be derived, report it as `unknown` with its cause.

### 4. Choose the textual next step

Apply the reference's recommendation rules to the normalized facts. Point to
`/quenching-git pr-review`, `/quenching-git sync`, `/quenching-git merge` or no action as the facts
warrant; recommendations are prose only. **Done when:** one next step is stated with the fact that
caused it, or the refusal is the next step.

### 5. Self-check and report

Confirm that the output names the provider, PR id/URL, source, base, checks, approvals, unresolved
threads, mergeability, spec link and recommendation, and that no write-capable command was run.
Report pagination, unknown causes and provider errors alongside the snapshot. **Done when:** the
read-only snapshot is returned and its evidence boundaries are explicit.

## Invariants

- Use only the provider selected by `.agents/quenching/bin/cq specs config --json` and the matching `origin` host.
- Keep the normalized **Link para revisão** and **API URL** labels distinct; never expose an
  Azure `/_apis/` endpoint as the human review link.
- Preserve not-found, authentication, permission and unknown states; none is a green result.
- Never push, create, edit, close, merge or resolve anything during this status read.
