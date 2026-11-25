---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), AskUserQuestion
---

# /quenching-design genre-new — one genre, N destinations

**Input**: `$ARGUMENTS` (one genre name or a short description of it).

This command mints one stable editorial contract. It does not produce an instance of the document;
`.agents/quenching/bin/cq design render` does that after fields have data.

## Workflow

### 1. Prove the front exists

Resolve `cq` per
[tool-resolution.md](.agents/quenching/references/align/tool-resolution.md)
§Resolving the tool and run `design status --json`. An absent or invalid front routes to
`/quenching-design align` and stops. Read existing genres and medium templates for name/path
collisions.

**Done when:** the front is healthy and the proposed genre has no collision.

### 2. Fix the contract

Derive a canonical-English kebab-case slug and ask only for missing choices that change output:

- display name and editorial register;
- fields, each required or optional with a one-line meaning;
- destinations among HTML, Typst and PDF (PDF uses the Typst template and compiler);
- composition and guardrails that stay true in every destination.

Use button, input, navigation, chip and card when the genre needs UI-like primitives; eyebrow,
rule and frame are the editorial extensions. Fields hold content, never token values.

**Done when:** slug, register, fields, media, composition and guardrails are explicit.

### 3. Present one plan and gate

Show the genre file, one template per underlying medium (`pdf` shares Typst), the exact CLI
invocation, and the MEDIUM.md regeneration. Execution profile is default: no fork, model pin,
background run or hook. Wait for one OK.

**Done when:** the user accepts; a decline writes nothing.

### 4. Mint through the deterministic tool

Invoke one command, repeating `--media` and `--field`:

```bash
.agents/quenching/bin/cq --root . design genre new <slug> \
  --name "<name>" --register "<register>" \
  --media <medium> --field "<name>:<required|optional>:<description>" --json
```

Fill the generated genre's Composition and Guardrails sections with the accepted content without
putting primitive values in them. Merge only that gap; never overwrite an existing genre or
template.

**Done when:** the genre and every declared medium template exist with the accepted contract.

### 5. Verify

Run `design build --json`, `design doctor --json`, then render one smallest fixture into every
declared non-PDF medium. For PDF, run it only when `typst` already resolves; otherwise report PDF
as supported but locally unverified. Inspect outputs for unresolved `{{...}}` placeholders.

**Done when:** doctor exits 0, MEDIUM.md lists the genre, every available renderer succeeds, and
the PDF compiler result or absence is stated.

### 6. Report

Report the invocation, genre path, templates, destinations, smoke-render outputs and any
unverified PDF capability.

**Done when:** one genre — no sibling work — is fully accounted for.

## Invariants

- One invocation mints one genre.
- Genre fields never redeclare tokens.
- One genre may target many media; PDF is a Typst compilation, not a second template authority.
- Never overwrite a collision and never claim PDF verification without compiling it.
- Never hand this command `context: fork`; its plan gate is mid-flow.
