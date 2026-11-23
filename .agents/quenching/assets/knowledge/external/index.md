# `external/` — EXTERNAL reference material

**Factual material about what WE CONSUME** — tool docs, library docs, regulatory
standards. External to what we produce; background, **never** our contract. Each doc
carries `type: external`.

**Boundary** (the subtlest one): `external/` = "facts about what **WE CONSUME**
(external)" — distinct from [standards/](/docs/standards/index.md) ("how **WE** do it") and
[catalog/](/docs/catalog/index.md) ("our **data**"). Example: the text of a regulation (a
PDF) lives in [regulations/](regulations/index.md) via a sidecar; **our interface** to it
(how we implement it) lives in [standards/mlops/](/docs/standards/mlops/index.md).

## Subfolders

* [tools/](tools/index.md) — docs for tools we use
* [libraries/](libraries/index.md) — docs for libraries / dependencies
* [regulations/](regulations/index.md) — regulatory standards (PDFs via sidecar)

External binaries (PDFs) are consumed through a **sidecar** (`type: sidecar`) — the LLM
reads the extract, never the binary.
