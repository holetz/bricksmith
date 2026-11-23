# Documentation craft — storytelling, page patterns, and signature experience

The page-writing layer: intent-first prose, progressive disclosure, useful visuals, and reusable
skeletons. Exact component syntax lives in [visual.md](visual.md).

## Contents

`.agents/quenching/bin/cq components read .agents/skills/quenching-knowledge/references/knowledge-documentation/craft.md`
returns the heading index; `--sections <name>` addresses one.

<!-- rules -->

## Voice and page arc

- Open with what the reader is trying to do, not a definition.
- Use second person, present tense and active voice.
- Show a concrete example before explaining the abstraction.
- Keep paragraphs to two to four sentences; every sentence earns its place.

Every page follows **hook → fast path → depth on demand → next step**. Put edge cases in `???`
blocks or a dedicated page. Comparisons are tables, sequences are numbered lists or Mermaid,
options are cards, and risks are callouts.

The published page is the reader-facing projection. Keep source ledgers, TODOs, prompts and
unresolved planning contracts in `.quenching/documentation/`; a page must contain real prose or a
clearly labeled intentional exclusion, never an internal work marker.

The first line states the payoff or stakes. “Point it at a repo; get a scored gap report; install
the fixes with your OK.” is a hook. “This document describes the hooks subsystem.” is not.

## Page patterns (twelve skeletons)

### 1. Landing page

**Intent:** promise and orient. **Audience:** skimmer. **Shape:** hero → value → cards → problem,
solution and quick-start CTA.

```markdown
<div class="q-hero" markdown>…name · one-sentence promise · two buttons…</div>

<div class="grid cards" markdown>
- :material-rocket: **Just run it** — first win in 2 commands. [Get started →](install.md)
- :material-lightbulb: **Understand why** — the problem in depth. [Concepts →](concepts/index.md)
- :material-book: **Reference** — the exact detail. [Reference →](reference/index.md)
</div>

## The problem   ## The solution   ## Quick start
```

### 2. Quick start

**Intent:** first success fast. **Audience:** implementer. **Shape:** prerequisites → copy-paste
steps → expected result → next step.

```markdown
!!! note "Prerequisites"  …one list…
1. `command one`   2. `command two`
!!! success "You should see…"  …the observable outcome…
Next: [The full workflow](workflow.md)
```

### 3. Tutorial

Goal → numbered stages, each with code and expected result → recap. Keep one runnable thread with
no branching; state the finished artifact up front.

### 4. Concept / explanation

One-line idea → why it exists → diagram → boundary/tradeoff → reference link → `TL;DR for agents`.

### 5. Reference

Stable item headings, an options table and one real example per item. Optimize for Ctrl-F and
predictable anchors; do not bury exact detail in narrative.

### 6. Architecture

One-line system summary → Mermaid diagram → node-to-page table → fixed/adaptive boundaries.
Describe relationships and link to each part's page.

### 7. Workflow / process

Governing callout → Mermaid or numbered steps → scenario tabs or cards → output of each step.

### 8. Decision / tradeoff

```markdown
| Option | Best when | Cost | Avoid when |
| --- | --- | --- | --- |
| A | … | … | … |
| B | … | … | … |
!!! tip "Recommendation"  Default to **A** unless … — because …
```

### 9. Troubleshooting

Lead with a symptom → cause → fix table, then put long cases in `???` blocks.

### 10. Catalog / index

One row per entry (name, purpose, link, status badge), generated deterministically where possible;
each row links to its reference page.

### 11. Changelog / evolution

Reverse chronological; each entry is date → summary → impact → link. Stable version headings; never
rewrite history.

### 12. For humans / for agents

```markdown
## For agents
!!! abstract "TL;DR for agents"
    - Contract: `input → output`
    - Invariants: …
    - Copy-paste: ```…```
    - Canonical anchors: #install, #run, #config
```

## Rewrite moves

| Raw shape | Rewrite |
| --- | --- |
| README topic paragraph | landing hook, promise and route cards |
| comparison in prose | table with a purposeful column per fact |
| process in prose | Mermaid flow plus a sentence mirroring it |
| vague configuration section | hook, fast path and collapsed tail |
| human-only explanation | `TL;DR for agents` with input/output/invariants |
| confusing route list | two to four intent-bearing cards |
| troubleshooting paragraph | symptom/cause/fix table |

Use real names and expected output. A visual must carry information, and a slogan never replaces a
contract.

## Signature experience

The eight elements are central thesis, repeatable line, controlled metaphor, first mental map,
primary visual flow, landing promise, 60-second aha, and reading progression. Find the thesis in
the IA pass: “If the reader remembers one thing, it’s ___.” Define a metaphor literally before
reusing it. A repeatable line is short, true and sourced; it is never empty marketing.

<!-- rationale -->

Beauty serves precision. A page that is attractive but vague scores zero on density, source or
structure and returns to the editorial loop.

## Craft checklist

- First sentence says what this is and why the reader is here.
- Fast path is reachable without scrolling past prerequisites.
- Rules state their why; examples are real and copyable.
- Long detail is split or collapsed; the page ends with a pointer.
- Internal links remain relative for strict builds.
