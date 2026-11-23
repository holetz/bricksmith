# Agent-readable documentation — contracts, anchors and copyable facts

Rules for the second audience: agents that search, lift, reconstruct and cite a page.

## Contents

`.agents/quenching/bin/cq components read .agents/skills/quenching-knowledge/references/knowledge-documentation/agent-readability.md`
returns the heading index; `--sections <name>` addresses one.

<!-- rules -->

## Stable headings and anchors

Name a heading in the reader's words (`Wiring the hooks`, not `Details`). Keep headings stable
after publication; `toc.permalink: true` makes `page/#wiring-the-hooks` a durable address. Reference
pages use one stable heading per item so an agent can deep-link to `#option-name`.

## TL;DR for agents

Pages carrying a reusable contract include a compact, high-signal block:

```markdown
!!! abstract "TL;DR for agents"
    - **Does:** audits a repo's knowledge surface, installs fixes with confirmation.
    - **Contract:** `repo path → scored report → item-by-item install`.
    - **Invariants:** never removes without OK; three dimensions are proposed-only.
    - **Run:** `.agents/quenching/bin/cq knowledge validate docs`
```

Put facts, inputs, outputs and invariants in text. A diagram reinforces a fact; it never owns the
only copy of one.

## Copyable examples and explicit contracts

Examples use real commands, paths and names, and show the expected output. Avoid `foo` and `bar`.
State preconditions, input/output shapes and the exact condition under which a step runs. Define a
central term once in the glossary and, when the editorial map exposes it, link to the published
relative glossary route rather than `/docs/glossary.md`. A concept map or choose-the-right-file table
gives an agent a deterministic route.

## Links and language

Internal links are relative and survive `--strict`. A metaphor follows its literal definition.
Never hide a critical fact in an image, and never use an undefined metaphor.

<!-- rationale -->

Reconstructable docs help humans too: predictable structure lowers the cost of finding, checking
and copying one exact fact.

## Checklist

- Headings are descriptive and stable.
- Anchors resolve and are guessable.
- Reusable contracts have a `TL;DR for agents` block.
- Examples are real, copyable and include expected output.
- Inputs, outputs and invariants are explicit.
- Core terms have one glossary definition.
- Routing tables and links are present.
- No fact lives only in an image or metaphor.
