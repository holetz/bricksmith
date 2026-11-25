---
type: standard
title: Design production
description: The gate for producing a branded artifact from one token source across web, Typst, and PDF
resource: .design/genres/**, .design/media/**
tags: [design, production, accessibility]
generated: { by: quenching/6.13.0, at: 2026-08-28T00:00:00Z }
audience: both
authority: background
source: quenching design brand pack; strengthen with confirmed production constraints
maintainer: project
---

# Design production

Every artifact cites this standard before production, selects a genre contract, imports the
generated adapter for its medium, and verifies the rendered destination rather than only its source.

## Do's and Don'ts

### Do

- **Do** keep every primitive declaration in `/.design/tokens.json` and regenerate adapters.
- **Do** preserve accessible foreground/background pairs and semantic heading order.
- **Do** inspect the actual HTML, deck, or PDF after rendering.

### Don't

- **Don't** copy a color, type size, radius, or spacing value into a medium primitive.
- **Don't** use an asset whose license and binding role are not recorded.
- **Don't** treat the lossy DESIGN.md projection as the whole DTCG source.
