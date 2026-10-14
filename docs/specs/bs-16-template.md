# Prepare static Copier starter without executable automation

- Date: 2026-10-14
- Status: paused at human request; no implementation started
- CQ: [4](https://github.com/holetz/bricksmith/issues/4)
- Owner: [Epic 0001](../epics/0001-bricksmith-library.md)

## Problem

Epic 0001 BS-16.1/16.5 requires a fresh updatable project, but template/ is absent. docs/planning/delivery-contracts.md specifies neutral questions, one library dependency, agent-free use and preservation of local configuration.

## Proposal

Prepare a static Copier starter with neutral configuration, synthetic Python tasks, registry seed and setup documentation; retain incomplete acceptance where render/update/deployment proof is unavailable.

## Out of Scope

Generated CI, Copier tasks/migrations/extensions, hooks/scripts/tools grants, executable plugin skills, deployments and marketplace publication. Do not claim full BS-16.1/16.5 completion from static checks.

## Impact

template/copier.yml; template/project/; tests/unit/template/test_template.py; docs/guide/template.md; docs/epics/0001-bricksmith-library.md.

## Validation

Check nonsecret questions, matching registry/task logical names, prohibited automation absence, neutral defaults and .env exclusion. With actual supported Copier available, render twice, validate/plan offline and update an edited project while proving task edits and local .env survive. Otherwise record actual Copier proof as not run. Run repository gate.

## Design

Stop override: the dated delegation below is historical and cannot resume this paused run.

Questions: validated project_name, logical_catalog, logical_schema, scope_layout shared/dedicated, agent_integration none/claude. Use _subdirectory: project and an answers template; save no hosts/profiles/credentials. Render pyproject.toml with exactly one runtime dependency bricksmith==0.1.0, bricksmith.toml, .gitignore, .env.example, README, a source/downstream task and neutral registry. Never render .env. Exclude .env/.git/caches; no _tasks or migrations. The claude option renders only thin public-contract/plugin pointers; plugin remains planned, with no copied workflow bodies. Use existing task decorator and mappings, no generated bundle YAML. Copier configuration reference: https://copier.readthedocs.io/en/latest/configuring/.

Autonomy delegated by the human invocation on 2026-10-14: ordinary product implementation, verification, commit, push and PR are authorized after verifier PASS; merge additionally requires green CI. Executable grants/surfaces, generated CI/hooks/scripts and plugin runtime expansion are never forced and belong to the human-decision epic. No publishing or visibility changes.

## Alternatives Considered

Static configuration plus public library tasks avoids duplicating framework implementations or granting template automation.

## Open Decisions

- none — ordinary product design is delegated; any executable-surface decision goes to the human epic.

## Risks

Copier is not currently on PATH; static file checks are not render/update proof. Pinning an unpublished private wheel needs a documented local artifact route; no public index availability claim.

## Handoff

Store discrepancy: CQ 6.4.0 resolves the legacy GitHub default because .agents/quenching.json is absent; the human expects the quenching branch store. Existing CQ issues #1-#4 are retained, and local specs are durable. No migration, configuration change, issue creation or protocol/store-design choice is authorized by this paused handoff. Resolve the store decision with the human before any resumed tracker writes; do not modify the quenching branch automatically.

User requested a stop on 2026-10-14. This run is paused; do not start development, remote diagnosis, push, PR or merge until an explicit resume. Branch: epic-0001; committed recovery d72b46e; persisted-artifact hardening 01f828e. No merge into main or publication occurred. All tasks below remain unimplemented.

After docs/specs/bs-16-catalog.md; before docs/specs/bs-17-release-readiness.md. Do not concurrently edit the epic. Any CI/hook/plugin execution requirement moves to the human-decision epic.

Local contract: docs/specs/bs-16-template.md

## Tasks

### 1. Implementation and acceptance

- [ ] 1.1 Create neutral static template and synthetic task/registry configuration
  files: template/
  verify: uv run pytest tests/unit/template -q
- [ ] 1.2 Verify static safety and record real render/update proof only when available
  files: tests/unit/template/test_template.py
  verify: uv run pytest tests/unit/template -q
- [ ] 1.3 Document setup, private wheel route and remaining acceptance
  files: docs/guide/template.md docs/epics/0001-bricksmith-library.md
  verify: uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q
