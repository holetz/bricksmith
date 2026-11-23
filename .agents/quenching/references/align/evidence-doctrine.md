# Gathering evidence economically — the aggregate, never the dump

Front-neutral: any command body that gathers evidence from `grep`/`gh`/`cq` to answer a small
question, or reads a doctrine or standard to obey part of it, pays for what it reads on every turn
that follows. This file states the two rules that keep that payment proportional to the question
being answered and the rule that governs a source gathered from the web, cited by
`.agents/quenching` from wherever a command needs them.

Measured on one real `/quenching-specs develop` session (`renomear-docs-para-knowledge`, backend
`github`): raw evidence was 36.6% of the ~58k tokens ingested — one `grep` alone returned 14,252
chars to produce an eight-line table — and whole-reference reads were 20.2%. Neither number is the
rule; both are why it was written down.

## Contents

`.agents/quenching/bin/cq components read <this file>` returns the heading index; `--sections` addresses one.

## 1. Extract the aggregate; never paste the raw dump

<!-- rules -->

Where a step needs a small aggregate — a table of a few rows, a count, a list of names — from
`grep`, `gh` or `cq`, that aggregate is produced **in the shell**, before it reaches the
conversation: compact flags (`-c`, `-l`, `-o`), a pipe (`sort | uniq -c`, `awk`, `cut`), or a
one-line `python3 -c` that already returns the final shape. The raw match text is never pasted into
the conversation to be summarized there — the summarizing happens in the command that produces the
output, not the one that reads it.

Where the sweep is wide and the question is still "one small table from many files," delegate
instead of narrowing badly: a read-only sub-agent (`Read, Grep, Glob`, nothing else) that returns
the table and no trail. This is not a new mechanism —
[specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md)
§Gathering the evidence and
[align/sweep-doctrine.md](.agents/quenching/references/align/sweep-doctrine.md) §4 The
blast-radius sweep both already delegate exactly this way; this rule is what generalizes their
shared shape to every other site that gathers evidence the same way.

<!-- rationale -->

A raw dump costs the same whether the reader keeps three lines of it or three hundred — the
extraction has to happen somewhere, and doing it in the shell means the conversation never carries
the part that gets thrown away. A grep that returns 14,252 chars for an eight-line table paid for
the other 14,000-odd chars for nothing; `grep -c` or `grep -l` would have answered the same
question for a few dozen.

## 2. Section-address any markdown — a target's `/docs/` included

<!-- rules -->

`.agents/quenching/bin/cq components read <path> --sections "§X"` resolves **any** markdown file, not only
`references/**` — a target repo's own `/docs/standards/**.md` and
`/docs/glossary.md` included. Where a step needs only the one or two sections that
govern its decision, address them the same way this plugin already addresses its own references;
reading the whole file through the plain file-reading tool is never the default once the file's
own heading index would have answered narrower.

A doctrine text that says "read `/docs/standards/<subject>.md`" without naming a section is instructing a
whole-file read on purpose — a short file with one governing rule throughout, or a step that
genuinely needs all of it. That default never changes on its own; narrowing to a section is an
assertion the citing text makes deliberately, the same way
[specs-execute/execution.md](.agents/quenching/references/specs-execute/execution.md)
§Read what the tasks must satisfy already narrows a spec's own `## Impact`-declared standards when
the bullet carries a `§`address.

**A `§`address is only as stable as the file it points at.** Against this plugin's own
`references/**` it is stable by construction — the reference ships in the same commit as the
body that cites it. A target's `/docs/` carries no such guarantee: the standard a target holds
may have been born from `assets/templates/automation/skills-standard.md` and never touched, or
evolved past it, and the two heading sets differ — `§Single-axis classification` exists only in the
evolved one. An unresolved section is a **refusal**, not a degradation: `.agents/quenching/bin/cq components read` exits
with 2, naming the headings the file does have. So a command body may hardcode a `§`address against
a target's `/docs/` only where it also says what to do on that exit 2. A path discovered at
runtime and read whole is unaffected — there is no address to be wrong.

<!-- rationale -->

The mechanism already generalizes — nothing about `.agents/quenching/bin/cq components read` is specific to the plugin's
own `references/`, and a doctrine site that still reaches for the plain file-reading tool on
a target's `/docs/` is paying for that gap, not for a real constraint. A rule two sections long
costs the same to read narrow as it does to read whole; only the surrounding rules that were not
in question stop being paid for.

The exit 2 is the useful half of the stability constraint: it names the headings the file actually
has, so a body that plans for it recovers in one call. What the constraint forbids is the silent
assumption that a target's bundle is shaped like this repository's.

## 3. A web source is cited, dated, and never the rule alone

<!-- rules -->

The citation carries three facts in the same place: the URL, the source's **publication date**,
and the date it was **retrieved**. A source with no publication date is marked `undated`; a
retrieval date invented from memory is not a date. Where the claim depends on the version or
release a page documents, cite it beside them.

A web source may ground an alternative in `## Alternatives Considered`, a rationale paragraph in
`## Design`, a description of an external tool's measured behaviour, or a version fact. It is
evidence for a **choice**, and it is citable.

A web source may not ground a binding rule alone. A `/docs/standards/` rule, a command's invariant,
or a task's `verify:` needs the repository's own contract beside the page — the code, a test, or a
run whose output is quoted. A page changes without notice and disappears; the rule must stand if
it does. Remove the URL: the rule still has to be defensible from what the repository carries.

Reading current practice is delegated to the read-only research sub-agent of
[specs-develop/questions.md](.agents/quenching/references/specs-develop/questions.md) §Surface
refinement. Its table carries the same three facts per row; a claim without them is dropped
rather than cited.

<!-- rationale -->

A citation with no retrieval date cannot be told apart from one copied from another summary.
The date pair places the source's publication and its observation in time; a rule that rests on a
page expires silently when the page moves.
