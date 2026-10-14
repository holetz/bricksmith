# Isolated experiments

Try a change without touching baseline outputs. A revision is a small file; the framework rebuilds the
chosen tasks in a revision-suffixed schema, reads inputs frozen at Delta versions and keeps evidence.

```toml
name = "rev_a"                       # destination schema: <schema>_x_rev_a
roots = ["main.sales.report"]        # tasks rebuilt; add downstream = true for their consumers
[parameters]
factor = 3                           # overrides context parameters for this run only
[[pins]]
input = "main.sales.events"          # version = N, timestamp = "...", or current = true
version = 0
[[seeds]]                            # history that read_state tasks need in the destination
output = "main.sales.history"
[[compare]]
output = "report"
keys = ["id"]
```

```bash
bricksmith experiment plan --revision rev_a.toml --freeze rev_a.evidence.json   # review, then freeze
bricksmith experiment run --evidence rev_a.evidence.json                         # dry run
bricksmith experiment run --evidence rev_a.evidence.json --apply
bricksmith experiment compare --evidence rev_a.evidence.json --report-out gate.json
bricksmith experiment promote --evidence rev_a.evidence.json --report gate.json  # plan and impact report
bricksmith experiment retire --evidence rev_a.evidence.json --apply              # drop experimental outputs
```

- Unpinned inputs are read live and reported as such; pinned ones are reproducible.
- Collisions (an existing destination table, a destination equal to a baseline schema) stop the plan before
  anything is created. Writes outside the destination are refused at run time.
- A `[backfill]` revision whose tasks use `read_state` must seed the earlier periods, or planning fails.
- Editing a task module or the configuration after freezing makes the evidence stale; freeze again under a
  new name (evidence files are never overwritten).
- Promotion applies only with `--apply --authorize <baseline object>` for each object. Retirement keeps
  source data, baseline outputs, declared `retain` model artifacts and the evidence file.
