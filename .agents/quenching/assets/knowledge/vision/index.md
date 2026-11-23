# `vision/` — direction, segmented by area

Where the platform is heading (target state, "the what and why"), **without a schedule**.
The vision is **segmented by area**: one file per pillar/area (`<area>.md`, `type: vision`),
not a single `VISION.md`.

**Boundary:** direction **carries no deadline, milestone, or order**. A **raw task** toward
it lands in `specs/backlog/` (the task inbox, outside this bundle); what **has already
become reality** distills into [standards/](/docs/standards/index.md); an agreed-but-unproven
decision about *how* also lands in [standards/](/docs/standards/index.md) as
`authority: background`.

## Organization

```
vision/
  <area>.md        # one shell per area (type: vision) — e.g. platform.md, data.md
```

Mold: `vision/area.md` (applied by `quenching-knowledge-add`). This home starts empty — the repo
declares its areas.
