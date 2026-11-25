# Open source controls catalog

## The data contract

The shared data lives at `.agents/quenching/assets/opensource/controls.json`. Its
`schema_version: 1` describes routing and evidence requirements, not assessment results.
Read it as JSON. Do not derive IDs or owners from prose in command bodies.

The adjacent `baseline-2026.08.28.json` freezes the OSPS identifiers, applicability and
active/retired state from upstream tag `v2026.08.28`. It carries the release date, inspection
date, eight authoritative family URLs and their original-byte SHA-256 digests. The digests
record what was inspected; the offline checker does not download or rehash upstream content.
The snapshot contains 41 controls and 65 assessment requirements: 64 active, one retired.
It does not reproduce the normative requirement text. Consult the pinned upstream sources
when interpretation matters.

| Catalog field | Meaning |
| --- | --- |
| `baseline` | Pinned version, release date and adjacent snapshot filename. |
| `owners` | The seven allowed front/pillar IDs. This is routing vocabulary, not installation state. |
| `sources` | Authoritative reference URL, version or inspection snapshot, and inspection date; supplemental sources also enumerate their referenced items. |
| `controls` | One record per OSPS control plus sourced optional supplements. |

Each control has a stable `id`, a short assessment title, `framework` (`osps` or
`supplemental`), one scalar `owner`, an `optional` Boolean, a
`measurable_without_provider` Boolean, `references` and `requirements`. OSPS controls also
carry their `family`. Supplemental IDs use named uppercase prefixes; they never claim OSPS
identity or maturity applicability.

Each requirement has its own stable `id`, `levels`, `state`, `provider_required`, and
`evidence`. OSPS applicability belongs to the requirement, not the control. Preserve the
listed levels exactly; do not extrapolate an omitted level from a lower one. In this release,
for example, BR-07.01 applies only at level 1, while BR-07.02 applies only at level 3.
Supplements have empty `levels` and remain optional independently of a target's maturity.

The evidence object names repository-relative candidate `paths`, named `provider`
assertions, a concise `assessment` prompt, and `human_required`. The current prompts require
interpretation: they are not executable probes. A provider route describes the fact to read,
not a new API or a command guaranteed to exist.

## Exactly one owner

| OSPS controls | Owner |
| --- | --- |
| LE, GV | `opensource` |
| VM; AC-04; BR-01, BR-07; QA-04 | `security` |
| BR-02 through BR-06 | `delivery` |
| QA-02, QA-05 | `toolchain` |
| QA-06 | `proof` |
| DO, SA | `knowledge` |
| AC-01 through AC-03; QA-01, QA-03, QA-07 | `git` |

This follows epic 1627 D5. Its omitted QA-01 and QA-04 rows are allocated by subject to
`git` (public development history) and `security` (cross-repository security requirements).
These are explicit catalog assumptions, not changes to either owner's behavior.

An aggregator asks the owner for an assessment and retains the owner's evidence and
limitations. It does not run a second implementation under `opensource`. A related external
item has exactly one catalog route as well: this prevents a Scorecard check or community
practice being assessed independently by two owners.

Catalog membership does not authorize new features in a front with a maintenance freeze.
Specs 1637 and 1639 consume this data. Spec 1638 recorded the owner's decision: `security`,
`delivery`, `toolchain`, `proof` and `knowledge` answer their controls in a read-only `controls`
block of their `status`, through the shared evaluator `quenching/controls/evaluate.py`, and
`docs/index.md` records the exception to the freeze. `git` does not answer yet: all its controls
need a provider. The provisional `opensource` owner
ID does not settle the future front's name, module layout, command API or adoption level.

## Evidence and provider absence

The artifacts the `opensource` owner writes are English by template declaration
(`artifactLanguage` in its `status`); the catalog assesses presence, never the language of content.

Candidate paths identify where evidence may live. They are not required filenames in every
target and file existence alone proves no requirement. For example, a SECURITY file may name
a contact without specifying disclosure timing; a workflow may declare permissions without
proving the forge's default permissions. Resolve the assessment against the requirement's
normative source and actual evidence, recording limitations.

`provider_required` is true exactly when the requirement's provider assertions are nonempty.
`measurable_without_provider` is true only when every active requirement is provider-free.
These flags describe whether complete assessment requires provider facts, not whether an
automated local probe already exists. Human interpretation, external release material or
supporting documents can still be necessary when `measurable_without_provider` is true.

Mixed controls retain this distinction at requirement level. AC-04.01 needs the forge's
pipeline-default permissions; AC-04.02 can be examined in workflow source. The whole AC-04
control therefore requires a provider, even though part of it has local evidence. QA-06 has
the same distinction between observed pre-acceptance CI execution and documented test policy.

With no provider tool or insufficient access, return `not-measured` for the affected
requirement and preserve any locally measured sibling requirements. Never convert absent
provider evidence into either compliance or a defect, and never claim an attained maturity
level from a complete inventory alone. This catalog defines no score or maturity aggregation
algorithm.

`OSPS-BR-01.02` is retained as `retired`. It has no assessment route and must be excluded from
active obligation and maturity calculations. A consumer must not reassign its former meaning
or silently delete its identifier.

## Related sources and optional conventions

Every `references` entry has `source`, `item` and `relation: related`. The source key resolves
through `sources`; the item resolves through that source's inventory. Related means a useful
comparison, never full equivalence, certification or a substitute for assessing the OSPS
requirement. Scorecard is pinned to the inspected documentation commit, with all 20 check
names routed once. GitHub's community documentation is an inspection snapshot; the catalog
does not promise an exact GitHub community-profile score or universal filename recognition.

REUSE 3.3 supplies the optional per-file licensing record, including covered-file metadata and
license texts. A top-level license alone does not establish REUSE conformance. Contributor
Covenant 3.0 is a related option for community conduct, not the only acceptable policy. DCO
1.1 is one mechanism related to contribution authorization; its mention does not mandate DCO
over other valid assertions. Keep a Changelog 1.1.0 and SemVer 2.0.0 are related release
conventions, not formats imposed by OSPS. CITATION.cff and GitHub FUNDING.yml are optional
attribution and sponsorship practices. Adopt them only when they serve the target.

The 12 supplemental records have no OSPS levels. They cover community conduct and review
templates, REUSE metadata, citation, funding, and Scorecard items not already routed through
an OSPS control. Their presence is not an additional baseline requirement.

## Offline verification and updates

From this repository's root, run:

```bash
python3 scripts/check_opensource_controls.py
```

Exit 0 means catalog integrity, not target compliance. Exit 1 reports malformed JSON,
including duplicate keys, or schema, ownership, coverage, source, applicability and evidence
findings. The repository gate invokes this checker; the existing unittest suite includes
mutation regressions. Neither path needs network access or provider credentials.

To update the baseline, inspect the authoritative tagged family sources, regenerate the
identifier snapshot and original-byte digests, review every routing and applicability change,
and deliberately migrate the schema/checker's pinned release inventory. Do not replace a
version with `latest`. Preserve retired identifiers and inspect changed supplemental sources
before advancing their recorded versions or dates. Run the checker, its mutation suite and
`bash scripts/verify_repo.sh` after the change. Updating catalog data grants no authority to
change an owner's verifier or a target's community artifacts.
