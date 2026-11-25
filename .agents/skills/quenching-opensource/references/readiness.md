---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*)
---

# /quenching-opensource readiness — assess a repository before it goes public

**Input**: `$ARGUMENTS` (an optional target repository root, an optional `--evidence <path>` to
the owner's reviewed license, consent and provenance evidence, and an optional `--denylist <path>`
to the owner's sensitive terms, kept outside the target; omit the root to use the current
repository).

This command is the read-only pre-publication assessment of the open source front. It runs
**before** `/quenching-opensource align` when the owner wants to open a private
repository. It reports blockers, warnings and the coverage it could not measure; it never
publishes, never writes an artifact, never rewrites history and never changes visibility. The
evidence contract, the finding classes and the disposition of each are in
[opensource-readiness/report.md](.agents/skills/quenching-opensource/references/opensource-readiness/report.md).

Resolve `cq` per
[tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool. Set `TARGET_ROOT` to the supplied root, or to `.` when omitted, and run:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" opensource readiness --json [--evidence "<path>"] [--denylist "<path>"]
```

Branch on the exit code: `0` is a complete measured assessment with no finding, `1` carries
measured findings, and `2` means at least one category was not measured or was refused — any
finding already measured is still in the payload. Exit `0` describes the assessed scope only; it is
never an approval to publish.

## Workflow

### 1. Read the readiness payload

Run the invocation above once. Preserve `scope`, `scanners`, `coverage`, `blockers`, `warnings`
and `truncated` as returned. Never re-run a scanner yourself, never print a file the payload names
and never open a commit it names: the payload is deliberately free of values, and reading the
location back is how a secret reaches the transcript.

**Done when:** the payload and its exit code are captured.

### 2. Report blockers, warnings and coverage separately

Report three tables, in this order, per
[opensource-readiness/report.md](.agents/skills/quenching-opensource/references/opensource-readiness/report.md)
§The report:

| Table | Rows |
| --- | --- |
| Blockers | every `blockers[]` row — class, code, location, commit, owner remediation |
| Warnings | every `warnings[]` row — class, code, location, owner remediation |
| Coverage | one row per class — state, code, scanner; then the history scope (`refs`, `shallow`, `historyComplete`) and the `untracked`/`ignored` counts that were not scanned |

A class that is `not-measured`, `partial` or `refused` stays so in the report and is named as the
reason the assessment is incomplete; never round it to clean. A missing scanner is named with the
install the owner may choose, never installed here.

**Done when:** every class has a coverage row and every finding keeps its code and remediation.

### 3. Hand back control

State that this command wrote nothing and that remediation and publication are the owner's: the
`remediation` of each row is a route for the owner, never an action this command takes. After the
owner clears the blockers, name `/quenching-opensource align` for the community
artifacts and this command again to reassess.

**Done when:** the report states its read-only boundary, the observed exit code and that no
result is a publication approval.

## Invariants

- Read only: no target write, no fetch, no ref change, no scanner install, no remote credential
  verification.
- No secret value, snippet, scanner message, personal address, denylist term or absolute path is
  ever echoed.
- An unmeasured category is never reported as clean, and no exit is an approval to publish.
