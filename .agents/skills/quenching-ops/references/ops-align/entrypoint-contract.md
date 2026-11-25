# Ops front entry-point contract

<!-- rules -->

This file is self-contained: an ops align reads it to classify entry points and findings without
loading another front reference.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## The seven rules

Every active entry point answers to these rules. `checked` is a property the verifier can decide
statically; `prose, with static proxy` is reported by the align because the full claim requires
running arbitrary target code.

| # | Rule | Kind |
| --- | --- | --- |
| 1 | Every entry point is reachable from the canonical router and named by the generated registry. | checked |
| 2 | Repository-root resolution, `sys.path`, and shared flags come from one bootstrap module. | checked |
| 3 | Exit codes are typed: `0` for success, `1` for findings, and `2` for misuse. | checked |
| 4 | Data goes to `stdout`, diagnostics go to `stderr`, and `--json` puts only data on `stdout`. | checked |
| 5 | An entry point that writes outside the repository is preview-first, armed by an explicit flag, and never armed by editing its source. | checked |
| 6 | `--help` carries a usage example. | prose, with static proxy |
| 7 | Every entry point declares one lifecycle: `active` or `archived`. | checked |

For rule 6, the verifier checks only the static proxy: a non-empty `description` or `epilog` on
the parser. The align reports that proxy and names the rule; it never claims to have proven the
runtime help output.

## The minting mold

`assets/templates/ops/entrypoint.py.tmpl` supplies the deterministic part of the contract. The
mint replaces its placeholders but keeps this mapping intact:

| Mold element | Contract rule |
| --- | --- |
| import from the target's shared bootstrap | 2 — root, `sys.path` and common flags have one owner |
| `main() -> int` and `raise SystemExit(main())` | 3 — typed success, findings and misuse exits |
| parser, `emit`, stdout/stderr split and `--json` | 4 — data stays machine-readable and diagnostics stay separate |
| parser description and usage example | 6 — the help surface explains one invocation |
| `LIFECYCLE` declaration | 7 — the entry point has one lifecycle state |

The mint asks the author whether the entry point writes outside the repository. A `no` answer
removes the write guard. A `yes` answer inserts preview-first behaviour and an explicit `--apply`
flag; there is no unarmed write-capable shape. Rule 5 remains a source-level claim for the doctor
and a runtime proof for the mint's closing check.

The mint offers `archived` from birth. When selected, it places the file below
`<opsRoot>/_archive/<domain>/`, keeps its registry row as `archived`, and does not add it to the
active router. This makes the lifecycle decision explicit at creation time without pretending that
one-shot work is an active operation.

## The disabled-gate rule

A verification call commented out inside an entry point's control flow is a finding, not a
configuration. A repository that wants a check off declares that state explicitly; it never hides
the call behind `#`. The verifier detects the disabled call with Python's `tokenize` module beside
its AST pass: a `COMMENT` token whose text, after the leading `#` is removed, parses as a call
expression and sits by indentation inside AST-identified entry-point control flow.

## Finding codes

The verifier and align use these stable codes:

| Code | Meaning | Band |
| --- | --- | --- |
| `op-undocumented` | An active entry point exists but is not named by the generated registry. | Mechanical |
| `op-registry-stale` | The generated registry does not match the entry points reachable from the router. | Mechanical |
| `op-adhoc-root` | An entry point resolves the repository root outside the shared bootstrap module. | Structural |
| `op-untyped-exit` | An entry point returns an untyped or otherwise non-contractual exit status. | Structural |
| `op-unarmed-write` | An operation writes outside the repository without preview-first behaviour and an explicit arming flag. | Judgement |
| `op-disabled-check` | A verification call is commented out inside an entry point's control flow. | Judgement |
| `op-orphan` | An entry point is present in the operations tree but is not reachable from the canonical router. | Judgement |
| `op-no-router` | The repository has no single declared router for its normalized operations surface. | Structural |
| `op-registry-malformed` | The registry file is unreadable as a registry: its generated zone is duplicated or the file is not valid UTF-8. | Judgement |

<!-- rationale -->

The `Kind` column prevents a static verifier from making a claim it cannot prove. Rules 1–5 and 7
describe structure or source-level behaviour; rule 6 would require executing target code merely to
read its help, so the static parser proxy is the honest boundary.

The disabled-gate rule is separate because a comment is invisible to the AST and therefore to the
ordinary call inventory. Pairing `tokenize` with the AST keeps the finding narrow enough to review:
it looks for a call-shaped comment in entry-point control flow, not every code-looking example in a
repository.
