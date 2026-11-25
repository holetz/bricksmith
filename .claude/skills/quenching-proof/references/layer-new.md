---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Read, Grep, Glob, Bash(python3:*), Bash(mkdir:*), Bash(mv:*), AskUserQuestion, Write, Edit
---

# /quenching-proof layer-new — mint one proof layer

**Input**: `$ARGUMENTS` — one new layer name and an optional target repository root. A layer name
must be a single stable directory/marker name; the command never invents a taxonomy or mints more
than one layer in a run.

This command creates the structural contract for one layer a human named. It binds the directory,
the derived marker, its reach and budget, the collection hook, the optional shared fixture module,
and the generated proof README as one operation. Existing tests that might belong there are a
proposal only; moving them changes what evidence a gate collects and needs its own confirmation.

Read [proof-align/layer-contract.md](.agents/skills/quenching-proof/references/proof-align/layer-contract.md),
[proof-align/target-structure.md](.agents/skills/quenching-proof/references/proof-align/target-structure.md),
and the two templates under `.agents/quenching/assets/templates/proof/` before writing. Resolve
`cq` per [tool-resolution.md](.agents/quenching/references/align/tool-resolution.md),
and branch on exit code and JSON: `0` is clean, `1` carries findings, and `2` is a refusal.

## Workflow

### 1. Read the current proof declaration

Set `TARGET_ROOT` to the supplied root, or `.` when it is omitted. Read the current state first:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" proof inventory --json
```

Refuse if the proof root is missing, the layer name already exists, the name is empty or unsafe as
a directory/marker, or the target uses a non-pytest runner. Do not create a layer to repair an
unrelated finding, and do not infer a layer from existing test directories.

**Done when:** the target proof root, configuration, existing layers, collection hook, fixture
library and candidate test paths are known, or a refusal has stopped the run without writes.

### 2. Ask the four binding questions

Ask exactly these four questions for the named layer:

| Question | Bound declaration |
| --- | --- |
| What does this layer prove, in one line? | The README section and the marker's registered description. |
| What may it reach — `nothing`, `tree`, `session`, or `workspace`? | The layer's declared reach and the ceiling for shared fixtures. |
| What is its wall-clock budget? | The declared budget and whether the layer belongs in the fast gate. |
| Should existing tests move into it? | A proposed list of paths and a separate migration decision; never an automatic move. |

The first three answers authorize the layer contract plan. The fourth only authorizes preparing a
counted proposal. If the target has no declared layers, this command is still allowed to mint the
one named layer; it does not propose a taxonomy for any other test.

For the proposal, compare each existing test module's current proof-root path with the named
layer's declared location and report only a counted, path-level candidate list. Do not infer
purpose from test bodies or imports. When this is the repository's first layer, suppress the list
entirely and say that every test would be a candidate without a prior taxonomy; asking the owner
to move all of them would be guesswork rather than evidence.

**Done when:** the four answers are explicit, the candidate paths and count are visible, and the
structural plan plus the separate migration decision are ready.

### 3. Present the two decisions

Present one plan for the five coupled structural edits:

1. add the named layer to `.agents/quenching.json`'s `proof.layers` map with reach, budget and
   `required: false` until the owner has filled and deliberately promoted the layer;
2. create `tests/<layer>/`;
3. register the derived marker and its one-line description in the target's pytest configuration;
4. add the recognizable path-to-marker block to `tests/conftest.py`, installing the collection
   hook template when no hook exists;
5. create `tests/fixtures/<layer>.py` when the reach requires shared setup, then run
   `.agents/quenching/bin/cq proof readme --write` to regenerate the layer README.

Ask once for authorization of this structural batch. Present the proposed existing-test moves as a
second, separately confirmed item with the count and every path. A declined migration leaves every
test path unchanged while the new layer remains legitimately empty.

**Done when:** the structural plan has its one approval and the migration is separately accepted
or declined; a rejection writes nothing.

### 4. Apply the five edits as one unit

Under the structural authorization, apply only the named layer's five edits. Preserve authored
content outside generated blocks and existing collection-hook rules. Use the supplied templates,
replace their placeholders with the answers, and keep the layer's reach and budget in the shared
configuration rather than duplicating them in fixture prose.

Do not move tests until the separate migration confirmation is affirmative. If it is affirmative,
move only the listed paths, preserve imports and report the exact list. Never create placeholder
tests, invent another layer, retire an existing layer, install CI, or run the target suite.

Regenerate the README:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" proof readme --write --json
```

The operation must either leave all five declarations complete or leave a state the doctor names
precisely; do not silently continue after a failed intermediate edit.

The fixture-library decision is reach-sensitive: `nothing` does not create an empty fixture module;
the generated README records `none`. `tree`, `session`, and `workspace` create the layer's fixture
module from the reach-scoped template because those boundaries need a declared shared home. An
existing fixture library is preserved and never replaced merely because the new layer has no
fixture of its own.

**Done when:** the named layer, marker, hook, fixture home and README are in place, and no
unconfirmed test move or target-suite execution occurred.

### 5. Re-verify and report

Run the static verifier:

```bash
.agents/quenching/bin/cq --root "$TARGET_ROOT" proof doctor --json
```

Report the layer name, purpose, reach, budget, required state, marker, fixture module, test paths
moved or left in place, README digest, and the closing doctor payload. State whether the new layer
is empty by design and state plainly that the target suite was not run.

If a finding remains, distinguish the new layer's evidence from pre-existing residue and name
`/quenching-proof align` or the target owner's reviewed change that closes it. Do not call an empty
new layer healthy; report its declared state and the command that owns the next decision.

**Done when:** the five-edit contract has a static closing proof, the migration decision is visible,
and the report hands back every remaining decision without writing anything else.

## Invariants

- One named layer per run; no inferred taxonomy and no placeholder layers for unclaimed evidence.
- The directory, marker, hook, fixture home and generated README are one coupled structural unit.
- Reach and budget live in `.agents/quenching.json`'s `proof.layers` declaration.
- Existing test moves are proposed with paths and count, and never happen without their own
  confirmation.
- Existing hook rules and authored README prose survive; generated zones are the only bytes the
  generator replaces.
- The target suite is never run. `.agents/quenching/bin/cq proof doctor` is the closing verifier, not a test runner.
- A new layer may be empty by design; the report states that fact instead of manufacturing proof.
