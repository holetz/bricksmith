# Open source finding disposition bands

<!-- rules -->

This file owns only the `os-*` codes, their disposition bands and the artifact-specific closure
shapes. The common axis is defined in [`front-align/mold.md`](.agents/quenching/references/front-align/mold.md)
§Disposition bands; ownership of each control is the catalog's, in
[`opensource-controls/catalog.md`](../opensource-controls/catalog.md). Every write closes through
`.agents/quenching/bin/cq opensource apply`, which copies the pinned template byte for byte and substitutes only the
placeholders `sources.json` declares.

## Artifact language

Every template in `sources.json` declares `language: en`, and `status` and `doctor` expose it as
`artifactLanguage` (`basis: template-declaration`, `contentVerified: false`) whatever language the
target declares for itself. The contract covers community artifacts and owner-supplied placeholder
prose. No `os-*` finding detects a language: `apply` returns the informational
`os-var-language-unverified` warning for supplied placeholders and accepts the values unchanged.
README and SECURITY stay with their owners, reported as `artifactLanguage.handoffs`
(`knowledge` and `security`).

## Mechanical findings

There is no mechanical `os-*` finding in a target. Every artifact this front writes carries an
answer only its owner can give, or at least one plan authorization; none regenerates from a
deterministic source in the target.

## Structural findings

The align may close a structural code after the one plan authorization, once the owner has
declared the template's placeholders in that plan. It applies one artifact per code and re-runs
`.agents/quenching/bin/cq opensource status --json`.

| Code | Severity | Artifact | Closure |
| --- | --- | --- | --- |
| `os-contributing-missing` | `error` | `contributing` (`CONTRIBUTING.md`, with the DCO sign-off section), optionally `dco` | Apply with the declared `project` and `discussion`. |
| `os-conduct-missing` | `info` | `conduct` (Contributor Covenant 2.1) | Apply with the declared `contact`; the upstream text is never adapted. |
| `os-support-missing` | `info` | `support` (`SUPPORT.md`) | Apply with the declared `discussion`. |
| `os-templates-missing` | `info` | `issue-bug`, `issue-feature`, `pr-template` | Apply each absent template; they declare no placeholder. |

## Judgement findings

A judgement code is closed only by the owner's explicit choice. The align asks for it in the plan
and applies the artifact with the declared answer; with no answer it reports the evidence and the
closing action, and writes nothing.

| Code | Severity | Owner choice | Closure |
| --- | --- | --- | --- |
| `os-license-missing` | `error` | SPDX id, year, holders | Apply `license` with `spdx`; `MIT` takes `year` and `holders`, `Apache-2.0` none. |
| `os-governance-missing` | `error` | maintainers and decision process | Apply `governance` with the declared `maintainers`. |
| `os-codeowners-missing` | `info` | code owners | Apply `codeowners` with the declared `owners`. |
| `os-citation-missing` | `info` | project, authors, repository, SPDX id | Apply `citation` with those four values. |
| `os-funding-missing` | `info` | funding account | Apply `funding` with the declared `github_sponsors`. |
| `os-reuse-missing` | `info` | per-file licensing | Report only: REUSE needs a header or sidecar per file, which no template writes. |
| `os-template-drift` | `error` | the plugin maintainer | Stop before any write; the pinned template no longer hashes to its `sources.json` sha256. Reinstall or update the plugin; never edit the template. |
| `os-readme-media-missing` | `info` | a screenshot or GIF of the interface, made with synthetic data | Report only: the row carries `owner: knowledge` and its `command`; the owner stores the image under `.design/assets/` and the README cites it. |
| `os-language-skew` | `info` | whether the flagged `paths` are vendored or templates | Report only: show the row's `gitattributes` lines; the owner commits them, outside `apply`. |
| `os-<control>-missing` | `error` or `info` | the requirement's evidence | Any opensource-owned control with no named code (for example `OSPS-GV-02`, `OSPS-LE-01`, the `SCORECARD-*` supplements): report the evidence and the owner action. |

## Apply refusals

These are refusals of `.agents/quenching/bin/cq opensource apply` (exit 2), never findings: the align reports each as it
came and skips that artifact.

| Code | Meaning |
| --- | --- |
| `os-artifact-unknown` | No template carries that artifact name. |
| `os-license-unvendored` | No vendored text for the SPDX id; the license choice stays a judgement hand-off. |
| `os-var-missing` | A declared placeholder had no `--var`. |
| `os-var-unknown` | A `--var` names no declared placeholder. |
| `os-target-exists` | The target path exists; apply never overwrites. |

## The complete mapping

Each finding code has one and only one disposition:

| Band | Codes |
| --- | --- |
| Mechanical | none |
| Structural | `os-contributing-missing`, `os-conduct-missing`, `os-support-missing`, `os-templates-missing` |
| Judgement | `os-license-missing`, `os-governance-missing`, `os-codeowners-missing`, `os-citation-missing`, `os-funding-missing`, `os-reuse-missing`, `os-readme-media-missing`, `os-language-skew`, `os-template-drift`, `os-<control>-missing` |

Severity follows the catalog: a required control's code is `error` and changes the exit code; an
optional control's code, and an artifact no control names, is an `info` advisory that never does.

<!-- rationale -->

The split keeps the align useful without making it the owner of the project's identity. Templates
whose only inputs are a name, a venue or a contact close as structural repairs once those inputs are
declared; a license, a maintainer list or a funding account commits the owner legally or socially,
so it is written only from an explicit answer. Writing through the route keeps upstream texts such
as the Contributor Covenant and the SPDX license texts byte-identical, which a model copy would not
guarantee.
