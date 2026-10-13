# Capability review for the bricksmith development epic

- Date: 2026-10-13
- Method: read-only inspection of a private reference project's documentation, Python definition
  inventory and selected test contracts. No reference application, tests, job or deployment ran.
- Result: [development epic](../epics/0001-bricksmith-library.md).

## Evidence and limits

The review covered framework discovery and execution, table and schema contracts, job assembly,
connectors, registry and naming, comparison and monitoring, model datasets and artifacts,
development isolation, generated documentation and agent workflows. Business pipelines were not
used as a specification of generic functionality.

**Observed** means the reference contains documentation, definitions or tests for a capability;
it does not establish that the capability currently works in a live workspace. **Partial** means
the reference itself documents a gap or limitation. **Proposed** means an independent improvement
recommended for bricksmith. Test inventories are evidence of intended behavior, not test results.

Private paths and the evidence map remain in ignored local research notes, referenced only from
`CLAUDE.local.md`. This document contains independently written, generic requirements and no
reference code, test fixtures, naming vocabulary, organization identifiers or data. See
[decision 0010](../decisions/0010-behavior-derived-independent-development.md).

At review time bricksmith has one installable library, package skeletons, local package and leak
tests, and CI configuration. Discovery, storage, execution, MLOps and monitoring are planned,
not implemented. The previous roadmap named most of those capabilities but left the following
contracts underspecified.

## Findings and development coverage

| Finding | Evidence classification | Independent bricksmith requirement | Epic slices |
|---|---|---|---|
| Offline planning and lazy connections | Observed: offline-build and context tests | Discover and plan without credentials, Spark session creation or remote calls; explicit execution context | BS-01, BS-02, BS-06 |
| Multiple task kinds and parameter expansion | Observed: data, script, notebook, condition, job and model definitions | One task protocol; validated, typed parameters; distinct date, dataset and segment selectors | BS-01, BS-02, BS-11 |
| Dependencies inside helpers and model pipelines | Partial: documentation identifies reads absent from the manifest | Report inference coverage; allow explicit dependencies; discover task kinds consistently; unresolved managed reads fail validation | BS-02, BS-03, BS-11 |
| Task names differing from output names | Partial: documented producer-resolution ambiguity | Stable task identity qualified by logical scope; explicit output ownership; physical names are not execution identities | BS-02, BS-10 |
| Selective upstream/downstream execution | Observed: graph, selector and runner tests | Deterministic closure, filters, dry-run, failure propagation, bounded concurrency and replay | BS-03 |
| Dependencies spanning jobs | Observed: job assembly; documentation describes external ordering and exemptions | Account for every edge; specify how a boundary is satisfied; never silently drop dependencies during job filtering | BS-03, BS-06 |
| Safe replacement and training-artifact protection | Observed: overwrite and cascade contracts | Preview target and operation; protected targets require explicit authorization scoped to exact affected objects; never spread authorization implicitly | BS-03, BS-04, BS-12 |
| Different table write strategies | Observed: full, partition, incremental and SCD2 definitions/tests | Explicit idempotence and retry contracts, bounded replacement, merge history invariants and empty-input semantics | BS-04 |
| Type drift and physical schema changes | Observed: widening, schema drift, constraints and column mapping tests | Separate schema planning from applying changes; report incompatible changes and precision risks; configurable migration policy | BS-05, BS-07 |
| Expensive maintenance on ordinary writes | Observed: documented automatic maintenance sequence | Separate data correctness from scheduled optimization and retention; no unconditional expensive optimization on every write | BS-05, BS-14 |
| Metadata, grain and privacy classification | Observed: registry, descriptions, keys and sensitivity tests | Configurable semantic registry, declared grain, validated constraints and classification coverage; neutral defaults | BS-07, BS-15 |
| Classification versus enforcement | Partial: tags, masks and filters are explicitly not applied by the reference | Distinguish declared, applied and verified governance; optional policy reconciliation; never label metadata as effective protection | BS-15 |
| Comparing datasets before promotion | Observed: schema-plan, row comparison and Spark contracts | Key validity, missing/extra rows, null-safe comparison, explicit tolerances and bounded discrepancy reports | BS-08, BS-10 |
| External databases and file volumes | Observed: connector and volume surfaces | Injectable adapters, secret references, bounded transfer, visible external inputs/outputs; volumes not tied to model-specific scopes | BS-09 |
| Frozen inputs and experimental revisions | Observed: snapshot and development-overlay tests | Pin input versions and code provenance; isolate outputs; compare and promote through explicit plans without copying reference classes | BS-10, BS-12 |
| Samples, segments, exclusions and labels | Observed: model dataset definitions | Reproducible cohort selection, explicit exclusion reasons, mature labels, train/evaluation separation and feature lineage | BS-11 |
| Model lookup and artifact portability | Partial: documented silent retraining and class-path failures | Explicit train/predict modes; fail prediction on missing or incompatible artifacts; versioned model identity and load contract | BS-12 |
| Statistical and operational monitoring | Observed: binning and model-monitoring test contracts | Fit immutable reference bins, compare distributions, evaluate discrimination/calibration, handle insufficient history and persist alerts as ordinary tasks | BS-13, BS-14 |
| Documentation and lineage from metadata | Observed: extraction, rendering, lineage and introspection tests | Stable catalog generation; optional remote enrichment; report missing metadata; do not scan data just to render docs | BS-16 |
| Analysis evidence and lifecycle workflows | Observed: research-dossier, provenance and model-review workflows | Neutral experiment record, validation evidence, model card, change and retirement workflows; skills operate public CLI contracts | BS-10, BS-12, BS-16 |

## Independent additions

The following are proposed requirements, not claims of feature parity:

- A public CLI with machine-readable plans and reports, stable exit codes and a common execution
  contract for local development and deployed jobs.
- Output ownership and identity independent of both task class names and physical catalog names.
- Evidence for inference coverage and incomplete plans; explicit edges for dynamic reads.
- Operation-specific authorization tied to a reviewed plan rather than a global force flag.
- Feature and label time boundaries, dataset version fingerprints and prediction that never trains.
- Metadata-only discovery with deliberate connection injection and a documented supported-runtime
  matrix; platform limits are checked during implementation against current primary documentation.
- Retry and partial-failure tests, metadata-versus-enforcement reporting, and cleanup as release
  criteria. Their design is independent of the reference's exception lists and execution structure.

## Adaptation choices

The reference's catalog topology, per-model schema layout, class hierarchy, special directory
names, target remapping, parameter aliases and fixed naming policies are not bricksmith contracts.
The library will provide common behavior with explicit project configuration. Data, model and
monitoring tasks can coexist in one scope, or use dedicated scopes by choice.

Stateful processing also needs explicit dependencies on earlier data versions or time windows.
Such reads must not appear as an ordinary cycle to be ignored, nor silently read an empty target
during a backfill. The temporal contract belongs in the storage and dataset specifications.

Platform automation and analysis helpers will be designed for supported operating systems and
clean environments. Legacy migration shims, notebook conventions and machine-specific bootstrap
scripts from the reference are not copied.

## Excluded or deferred complements

Corporate vocabularies, financial formulas, organization-specific permissions, private model
artifacts, source data, business applications, brand assets and communication templates are
excluded from the library and examples.

Additional database drivers, presentation exporters, chat-provider adapters, conversational data
spaces and services consuming warehouse results are extension candidates after the first release.
Each needs an independent use case, support contract and specification. Streaming ingestion,
online feature serving and a general workflow scheduler were not established by this review and
are not implicit requirements of the first release.

## Next planning step

Use the epic as the parent scope. Before implementing a slice, develop its specification using
the generic requirements, public platform documentation and newly designed synthetic examples.
Record API decisions and acceptance tests there; do not reopen the private source to translate
methods or copy tests. The analysis establishes a plan, not a claim of runtime parity or a formal
separation between the observer and implementer.

The private path and local evidence map are historical review notes, not required implementation
inputs. The [delivery contracts](delivery-contracts.md) further specify library/template/plugin
artifacts, and the [test guide](../development/databricks-tests.md) specifies independent free and
corporate configuration. Missing detail is resolved in the child specification, not by requiring
access to the private source.
