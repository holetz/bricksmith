# Governance

`bricksmith governance` declares tags, grants and metadata for **managed** resources, plans the
statements, applies them on request and audits what is actually in place.

## Configure

See `examples/governance/governance.toml` for a complete neutral example. Key ideas:

- `categories` are yours; `unclassified` always exists and is the default.
- Principals are logical names mapped per environment; an unknown, unmapped or expired mapping
  fails before anything runs. Real identities do not belong in the repo.
- Only `managed = true` resources are touched. `{catalog}`/`{schema}` placeholders come from
  `--var` (the context catalog is the default `catalog`).
- `[[policy]]` rules and `--hook module:callable` hooks map a classification to tags and grants.

## Run

```bash
bricksmith governance plan  --config governance.toml --var schema=sales
bricksmith governance apply --config governance.toml --var schema=sales --confirm
bricksmith governance audit --config governance.toml --var schema=sales [--json]
```

`plan` and `audit` also accept `--live state.json` to work offline.

## What the states mean

| State | Meaning |
|---|---|
| desired | declared, not present live |
| applied | statement succeeded but the live state does not show it yet |
| verified | read back from the platform and matching |
| unavailable | cannot be applied or read; the reason is shown |

Classification is a tag: **metadata only, never masking**. Column masks and row filters have no
mutation adapter in this release; declaring one yields `unavailable`, and existing ones are
detected read-only. Reconcile never revokes or unsets: extra tags, grants and policies are
reported as external (`governance.external.*`) and left alone.

## Reports

`governance_report(audit)` returns coverage (managed, unmanaged, per-classification, unclassified
list, state totals) and per-resource controls for catalog documentation.
`governance_findings(audit, severity)` returns registry-style findings; set severity per rule or
glob in `[severity]` (`error`, `warning`, `off`).

## Platform support

Proven on the free workspace: table tags, a table grant to a built-in group, comments, reading tags
through `information_schema`, grants through `SHOW GRANTS`, and reading column masks and row filters
(see `docs/evidence/bs-15-free.md`). Not proven: schema/catalog/view controls (planned the same way,
no remote proof), table properties apply (offline only), and any mask or row-filter mutation.
