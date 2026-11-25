# Pre-publication readiness: evidence, report and disposition

<!-- rules -->

This file owns the `pr-*` codes of `.agents/quenching/bin/cq opensource readiness`, the evidence each class accepts and
the shape of the report `/quenching-opensource readiness` builds from it. Readiness is a
separate assessment from the baseline: it never converts into an OSPS level, and the catalog in
[`opensource-controls/catalog.md`](../opensource-controls/catalog.md) never reads it.

## The assessed scope

The route assesses the target's committed content: the `HEAD` tree and the reachable history of
every local branch and tag. `scope` returns the exact refs and commits, whether the clone is
shallow and whether every reachable object is present (`historyComplete`). Untracked and ignored
files are counted and never scanned (`untrackedScanned: false`); they are not part of what would be
published, and they are not represented as history either.

The route never fetches, never changes a ref, never writes to the target and never runs a target
script: every git call, its own and the scanners', runs with `core.fsmonitor=false` and an empty
hooks path. Scanner reports go to a disposable directory outside the target, deleted when the
assessment ends; a scratch directory that resolves inside the target refuses the scanner
(`pr-scratch-inside-target`).

## Classes and their evidence

| Class | Evidence | Measured when | Finding | Severity |
| --- | --- | --- | --- | --- |
| `secrets` | `gitleaks` ≥ 8.19 (`git` subcommand, `--redact`, branches and tags), else `trufflehog` 3.x (`--no-verification --no-update`) | a supported scanner completed over complete history | `pr-secret` | blocker |
| `internal-identifiers` | `git grep` over `HEAD` for internal host suffixes, private IPv4 ranges and a tenant inside a public SaaS host (Azure DevOps, `visualstudio.com`, SharePoint, Databricks, Snowflake, Atlassian); author and committer addresses of the reachable history | both reads completed over complete history | `pr-internal-identifier`, `pr-identity-in-metadata` | warning |
| `owner-terms` | the owner's `--denylist` file: whole-word, case-insensitive `git grep` over `HEAD`, and `git log` over the diffs and messages of every branch and tag | the file was usable and both reads completed over complete history | `pr-owner-term` | blocker |
| `dependency-license` | the owner's reviewed evidence file | the evidence names a state and a reviewer | `pr-license-incompatible` | blocker |
| `attribution` | `reuse lint --json` ≥ 2, tracked files only | reuse completed | `pr-attribution-missing`, `pr-license-text-missing` | blocker |
| `large-generated` | the `HEAD` tree: blobs over 5 MiB, build and dependency directories, compiled suffixes | the tree was readable | `pr-large-file`, `pr-generated-artifact` | warning |
| `history-consent` | the owner's reviewed evidence file | the evidence names a state and a reviewer | `pr-consent-withheld` | blocker |
| `provenance` | the owner's reviewed evidence file | the evidence names a state and a reviewer | `pr-provenance-derived` | blocker |

No regular expression stands in for a secret scanner: without a supported one, `secrets` is
`not-measured` (`pr-no-safe-scanner`), never clean. A scanner is refused, not run, when it is
absent (`pr-scanner-absent`), its version cannot be read (`pr-scanner-version-unknown`) or lies
outside the supported range (`pr-scanner-unsupported`). A run that times out, exits with an error or
returns a payload outside the allowlisted schema is `pr-scanner-timeout`, `pr-scanner-failed` or
`pr-scanner-malformed`, and the class stays unmeasured. `reuse` establishes attribution and license
files only; it never establishes dependency compatibility or author consent.

A tenant segment that names no organization (the vendor's own `marketplace`, `login` or `www`, and the
documentation placeholders `org`, `example`, `contoso` and their kin) is not a finding; a `{org}` or
`<org>` template never matches.

### The owner's denylist

Employer, client, team and project names no generic pattern finds. `--denylist <path>` takes a
UTF-8 file of terms, one per line; blank lines and lines starting with `#` are ignored. The file
must live **outside** the target, so the list never enters the repository: a path inside it
refuses the class (`pr-denylist-inside-target`). No `--denylist` is `pr-denylist-absent`, an
unreadable file `pr-denylist-unreadable` and a file with no term `pr-denylist-empty`; each leaves
`owner-terms` unmeasured. A `pr-owner-term` row carries the `location` of a `HEAD` file or the
`commit` whose diff or message holds a term, never the term.

### The evidence file

Dependency-license compatibility, historical author consent and the repository's provenance are
legal facts the owner establishes. `--evidence <path>` takes a JSON file:

```json
{
  "dependencyLicenses": {"state": "compatible", "reviewedBy": "<who reviewed it>"},
  "historyConsent": {"state": "granted", "reviewedBy": "<who reviewed it>"},
  "provenance": {"state": "clean-room", "reviewedBy": "<who reviewed it>"}
}
```

`dependencyLicenses.state` is `compatible` or `incompatible`; `historyConsent.state` is `granted`
or `withheld`; `provenance.state` is `original`, `clean-room` (only knowledge, no copied code, from
a project the owner does not control) or `derived`. `derived` is a blocker until the entry also
carries a non-empty `authorization` naming the license or permission, which is checked and never
echoed. An absent entry is `pr-evidence-absent`, another state is `pr-evidence-ambiguous`,
an empty reviewer is `pr-evidence-unreviewed` and an unreadable file is `pr-evidence-unreadable` —
each leaves its class `not-measured`. The reviewer's name is checked, never echoed.

## The result

| Exit | Meaning |
| --- | --- |
| `0` | every class measured over the declared scope, no finding |
| `1` | every class measured, at least one blocker or warning |
| `2` | at least one class `not-measured`, `partial` or `refused`; measured findings are still returned |

`grantsPublication` is always `false`. A clean exit describes the assessed scope; it is never an
approval to change visibility. A repository with no commit, a path outside git or a path that is
not the repository root refuses every class (`pr-no-commits`, `pr-not-a-repository`,
`pr-not-repository-root`).

## The report

Every finding carries `code`, `class`, `severity`, `remediation`, `owner` and, where it applies, a
`location` and a validated `commit`. Nothing else crosses from a scanner: no snippet, match,
rule text, entropy value, scanner message, personal address or absolute path. A location is a
relative path only when every segment is plain and no run of twenty alphanumerics could be a
value; otherwise it is `opaque:<sha256 prefix>`. At most fifty findings per code are returned;
`truncated` counts the rest.

The command reports blockers, warnings and coverage as three separate tables. An unmeasured class
is named as the reason the assessment is incomplete.

## Disposition

Every `pr-*` code is owner-only. The route and the command never remediate, never rewrite
history, never rotate a credential and never publish. Each row's `remediation` names the owner's
route; the command hands back control and names itself again to reassess once the owner acts.

| Code | Owner remediation |
| --- | --- |
| `pr-secret` | rotate the credential; the owner decides whether to rewrite history |
| `pr-internal-identifier` | review the reference and remove or generalize it |
| `pr-owner-term` | remove the term from the tree; decide whether to rewrite history |
| `pr-identity-in-metadata` | confirm every author agrees to public commit metadata |
| `pr-large-file`, `pr-generated-artifact` | decide whether the artifact belongs in the published history |
| `pr-attribution-missing`, `pr-license-text-missing` | add the copyright, license information or license texts reuse names |
| `pr-license-incompatible` | resolve the incompatible dependency license |
| `pr-consent-withheld` | obtain consent or remove the contribution |
| `pr-provenance-derived` | obtain a license or authorization from the origin project, or remove the derived work |

<!-- rationale -->

Readiness is distinct from align because its failures are not repairable by writing a file: a
secret in history stays public once pushed, and consent is a fact about people. Reporting an
unmeasured category as a gap, rather than as clean, is what keeps a missing scanner from reading as
a green light.
