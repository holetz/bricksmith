# The canonical router for repository operations. Entry points live in scripts/ and are listed,
# with their lifecycle, in scripts/registry.md. The library's own CLI is [project.scripts].

# List the operations.
default:
    @just --list

# One-time setup of a clone: hooks, environment and the private denylist check.
setup:
    scripts/setup.sh

# Lint, format check, type check and the generic leak gate.
check:
    uv run ruff check
    uv run ruff format --check
    uv run pyright
    python3 scripts/leak_check.py --all --generic-only

# The local test suite (remote tests skip without a workspace).
test *args:
    uv run pytest {{args}}

# The local-pyspark parity tests in an isolated classic-pyspark environment; fails on any skip.
local-spark *args:
    python3 scripts/local_spark_check.py {{args}}

# Scan for identifiers that must never reach the repository.
leak-check *args:
    python3 scripts/leak_check.py {{args}}

# Build the JVM feature kernel JAR into the package.
build-kernel *args:
    python3 scripts/build_kernel.py {{args}}

# Install the built distributions into clean environments and import every module.
verify-dist *args:
    python3 scripts/verify_dist.py {{args}}

# Time the feature engines per aggregate family.
features-benchmark *args:
    uv run python scripts/features_benchmark.py {{args}}
