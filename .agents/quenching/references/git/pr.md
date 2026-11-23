# Pull request status — provider facts and normalized snapshot

This reference owns the read-only facts used by `/quenching-git pr-status`. The command resolves
the provider from the specs backend and the `origin` host, then reports the provider's answer
without turning an unavailable field into a positive state.

## Snapshot fields

Every report carries these fields when the provider supplies them:

| Field | Meaning |
| --- | --- |
| `provider` | `github` or `azure-boards` |
| `pullRequest` | normalized `id`, `webUrl`, `apiUrl`, title and state |
| `source` / `base` | source branch and target branch |
| `checks` | check name, status, conclusion and URL when available |
| `approvals` | review decision, reviewer states and requested reviewers |
| `unresolvedThreads` | count, locations and whether pagination was complete |
| `mergeability` | provider value or `unknown` with its cause |
| `spec` | selected spec/issue link, or `unknown` when no link was returned |
| `nextStep` | a textual recommendation derived only from known facts |

`unknown` is a value with a cause, not an empty success. Authentication failures, missing CLI
credentials, permission-limited fields, missing PRs and unsupported provider responses remain
separate in the report. A report with an unknown field never claims that the PR is approved,
mergeable or clear.

## URL normalization

The provider adapter returns exactly three identity fields: `id`, `webUrl` and `apiUrl`.
`webUrl` is the **Link para revisão** shown to a human; `apiUrl` is the **API URL** shown as a
technical endpoint. Never use one label for the other, and never print a URL containing `/_apis/`
as the Link para revisão.

Azure's response `url` is the API URL. When `repository.webUrl` and `pullRequestId` are present,
the adapter constructs the browser link as `repository.webUrl/pullrequest/pullRequestId`; it does
not promote the `url` field into a human link. GitHub accepts its browser `url`/`html_url` and
REST `url` separately, deriving the API endpoint from the repository and PR number only when the
provider did not return one.

## The Azure description limit

Azure DevOps refuses a pull-request description longer than 4000 characters, whatever the
markdown. `cq git pr create` sends a longer body cut at the last `\n## ` section boundary that
fits beside the note "Corpo completo no primeiro comentário deste PR." (raw cut when none fits),
then posts the whole body as the PR's first thread with `az devops invoke --area git --resource
pullRequestThreads` (`status` closed), the argv built in the module. A failed thread is a
`git-pr-azure-comment-failed` finding in the payload; the PR stands.

## Selection and provider route

Read `.agents/quenching/bin/cq specs config --json` and the `origin` URL before
choosing a provider. A GitHub route requires a GitHub host and `gh repo view`; an Azure route
requires a DevOps host and the `az` CLI's detected project/repository context. A host mismatch,
unknown backend or unauthenticated CLI is a refusal with its cause.

The selector is resolved in this order:

1. `spec:<id>` reads `.agents/quenching/bin/cq specs status --spec <id> --json` and uses its recorded PR number/URL.
2. A provider URL or numeric PR id is sent to the configured provider.
3. No argument reads the PR for the current branch; Azure lists active PRs by source branch.

If a spec has no PR record, or the branch has no PR, report that fact and stop. More than one
Azure PR for the current branch is ambiguous and is reported with the candidate ids.

## GitHub collection

After `gh repo view --json owner,name`, collect one PR snapshot with:

```bash
gh pr view <number-or-current-branch> --json number,title,url,state,isDraft,author,headRefName,baseRefName,statusCheckRollup,reviewDecision,reviewRequests,latestReviews,mergeable,mergeStateStatus
```

Collect review threads with the read-only GraphQL `repository.pullRequest.reviewThreads` connection,
requesting `id`, `isResolved`, `path`, `line`, `comments` and `pageInfo`. Follow `hasNextPage` to
the end; if a nested comments connection is truncated, report it. Count only `isResolved: false`.

`gh` exit failures retain the command's distinction between not found, authentication failure and
other provider errors. `statusCheckRollup` may be pending, failed, skipped or absent; preserve each
state and never reduce it to a boolean green value.

## Azure DevOps collection

Use `az repos pr show --id <id> --detect true --output json` for an explicit id and
`az repos pr list --source-branch <branch> --status active --detect true --output json` for the
current branch. Preserve the returned pull request id, URL, source/target refs, status, reviewers,
merge status and policy/status rows. Normalize the identity as `id`, `webUrl` and `apiUrl`: use
`repository.webUrl/pullrequest/pullRequestId` for the Link para revisão and keep the response's
`url` as the API URL. Collect threads through the read-only
`az devops invoke --area git --resource pullRequestThreads` route using the project, repository and
pull request ids returned by the PR object. Count non-terminal thread statuses and report any
pagination or provider truncation.

Azure fields that have no GitHub equivalent stay in the provider-specific detail; only the common
snapshot fields are normalized. A missing reviewer approval is not an approval, and an absent
merge status is `unknown`. The report keeps the normalized **Link para revisão** and **API URL**
labels distinct from provider-specific detail.

## Recommendation rules

Recommendations are text only:

- unresolved threads → `/quenching-git pr-review`;
- failed checks or conflicts → inspect/fix before merge;
- pending checks or unknown mergeability → wait or refresh provider state;
- known clean checks, approvals and mergeability → `/quenching-git merge` may be the next human step;
- missing PR, authentication failure or incomplete evidence → no action until the cause is resolved.

The command never pushes, edits, resolves threads, changes a spec record or merges a PR.
