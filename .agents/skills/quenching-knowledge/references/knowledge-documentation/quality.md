# Documentation quality — rubric, integrity ledger and editorial loop

The hard quality gate for generated pages: eleven scored dimensions, five automatic forbiddens,
source provenance, and a bounded review → write loop.

## Contents

`.agents/quenching/bin/cq components read .agents/skills/quenching-knowledge/references/knowledge-documentation/quality.md`
returns the heading index; `--sections <name>` addresses one.

<!-- rules -->

## The scale

| Score | Meaning |
| --- | --- |
| **0** | Fail — absent, wrong, or actively misleading |
| **1** | Weak — present but barely acceptable |
| **2** | Good — does its job |
| **3** | Excellent — clearly above average |
| **4** | Reference — other pages should copy it |
| **5** | Iconic — memorable, nothing to add or cut |

## The eleven dimensions

| # | Dimension | 0 (fail) | 2 (good) | 4–5 (reference/iconic) |
| --- | --- | --- | --- | --- |
| 1 | **Hook** | opens with a definition/topic | first line states what & why | first line makes you have to read on |
| 2 | **Intent clarity** | unclear who/what it is for | one clear purpose | purpose obvious in 3 seconds |
| 3 | **Information architecture** | orphan / wrong section | fits nav and journey | anchors the whole section |
| 4 | **Scannability** | wall of text | headings, lists, callouts | skim gives 80% in 20 seconds |
| 5 | **Technical density** | vague / hand-wavy | accurate and specific | dense yet effortless to read |
| 6 | **Real examples** | none or `foo`/`bar` | real, runnable | real plus expected output |
| 7 | **Visual with function** | decoration or none | each visual informs | diagram is the explanation |
| 8 | **LLM-readability** | facts only in prose/images | stable headings, copyable | TL;DR plus contract lifted whole |
| 9 | **Integrity / source** | unsourced/invented | claims traceable | source ledger explicit |
| 10 | **Navigation / next step** | dead end | ends with a pointer | routes every reader onward |
| 11 | **Coverage / boundary** | empty section or internal TODO leaks | every mapped section has content and no internal TODO | coverage percentage and intentional exclusions are ledgered |

## Per-page score template

```markdown
### Score — <page>
| Dim | 1 Hook | 2 Intent | 3 IA | 4 Scan | 5 Density | 6 Examples | 7 Visual | 8 LLM | 9 Source | 10 Next | 11 Coverage | Avg |
| --- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| Score | 3 | 4 | 3 | 4 | 3 | 2 | 3 | 4 | 3 | 3 | 3 | 3.2 |
- Below threshold: <none | dim + why>
- Fix before done: <bullet list>
```

## Gate and five forbiddens

A page is done only when no dimension is 0, critical dimensions #4, #9 and #10 are at least 2,
dimension #11 is at least 2, the average is at least 2.5, and the hard build, nav, ledger and visual checks pass. Landing pages
and indexes require an average of at least 3.5. A page below threshold after the last round is
reported with its failing dimensions and reason; it is not silently shipped.

Coverage is `non-empty published documents / publishable documents × 100` — the denominator is
every `.md` in the bundle, **counted one by one**, never the handful of rows in the editorial map.
That distinction is the whole gate. While the denominator was the map's rows, a bundle of 106
documents reached 100% coverage with 12 pages and the check reported green: a map has around seven
rows, so sub-sampling a home was structurally invisible. A document is covered when it has a built,
non-empty page, or an explicit `do not publish` row naming that document and why.

The inventory covers the whole `/docs/` bundle, not only pages selected for one write slice. A section with only a heading, placeholder, source gap, or the shipped skeleton's own
section descriptor is not content and fails the gate
until the plan marks it intentionally excluded. Internal planning markers (`TODO`, `.quenching/`, prompts and
unresolved source-ledger notes) never cross into a published page; keep them in the source ledger
under `.quenching/documentation/`.

The five forbiddens are **fake depth**, **pretty-but-useless**, **README dump**, **claims without
source**, and **section without intent**. Any one is an auto-fail.

The delivery checklist is explicit:

- [ ] **No fake depth** — no page that sounds technical but says nothing checkable.
- [ ] **No pretty-but-useless** — no visual, hero or flourish without a function.
- [ ] **No README dump** — every page is rewritten for a reader who arrived from search.
- [ ] **No claims without source** — every strong claim has an origin in the ledger.
- [ ] **No section without intent** — every section answers “why keep reading?”.
- [ ] `zensical build --clean --strict` passes with zero issues.
- [ ] No orphan pages and all relative internal links resolve.
- [ ] Every extension used by a page is enabled in the site layer.
- [ ] Every page ends with a next step and every reusable contract has a TL;DR block.

## Source ledger

Strong claims may come only from one of five origins:

1. **Source file** — a `.md`, spec or README in the repo.
2. **Code** — a file and line in the codebase that proves it.
3. **Official docs provided** — a document the user pointed to.
4. **A link the user gave** — an explicit reference.
5. **Marked inference** — reasoning labeled `inferred:` or `likely:`.

```markdown
### Source ledger — <page>
| Claim | Source | Used in | Confidence |
| --- | --- | --- | --- |
| "85% of devs use AI coding agents" | Google, *New SDLC* (2026), p7 | landing stat band | high |
| "hooks fire only when wired" | `assets/hooks/README.md` | artifacts page | high |
| "most repos drift within a quarter" | inferred from source | intro | low — marked |
```

When a source is missing, write `source gap: <the claim + what's missing>` in the ledger, cut or
soften the claim, or leave a visible `!!! note "Needs a source"`. Carry every open gap into the
delivery report. Numbers and quotes are copied with attribution; computed read-times explain the
formula (`~N min at 200 wpm`).

## Editorial loop

The passes are **plan/architect → write/storyteller + visual → agent-read → review/critic → write
reconstruction → build/validation**. The plugin distributes no docs agents: for a large batch,
`write` and `review` may fan out with ad-hoc `Task` slices, each returning a condensed summary.
Only `write` edits prose; `review` is read-only; `build` owns the site layer and validation.

Run at most three `review → write` rounds. A source gap is carried forward because craft cannot
invent its missing origin. A validator never passes a red build.

<!-- rationale -->

The rubric makes subjective craft legible without pretending that a missing fact can be repaired
by nicer prose. Bounded rounds prevent an editorial loop from becoming an unbounded token sink.
