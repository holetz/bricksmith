# Specs store conformance

This reference defines the checks for the specs front. The documents on the `quenching` branch are
the source of truth and tracker cards are their projection; `.agents/quenching/bin/cq specs` is the uniform surface that
reads and writes both. The `github` and `azure-boards` backends are deprecated, still work, and
`.agents/quenching/bin/cq specs doctor` flags them (`sp-backend-deprecated`).

## Store configuration

The target's `.agents/quenching.json` (or the legacy `.agents/quenching.json`) selects the store
(`"backend": "git"`) and may refine card
settings and project conventions; the branch's `quenching.json` holds the specs-axis keys. No
command silently switches the store. A legacy repository-store value is refused with an actionable
finding.

Configuration is validated before any write:

- The git store requires a remote that holds, or can receive, the `quenching` branch.
- A card provider requires a resolvable repository (GitHub) or the placement needed to create a
  work item (Azure Boards).
- An unknown backend refuses; it never falls back to another transport.

The configuration details and defaults live in
[plugin-configuration.md](plugin-configuration.md).

## Probes

Run the store checks before reading or changing a spec:

```bash
.agents/quenching/bin/cq specs doctor --json
.agents/quenching/bin/cq specs config --json
.agents/quenching/bin/cq specs validate --json
```

Exit `0` means the store and fetched front are conformant. Exit `1` reports findings that a
human or the owning lifecycle command must resolve. Exit `2` is a refusal: authentication,
store selection, or required configuration is missing or invalid.

## Lifecycle ownership

`create`, `develop`, `execute`, `status`, `triage`, and `conclude` own the spec lifecycle.
There is no installer step for the specs branch: the first write creates it, and
`.agents/quenching/bin/cq specs migrate --to git` moves a deprecated tracker backend onto it. A complete spec is closed by
`conclude`, which records the outcome in the spec and distils durable knowledge into the OKF
bundle.

## Finding policy

Findings name the store or card provider, the missing configuration, and the command that owns the
repair. The checks must not invent a path, fabricate a phase, or offer a second store.
