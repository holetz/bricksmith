# `documentation/` — documentation

Understanding-oriented pages that explain how and why the product works — business rules
included — for the reader of the documentation site, engineering or not. The home covers the
Diátaxis **explanation** quadrant; each page carries `type: documentation`.

The name diverges from OKF v0.2, which calls this home `explanation/`: `documentation/` is the
name its readers outside engineering look for. A bundle still on `explanation/` gets
`okf-legacy-home` with the rename as its remedy.

**Boundary:** a page here is part of the **published site** for product users.
Internal team understanding — mental models and learnings — belongs in the `concepts/` home. The
root glossary remains canonical there and is copied into the bounded `site-source/` input by
`.agents/quenching/bin/cq knowledge site-source`; readers reach its published route without a second editable source.

## How to organize

The repo decides which pages exist — the skeleton ships none. Standalone pages live
as `.md` files here; group by subject into subfolders with their own `index.md`.
