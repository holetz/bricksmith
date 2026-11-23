# `decisions/` — why a contract is the way it is

The **history and measurements** behind a contract: the dated measurement, the first cut that
failed, the chain of specs that moved a rule. A [standard](/docs/standards/index.md) keeps only the
rule and a link here, so an agent reading the standard (`.agents/quenching/bin/cq components read --rules-only`) pays for
the rule, not for how it was found. One decision per file, `type: decision`, numbered
`NNNN-<slug>.md` in the order written; a decision is **append-only**: a later one supersedes it
by saying so, never by rewriting it.

**Boundary:** `decisions/` = "why it became this rule". Distinct from
[standards/](/docs/standards/index.md) (the rule itself) and [vision/](/docs/vision/index.md) (direction).
An agreed-but-unproven rule is still a standard with `authority: background`; this home holds the
evidence, not the rule.

## Decisions

_None yet. A spec that changes a contract writes its history here as `NNNN-<slug>.md` and links it from the standard._
