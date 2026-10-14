# Record release readiness and workspace capability evidence

- Date: 2026-10-14
- Status: paused at human request; no implementation started
- CQ: not created; local proposed/stopped candidate only
- Owner: [Epic 0001](../epics/0001-bricksmith-library.md)

## Problem

BS-00.6/BS-17 need truthful artifact and workspace coverage. docs/evidence/bs-00-free.md:42-44 leaves corporate/unattended CI unproven; docs/roadmap.md:24-27 still describes a 13-test skeleton. Missing template/plugin and failed BS-12 registration cannot be called release proof.

## Proposal

Consolidate dated stage/capability/artifact evidence, update the stale readiness prose and run existing clean wheel/sdist verification without changing executable surfaces.

## Out of Scope

Publication, repository visibility, new CI/scripts, unproved platform matrices, remote retries/fallbacks and claims of plugin or template acceptance without actual exercise.

## Impact

docs/evidence/release-readiness.md; docs/roadmap.md; docs/epics/0001-bricksmith-library.md; existing scripts/verify_dist.py executed unchanged.

## Validation

Run existing scripts/verify_dist.py and repository gate; record exact outcomes/platform versions and selected-environment evidence. Review every acceptance claim against its command/date/source. Skips, missing artifacts and missing auth remain not run; unsupported requires evidence.

## Design

Stop override: the dated delegation below is historical and cannot resume this paused run.

Matrix rows distinguish configured/authenticated/compute-tested/deployment-tested and each capability per free/corporate workspace. Artifact rows distinguish library wheel/sdist, Copier render/update and Claude manifest/load/behavior. scripts/verify_dist.py:45-70 already installs both archives in fresh environments and verifies imports absent heavy extras. Keep failed BS-12 and not-run downstream lifecycle explicit. Record full-history private scan only if actually executed; no release approval implied.

Autonomy delegated by the human invocation on 2026-10-14: ordinary product implementation, verification, commit, push and PR are authorized after verifier PASS; merge additionally requires green CI. Executable grants/surfaces, generated CI/hooks/scripts and plugin runtime expansion are never forced and belong to the human-decision epic. No publishing or visibility changes.

## Alternatives Considered

Reuse existing evidence and verification instead of inventing a second release harness.

## Open Decisions

- none — ordinary product design is delegated; any executable-surface decision goes to the human epic.

## Risks

Historic passing runs are not current reruns. Release readiness is partial until human-gated surfaces and all required scenarios are proved.

## Handoff

Store discrepancy: CQ 6.4.0 resolves the legacy GitHub default because .agents/quenching.json is absent; the human expects the quenching branch store. Existing CQ issues #1-#4 are retained, and local specs are durable. No migration, configuration change, issue creation or protocol/store-design choice is authorized by this paused handoff. Resolve the store decision with the human before any resumed tracker writes; do not modify the quenching branch automatically.

User requested a stop on 2026-10-14. This run is paused; do not start development, remote diagnosis, push, PR or merge until an explicit resume. Branch: epic-0001; committed recovery d72b46e; persisted-artifact hardening 01f828e. No merge into main or publication occurred. All tasks below remain unimplemented.

After catalog and static template, serialized over docs/epics/0001-bricksmith-library.md. Existing issue #2 is the artifact blocker, not a duplicate finding.

Local contract: docs/specs/bs-17-release-readiness.md

## Tasks

### 1. Implementation and acceptance

- [ ] 1.1 Run unchanged clean installation verification and record results
  files: docs/evidence/release-readiness.md
  verify: python3 scripts/verify_dist.py
- [ ] 1.2 Consolidate truthful coverage and update stale roadmap acceptance
  files: docs/evidence/release-readiness.md docs/roadmap.md docs/epics/0001-bricksmith-library.md
  verify: uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q
