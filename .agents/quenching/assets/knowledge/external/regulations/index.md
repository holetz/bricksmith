# `external/regulations/`

Regulatory standards **we must comply with** — the external rule itself (often a PDF),
consumed via a **sidecar** (`type: sidecar`): the LLM reads the textual extract, never the
binary.

**Boundary:** the regulation as an external fact — **our implementation** of it (how we
satisfy the rule) lives in [standards/mlops/](/docs/standards/mlops/index.md) or the
relevant standards subject. A sidecar marked `authority: current` is a smell (external
material is background). Starts empty; the repo fills it.

Mold: `sidecar.md` (applied by `quenching-knowledge-add`).
