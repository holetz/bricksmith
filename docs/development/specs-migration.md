# Canonical specification storage migration

On 2026-10-14 the human authorized converting Bricksmith planning to the `quenching` git
branch after Quenching prerequisite spec 1654. Product development remains **paused**.
The conversion publishes specification metadata only; no product PR, merge or release occurred.

Both agent hosts use regular files, `.agents/quenching.json` and `.claude/quenching.json`,
containing copies of the same configuration envelope (`backend: git`).
`tests/test_quenching_config.py` requires equal parsed JSON and the git backend.
Branch configuration disables GitHub cards with `provider: none`.
GitHub issues 1–4 are closed, thin retired projections; their history remains available.
Closing those cards does not mark unfinished canonical specifications done.

The [manifest](specs-migration.json) maps every source and all 32 native IDs. The 26 local
source documents are preserved as readable snapshots with exact byte/hash reconstruction.
Local mirrors of artifact #2, catalog #3 and template #4 were folded into those same native
identities. The other records decompose existing slice/epic/human requirements and record this
migration; no duplicate health or model-transport issue was created. Reusable guidance now lives
in [the specification workflow](spec-workflow.md); the old specs README is navigation only.
Only 25 verified old planning bodies were removed. Evidence and guides were retained, with
necessary links repaired. Original code history is preserved at `302de4d`; recovery `d72b46e`
and local artifact hardening `01f828e` remain intact.

One explicit normalization exception applies to tracker #1: its Validation section contained a
private absolute machine checkout path. The private gate rejected its exact-source publication.
Only that path was replaced with generic checkout text; lifecycle, tasks, outcome and other
sections were unchanged. The original export is retained in ignored local backup and original
GitHub edit history. Original SHA-256: 313a9a470dda2f458c98d720b2b64049c2762bcab3d657f93aea2de9ed2aff21. Sanitized SHA-256: 1f87f11b7cf7d18e4e3b4e42a76f59bd73d6af7d0b6192c52232f40de7e4fa7a.
Supported migration then reported zero state differences and exact sanitized-source equality.
The rejected commit was an unreferenced orphan, not an ancestor of published metadata.

Installed **stock CQ 6.4.0 still adds `--no-verify` to git-store pushes**. The installed plugin
was not patched. Ordinary read-only status/config/list commands are safe; unadapted writes do
not satisfy this repository's hook policy. Until the Quenching fix is available, metadata writes
require the same explicit temporary process adapter used for this migration: wrap
`GitBackend._git`, remove only `--no-verify` when the operation is `push`, run the existing
private leak checker against the candidate and previous SHAs, and delegate to ordinary git push.
Abort on any private gate or hook failure. Actual `.githooks/pre-push` remains enabled and runs
its normal private check; no hook, permission or execution grant was installed or widened.
The private gate passed for all local candidate texts and all successful metadata publications.

Verification covered source bytes/hashes, tracker state/equality, native records and epic DAGs,
retired cards, shared configuration and repaired local links. Canonical validation passed with
only nonblocking unrefined warnings. No product or remote acceptance tests were run for this
metadata conversion. Imported completed slices reflect original checked tasks, not new testing.

On explicit development resume, the six safe candidates are catalog #3, template #4,
readiness #23, redaction #24, null-match #25 and finite-freshness #26. Serialize catalog → template
→ partial readiness and null-match → freshness. Catalog consumes completed registry APIs while
finishing registry catalog acceptance (#14); it must not wait on that remaining acceptance.
Partial readiness reports missing full release proof rather than waiting to document it.
Artifact #2 remains partial/external-blocked: task 1.1 is committed in `01f828e`, task 1.2 pending.
Full model proof (#19), unattended CI (#7/#28), plugin execution (#27/#30), generated automation
(#29) and supported live-Claude proof (#31) remain unresolved. Human-decision epic #6 owns the
never-forced choices. Migration does not automatically resume or clear any of these blockers.

The canonical [migration record](https://github.com/holetz/bricksmith/blob/quenching/specs/archive/32.md)
is archived done. Cleanup checkpoint: `42b636e`; original source history and private normalization
exception are recorded in the manifest. Development remains paused.
