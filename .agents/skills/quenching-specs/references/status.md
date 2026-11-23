---
allowed-tools: Bash(cq:*), Bash(cq.cmd:*), Bash(python3:*), Read
---

# /quenching-specs status — read the specs front

The canonical documents live on the `quenching` branch (the git store), or in a deprecated tracker
backend. There is no local `/.specs/` root.

The recognised configuration envelope and its defaults live in
[specs-align/plugin-configuration.md](.agents/quenching/references/specs-align/plugin-configuration.md)
§The envelope and recognised namespaces. This command reports that state; it does
not create a local specs store or infer missing configuration.

## Workflow

1. Resolve `cq` through the repository's declared tool-resolution rule.
   **Done when:** the tool path is resolved or refusal is reported.
2. With an ID, run `.agents/quenching/bin/cq specs status --spec <id> --json`; without one, run `.agents/quenching/bin/cq specs list --json`.
   The command's payload is the source of the stage, records, task progress and provider locator.
   **Done when:** the requested spec list or record is in hand.
3. Run `.agents/quenching/bin/cq specs config --json` when the report needs to explain provider selection. A missing or
   unrecognised provider is a refusal, never a fallback to local files. **Done when:** provider
   configuration is reported when needed.
4. Report the tool's JSON and exit code. Do not enumerate local folders, infer a filesystem root,
   or re-render provider data into a second source of truth. **Done when:** the JSON, exit code, and
   refusal/absence state are reported.

## Invariants

- Never write, scaffold, align, migrate or repair anything.
- Never treat an absent local `/.specs/` directory as a finding; external backends do not use it.
- Never call `FilesBackend`, infer `backend: files`, or offer a local fallback.
- Use `/quenching-specs develop`, `/quenching-specs execute`, or `/quenching-specs conclude` for
  lifecycle work on one named provider-owned spec.
