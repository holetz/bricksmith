---
type: sidecar           # OKF concept type — non-empty (the textual extract IS the concept the LLM consumes)
title: <extract name — e.g. "Risk Committee deck 2026-Q2">
description: <one sentence — what the material says>
resource: <the binary source — path/URL (same as `binary:`)>
generated: { by: human:<author>, at: <ISO 8601 UTC — date of the binary source> }
audience: agent          # the LLM consumes the EXTRACT; the binary source is `human`
authority: background    # extract of human/external material — background, never a current contract
source: <author/origin of the binary — person, team, external standard>
maintainer: <who keeps the binary source alive>
binary: ./<source-file.pptx|pdf|drawio>   # PATH/URL of the binary — the agent does NOT open it
pages: <N>              # pages/slides in the source (signals the avoided cost)
---

# <material title>

> **Sidecar** (textual extract alongside the binary). The LLM reads **this `.md`**, not the
> `binary:` above. Only the **high-signal content** an agent needs to reason about it lives here.

## Summary (2–5 lines)
<what the material says, in navigable prose — the thesis, the decision, the key number.>

## Key points
- <fact/decision/definition extracted, with a reference — e.g. "slide 4: …">
- <…>

## When the agent should open the `binary:` (rare)
<what ONLY exists in the source and was not extracted — usually leave empty (the extract suffices).>

<!-- MOLD (quenching · sidecar) for external binaries consumed via extract:
     reference/regulations/ (normative PDFs) and other external binaries we consume.
     A sidecar marked `authority: current` is a smell (external/human material is background).
     If the CONTENT became a current contract, distill it into standards/ — the sidecar then
     stays only as a historical pointer. Extract what governs a decision; index the rest. -->
