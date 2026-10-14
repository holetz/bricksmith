# Autopilot human-decision epic — delivery execution surfaces

- Date: 2026-10-14
- Status: paused at human request; needs-human decisions; no execution authorized
- CQ: not created; durable local decision epic only
- Parent: [Epic 0001](0001-bricksmith-library.md)
- Branch: `epic-0001`; recovery `d72b46e`; local artifact hardening `01f828e`

## Problem

Autonomous work cannot widen executable surfaces or choose the autonomous protocol itself.
The 2026-10-14 autopilot delegation authorized ordinary product work but reserves the following
choices for the human. The later stop request overrides launches, publication and continuation.

## Evidence and decisions

| Decision | Existing evidence | Human choice required | Dependent acceptance |
|---|---|---|---|
| Unattended workspace CI authentication | BS-00 free evidence:42-44 records corporate and unattended auth as not run; `.github/workflows/ci.yml:15-47` contains local checks only | Select/prove free CI authentication, secret delivery and identity permissions; explicitly choose corporate trigger/destination, never fallback | BS-00.6 and BS-17 selected-workspace coverage |
| Generated CI and template execution | Epic BS-16.1 requires CI; delivery contracts prescribe Copier updates; static template spec excludes executable automation | Approve exact generated CI commands/permissions and whether any Copier tasks/migrations/hooks/scripts are needed; defaults remain absent | Full BS-16.1/16.5 and rendered deployment/update automation |
| Plugin runtime workflows | Epic BS-18.2/18.3 requires workflows invoking public contracts; plugin package/skills are absent | Approve exact executable skill instructions/resources and command/environment scope; agents/hooks/MCP require a specific justified workflow | BS-16.4, BS-18 runtime behavior and BS-17 agent-assisted scenario |
| Supported Claude installation/testing route | Delivery contracts separate static manifest checks from live supported-Claude proof | Choose supported Claude versions and repository-local load/install/update route, then authorized smoke environment | BS-18.4/18.5 discovery/live smoke and BS-17 three-artifact compatibility |

## Design

Do not silently implement these surfaces, install hooks, widen grants, log in, switch workspaces,
create marketplace entries or publish. Narrowing grants is permitted only within resumed work.
Record each concrete decision before developing its execution spec. Human decisions do not turn
unperformed remote/plugin tests into passing evidence. Reuse the original epic rather than
migrating the legacy CQ store or duplicating its delivery backlog.

## Tasks

- [!] H1 Decide unattended free/corporate CI authentication — needs-human; user requested stop.
- [!] H2 Decide generated CI/Copier execution surfaces — needs-human; user requested stop.
- [!] H3 Decide plugin runtime scope/resources — needs-human; user requested stop.
- [!] H4 Decide supported Claude local installation/smoke route — needs-human; user requested stop.

## Handoff

Store discrepancy: CQ 6.4.0 resolves the legacy GitHub default because .agents/quenching.json is absent; the human expects the quenching branch store. Existing CQ issues #1-#4 are retained, and local specs are durable. No migration, configuration change, issue creation or protocol/store-design choice is authorized by this paused handoff. Resolve the store decision with the human before any resumed tracker writes; do not modify the quenching branch automatically.

This is a local proposed/stopped epic, with no CQ ID and no implemented task. Resume only when
explicitly instructed. Six safe candidates remain unimplemented: catalog CQ #3, static Copier
CQ #4, release readiness, quoted-secret redaction, null required matches and nonfinite freshness.
Ordinary task design may progress after resume, while these executable-surface choices remain
with the human. Release publication and visibility remain separate decisions.
