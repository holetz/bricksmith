# 0003. Private first, with a leak gate

- Status: accepted
- Date: 2026-10-11

## Context

The project is written with a private reference implementation at hand. Making the repository
private only protects it until the day it goes public; anything that slipped in by then is in the
history for good.

## Decision

- The repository starts private.
- Every commit and push passes `scripts/leak_check.py`, installed as `pre-commit` and `pre-push`
  hooks by `scripts/setup.sh`. It checks generic patterns built into the script and a private
  denylist of organization-specific terms, one regex per line.
- The denylist lives outside the repository (`~/.config/bricksmith/denylist.txt` or
  `$BRICKSMITH_DENYLIST_FILE`). Committing it would leak the terms it protects. Without it the
  check fails closed.
- The author email is part of the check, so a commit cannot be signed with a non-personal address.
- CI runs the generic patterns and gitleaks as a second line; the denylist never leaves the
  developer machine.
- Every distribution carries the `Private :: Do Not Upload` classifier, which PyPI rejects, until
  the project goes public.
- Going public is gated by a full-history scan with the denylist.

## Consequences

- A fresh clone cannot commit until `scripts/setup.sh` has run and the denylist exists.
- A finding is fixed by removing the content, never by weakening a pattern.
