#!/bin/sh
# One-time setup of a bricksmith clone: hooks, environment and the private denylist check.
set -eu
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
uv sync --locked
denylist="${BRICKSMITH_DENYLIST_FILE:-$HOME/.config/bricksmith/denylist.txt}"
if [ ! -f "$denylist" ]; then
  echo "setup: no denylist at $denylist — commits will be refused until you create one." >&2
  exit 1
fi
echo "setup: hooks active, environment synced, denylist found."
