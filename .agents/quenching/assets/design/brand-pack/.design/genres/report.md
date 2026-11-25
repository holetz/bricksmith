---
name: "Report"
slug: "report"
register: "read"
media: "html, typst, pdf"
---

# Report

## Fields

- `title` — required document title.
- `subtitle` — optional one-line framing.
- `body` — required Markdown-compatible body.
- `date` — optional publication date.

## Composition

Open with one decisive title block, keep body measure readable, and use a single accent thread per
section. Evidence belongs beside the claim it supports.

## Guardrails

- Cite `/.knowledge/standards/design/production.md` before producing the artifact.
- Use generated tokens through the medium adapter; never copy a primitive value into the template.
- Preserve heading order and accessible contrast in every destination.
