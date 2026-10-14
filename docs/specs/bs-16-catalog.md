# Generate deterministic offline data and model catalog

- Date: 2026-10-14
- Status: paused at human request; no implementation started
- CQ: [3](https://github.com/holetz/bricksmith/issues/3)
- Owner: [Epic 0001](../epics/0001-bricksmith-library.md)

## Problem

Epic 0001 BS-07.4 and BS-16.2 lack catalog documentation. Registry write integration is proven only on the write side (docs/evidence/bs-04-free.md:25). Existing metadata and graph APIs make lineage and sensitivity visible without data scans (registry/metadata.py:68,88; graph/model.py:28-40,89-91,112-119).

## Proposal

Add a lightweight catalogdocs capability joining the task graph and optional column registry; expose deterministic JSON/Markdown through public API and CLI.

## Out of Scope

Live enrichment, remote access, task imports, arbitrary metadata dumps, data scans and plugin execution. BS-16.2 remains partial if full live-enrichment acceptance is not proved.

## Impact

src/bricksmith/catalogdocs/{__init__,model,render,cli}.py; src/bricksmith/cli/groups.py; tests/unit/catalogdocs/test_catalog.py; docs/guide/catalog.md; docs/epics/0001-bricksmith-library.md.

## Validation

Independent expected lineage/coverage, shuffled-input stability, registry overrides, missing metadata, Markdown escaping and stdout/file/error CLI behavior. Static task containing an exception/heavy import must never execute. Run the repository gate.

## Design

Stop override: the dated delegation below is historical and cannot resume this paused run.

build_catalog(graph: TaskGraph, registry: Registry | None = None) -> CatalogReport; to_dict/to_json/to_markdown. CLI: bricksmith catalog render --project PATH [--registry PATH] [--format markdown|json] [--output PATH]. Explicit bad registry fails; absent optional registry reports incomplete metadata. Sort every collection; no timestamps. Consume Registry.resolve/table_metadata/sensitivity_coverage and graph outputs/edges/diagnostics. Model identity is explicit metadata.model_task.model (examples/shared_schema/catalogs/demo/ml/churn_model.py:16-26), never an inferred output identity. Preserve logical/physical scopes, declared grain and classified/unclassified/unknown coverage. Escape Markdown cells.

Autonomy delegated by the human invocation on 2026-10-14: ordinary product implementation, verification, commit, push and PR are authorized after verifier PASS; merge additionally requires green CI. Executable grants/surfaces, generated CI/hooks/scripts and plugin runtime expansion are never forced and belong to the human-decision epic. No publishing or visibility changes.

## Alternatives Considered

Reuse graph and registry contracts rather than importing Spark or building a second metadata engine.

## Open Decisions

- none — ordinary product design is delegated; any executable-surface decision goes to the human epic.

## Risks

Partial metadata must remain visible; output paths and Markdown need careful handling. No secret-bearing arbitrary metadata serialization.

## Handoff

Store discrepancy: CQ 6.4.0 resolves the legacy GitHub default because .agents/quenching.json is absent; the human expects the quenching branch store. Existing CQ issues #1-#4 are retained, and local specs are durable. No migration, configuration change, issue creation or protocol/store-design choice is authorized by this paused handoff. Resolve the store decision with the human before any resumed tracker writes; do not modify the quenching branch automatically.

User requested a stop on 2026-10-14. This run is paused; do not start development, remote diagnosis, push, PR or merge until an explicit resume. Branch: epic-0001; committed recovery d72b46e; persisted-artifact hardening 01f828e. No merge into main or publication occurred. All tasks below remain unimplemented.

Execute after existing artifact registration issue #2 completes its worker. Then static template, then release readiness; these share Epic 0001/docs and CLI examples and must run serially. BS-07.4 may be ticked only when catalog and prior write evidence cover its acceptance.

Local contract: docs/specs/bs-16-catalog.md

## Tasks

### 1. Implementation and acceptance

- [ ] 1.1 Implement pure catalog report and CLI
  files: src/bricksmith/catalogdocs/ src/bricksmith/cli/groups.py
  verify: uv run pytest tests/unit/catalogdocs -q
- [ ] 1.2 Prove determinism, incomplete metadata and offline behavior
  files: tests/unit/catalogdocs/test_catalog.py
  verify: uv run pytest tests/unit/catalogdocs -q
- [ ] 1.3 Document public usage and record only achieved epic acceptance
  files: docs/guide/catalog.md docs/epics/0001-bricksmith-library.md
  verify: uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest -q
